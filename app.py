import os
import re
import json
import uuid
import threading
import time
import subprocess
import tempfile
import cv2
import numpy as np
import shutil
from fastapi import FastAPI, Form, BackgroundTasks, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
import yt_dlp

app = FastAPI()

# The SPA is served by Vercel while this API runs on a separate host, so every
# browser request is cross-origin. CORS_ALLOW_ORIGINS is a comma-separated list
# of exact origins; it defaults to localhost only, never to "*", because the API
# accepts uploads and spends the operator's bandwidth.
_allowed = os.environ.get("CORS_ALLOW_ORIGINS", "http://localhost:5173,http://localhost:8443")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _allowed.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

JOBS = {}
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Where downloaded sources and generated clips live. A container host mounts a
# volume here so artifacts survive restarts; Vercel has no writable disk outside
# /tmp, hence the tempfile fallback for that (unsupported) target.
_dl = os.environ.get("SKIMREEL_DOWNLOAD_DIR", "").strip()
if _dl:
    DOWNLOAD_DIR = _dl
elif os.environ.get("VERCEL"):
    DOWNLOAD_DIR = os.path.join(tempfile.gettempdir(), "skimreel-downloads")
else:
    DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")

# Vite builds the SPA into frontend/dist. On Vercel the CDN serves it from the
# frontend service, so this only matters for local `uvicorn app.py` runs.
WEB_DIR = os.path.join(BASE_DIR, "frontend", "dist")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
if not os.environ.get("VERCEL"):
    os.makedirs(WEB_DIR, exist_ok=True)

# Per-clip work (clip cut / preview render / transcription) must not run twice
# for the same moment when the user clicks fast, so guard each one with a lock.
ARTIFACT_LOCKS = {}
ARTIFACT_LOCKS_GUARD = threading.Lock()

WHISPER_MODELS = {}


def artifact_lock(key):
    with ARTIFACT_LOCKS_GUARD:
        if key not in ARTIFACT_LOCKS:
            ARTIFACT_LOCKS[key] = threading.Lock()
        return ARTIFACT_LOCKS[key]


def norm_quality(quality: str) -> str:
    """UI sends '1080p'; the format selectors below expect '1080'."""
    q = str(quality or "").strip().lower()
    if q.endswith("p"):
        q = q[:-1]
    return q if q in ("1080", "720", "480", "360") else "720"


def format_str_for(quality: str) -> str:
    height = norm_quality(quality)
    return f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best"


# --- yt-dlp options ---------------------------------------------------------
# YouTube answers anonymous requests from datacenter IPs with
# "Sign in to confirm you're not a bot". Cookies are the reliable fix, so both a
# Netscape cookies.txt path and a browser name are supported via the environment.

YTDLP_CLIENTS = ["tv", "web_safari", "mweb", "web", "android"]


def _curl_cffi_available():
    try:
        import curl_cffi  # noqa: F401
        return True
    except ImportError:
        return False


def base_ydl_opts() -> dict:
    opts = {
        'geo_bypass': True,
        'nocheckcertificate': True,
        'quiet': True,
        'no_warnings': True,
        'retries': 3,
        'extractor_args': {
            'youtube': {
                'player_client': YTDLP_CLIENTS,
            }
        },
    }

    cookies_file = os.environ.get("YTDLP_COOKIES_FILE", "").strip()
    if cookies_file:
        # Resolve a bare filename against the repo so the uvicorn process cwd
        # does not decide whether it resolves.
        if not os.path.isabs(cookies_file):
            cookies_file = os.path.join(BASE_DIR, cookies_file)
        cookies_file = os.path.abspath(cookies_file)
        if os.path.exists(cookies_file):
            opts['cookiefile'] = cookies_file

    cookies_from_browser = os.environ.get("YTDLP_COOKIES_FROM_BROWSER", "").strip()
    if cookies_from_browser and not opts.get("cookiefile"):
        opts['cookiesfrombrowser'] = tuple(
            part.strip() for part in cookies_from_browser.split(":")
        )

    impersonate = os.environ.get("YTDLP_IMPERSONATE", "").strip()
    if impersonate and _curl_cffi_available():
        opts['impersonate'] = impersonate

    return opts


@app.get("/api/health")
def health():
    """Liveness probe for the container host. Also reports whether yt-dlp is
    likely to pass YouTube's bot check, which is the usual reason for a
    "deployed but every link fails" report."""
    return {
        "ok": True,
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "cookies": bool(os.environ.get("YTDLP_COOKIES_FILE", "").strip()),
        "download_dir_writable": os.access(DOWNLOAD_DIR, os.W_OK),
    }


@app.post("/api/analyze")
async def analyze(
    url: str = Form(""),
    length: float = Form(30.0),
    count: int = Form(8),
    quality: str = Form("720"),
    file: UploadFile = File(None)
):
    job_id = str(uuid.uuid4())
    
    # Handle file upload
    video_path = None
    original_url = url
    if file and file.filename:
        video_path = os.path.join(DOWNLOAD_DIR, f"{job_id}_{file.filename}")
        with open(video_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        original_url = f"file://{video_path}"
    
    JOBS[job_id] = {
        "done": False,
        "status": "Queued",
        "pct": 0,
        "title": "Untitled",
        "moments": [],
        "error": None,
        "video_path": video_path,
        "video_url": None,
        "audio_url": None,
        "original_url": original_url,
        "quality": norm_quality(quality),
        "duration": 0,
        "transcripts": {}
    }
    threading.Thread(target=process_job, args=(job_id, original_url, length, count, quality), daemon=True).start()
    return {"job": job_id}

@app.get("/api/job/{job_id}")
def get_job(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(status_code=404, detail="Job not found")
    j = JOBS[job_id]
    return {
        "done": j["done"],
        "status": j["status"],
        "pct": j["pct"],
        "title": j["title"],
        "moments": j["moments"],
        "error": j["error"],
        "duration": j.get("duration", 0),
        "transcripts": j.get("transcripts", {})
    }

def moment_of(job_id: str, idx: int):
    if job_id not in JOBS or not JOBS[job_id]["done"]:
        raise HTTPException(status_code=404, detail="Job not finished or not found")
    moments = JOBS[job_id]["moments"]
    if idx < 0 or idx >= len(moments):
        raise HTTPException(status_code=404, detail="Moment index out of range")
    return moments[idx]


def ensure_source(job_id: str) -> str:
    """Return a local path to the full source video, downloading it if needed."""
    j = JOBS[job_id]
    video_path = j.get("video_path")
    if video_path and os.path.exists(video_path):
        return video_path

    original_url = j.get("original_url") or ""
    if original_url.startswith("file://"):
        raise HTTPException(status_code=404, detail="Source video file not found")

    video_path = os.path.join(DOWNLOAD_DIR, f"{job_id}_source.mp4")
    if not os.path.exists(video_path):
        ydl_opts = base_ydl_opts()
        ydl_opts.update({
            'format': format_str_for(j.get("quality", "720")),
            'outtmpl': video_path,
            'merge_output_format': 'mp4',
        })
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([original_url])

    if not os.path.exists(video_path):
        found = None
        for f in os.listdir(DOWNLOAD_DIR):
            if f.startswith(job_id) and f.endswith(('.mp4', '.mkv', '.webm', '.mov', '.ts')):
                found = os.path.join(DOWNLOAD_DIR, f)
                break
        if found:
            video_path = found

    if not os.path.exists(video_path):
        raise HTTPException(status_code=404, detail="Failed to download source video")

    j["video_path"] = video_path
    return video_path


def ffmpeg_run(cmd):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


@app.get("/api/clip/{job_id}/{idx}")
def get_clip(job_id: str, idx: int):
    m = moment_of(job_id, idx)
    video_path = ensure_source(job_id)

    out_clip = os.path.join(DOWNLOAD_DIR, f"{job_id}_clip_{idx}.mp4")
    if not os.path.exists(out_clip):
        with artifact_lock(f"clip:{job_id}:{idx}"):
            if not os.path.exists(out_clip):
                ffmpeg_run([
                    "ffmpeg", "-y", "-ss", str(m["start"]), "-i", video_path,
                    "-t", str(m["end"] - m["start"]), "-c", "copy",
                    "-movflags", "+faststart", out_clip
                ])

    if os.path.exists(out_clip) and os.path.getsize(out_clip) > 0:
        return FileResponse(out_clip, media_type="video/mp4", filename=f"clip_{idx + 1}.mp4")
    raise HTTPException(status_code=500, detail="Failed to generate clip")


@app.get("/api/preview/{job_id}/{idx}")
def get_preview(job_id: str, idx: int):
    """Web-optimised re-encode of a moment so the browser can scrub it instantly."""
    m = moment_of(job_id, idx)
    video_path = ensure_source(job_id)

    out_preview = os.path.join(DOWNLOAD_DIR, f"{job_id}_preview_{idx}.mp4")
    if not os.path.exists(out_preview) or os.path.getsize(out_preview) == 0:
        with artifact_lock(f"preview:{job_id}:{idx}"):
            if not os.path.exists(out_preview) or os.path.getsize(out_preview) == 0:
                ffmpeg_run([
                    "ffmpeg", "-y", "-ss", str(m["start"]), "-i", video_path,
                    "-t", str(m["end"] - m["start"]),
                    "-vf", "scale='min(854,iw)':-2",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                    "-c:a", "aac", "-b:a", "96k", out_preview
                ])

    if os.path.exists(out_preview) and os.path.getsize(out_preview) > 0:
        return FileResponse(out_preview, media_type="video/mp4")
    raise HTTPException(status_code=500, detail="Failed to generate preview")


def _parse_ts(value: str) -> float:
    parts = value.strip().replace(",", ".").split(":")
    try:
        parts = [float(p) for p in parts]
    except ValueError:
        return 0.0
    seconds = 0.0
    for p in parts:
        seconds = seconds * 60 + p
    return seconds


def _parse_vtt(raw: str):
    cues = []
    for block in re.split(r"\n\s*\n", raw.replace("\r\n", "\n").replace("\r", "\n")):
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        if not lines or lines[0].startswith(("WEBVTT", "NOTE", "STYLE", "Kind:", "Language:")):
            continue
        ti = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if ti is None:
            continue
        match = re.match(r"([\d:.,]+)\s*-->\s*([\d:.,]+)", lines[ti])
        if not match:
            continue
        text = " ".join(lines[ti + 1:])
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            cues.append((_parse_ts(match.group(1)), _parse_ts(match.group(2)), text))
    # YouTube auto-captions repeat the previous line; collapse consecutive dupes.
    deduped = []
    for start, end, text in cues:
        if deduped and deduped[-1][2] == text:
            deduped[-1] = (deduped[-1][0], end, text)
        else:
            deduped.append((start, end, text))
    return deduped


def _parse_json3(raw: str):
    data = json.loads(raw)
    cues = []
    for event in data.get("events", []):
        segs = event.get("segs")
        if not segs:
            continue
        text = re.sub(r"\s+", " ", "".join(s.get("utf8", "") for s in segs)).strip()
        if not text:
            continue
        start = event.get("tStartMs", 0) / 1000.0
        cues.append((start, start + event.get("dDurationMs", 0) / 1000.0, text))
    deduped = []
    for start, end, text in cues:
        if deduped and deduped[-1][2] == text:
            deduped[-1] = (deduped[-1][0], end, text)
        else:
            deduped.append((start, end, text))
    return deduped


def _captions_from_source(job_id: str, start: float, end: float):
    """Free fallback: reuse captions the source already ships with (YouTube)."""
    import requests

    original_url = JOBS[job_id].get("original_url") or ""
    if original_url.startswith("file://"):
        return None

    ydl_opts = base_ydl_opts()
    ydl_opts['skip_download'] = True
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(original_url, download=False)
    except Exception:
        return None

    tracks = info.get("subtitles") or {}
    preferred = [k for k in ("en", "en-US", "en-GB") if k in tracks]
    pool = {k: v for k, v in preferred} or (dict(list(tracks.items())[:1]) if tracks else {})
    if not pool:
        auto = info.get("automatic_captions") or {}
        for key in ("en", "en-orig", "en-US", "a.en"):
            if key in auto:
                pool = {key: auto[key]}
                break
        else:
            if auto:
                pool = {list(auto.keys())[0]: auto[list(auto.keys())[0]]}
    if not pool:
        return None

    formats = list(pool.values())[0]
    pick = next((f for f in formats if f.get("ext") == "json3"), None) \
        or next((f for f in formats if f.get("ext") == "vtt"), None)
    if not pick:
        return None

    try:
        res = requests.get(pick["url"], timeout=20, verify=False)
        res.raise_for_status()
    except Exception:
        return None

    try:
        cues = _parse_json3(res.text) if pick.get("ext") == "json3" else _parse_vtt(res.text)
    except Exception:
        return None

    windowed = []
    for c_start, c_end, text in cues:
        if c_end <= start or c_start >= end:
            continue
        windowed.append({"start": max(0.0, c_start - start),
                         "end": min(end - start, c_end - start),
                         "text": text})
    if not windowed:
        return None
    return {"text": " ".join(c["text"] for c in windowed), "segments": windowed, "engine": "captions"}


def _whisper_segments(job_id: str, wav_path: str, offset: float, length: float):
    """Local speech-to-text. Prefers faster-whisper, falls back to openai-whisper."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        try:
            import whisper
        except ImportError:
            return None
        if "whisper" not in WHISPER_MODELS:
            WHISPER_MODELS["whisper"] = whisper.load_model("base")
        result = WHISPER_MODELS["whisper"].transcribe(wav_path, fp16=False)
        segments = [{"start": s["start"], "end": s["end"], "text": s["text"].strip()}
                    for s in result.get("segments", [])]
        return {"text": result.get("text", "").strip(), "segments": segments, "engine": "whisper"}

    if "faster-whisper" not in WHISPER_MODELS:
        WHISPER_MODELS["faster-whisper"] = WhisperModel("base", device="cpu", compute_type="int8")
    segments_iter, _info = WHISPER_MODELS["faster-whisper"].transcribe(
        wav_path, beam_size=1, vad_filter=True
    )
    segments = [{"start": s.start, "end": s.end, "text": s.text.strip()}
                for s in segments_iter if s.text and s.text.strip()]
    return {"text": " ".join(s["text"] for s in segments), "segments": segments, "engine": "faster-whisper"}


def _format_srt_time(seconds: float) -> str:
    ms = max(0, int(round(seconds * 1000)))
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    ms %= 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _segments_to_srt(segments) -> str:
    blocks = []
    for i, seg in enumerate(segments, start=1):
        s_ts = _format_srt_time(seg.get("start", 0.0))
        e_ts = _format_srt_time(seg.get("end", 0.0))
        txt = str(seg.get("text", "")).strip()
        blocks.append(f"{i}\n{s_ts} --> {e_ts}\n{txt}")
    return "\n\n".join(blocks) + "\n"


@app.post("/api/transcribe/{job_id}/{idx}")
def post_transcribe(job_id: str, idx: int):
    m = moment_of(job_id, idx)
    cached = JOBS[job_id].get("transcripts", {}).get(str(idx))
    if cached:
        return cached

    with artifact_lock(f"transcribe:{job_id}:{idx}"):
        cached = JOBS[job_id].get("transcripts", {}).get(str(idx))
        if cached:
            return cached

        start, end = float(m["start"]), float(m["end"])
        length = max(0.1, end - start)
        video_path = ensure_source(job_id)

        with tempfile.TemporaryDirectory() as tmp:
            wav_path = os.path.join(tmp, "segment.wav")
            ffmpeg_run([
                "ffmpeg", "-y", "-ss", str(start), "-i", video_path, "-t", str(length),
                "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav_path
            ])
            if not os.path.exists(wav_path):
                raise HTTPException(status_code=422, detail="This clip has no audio track to transcribe.")

            try:
                result = _whisper_segments(job_id, wav_path, start, length)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Transcription failed: {e}")

        if not result or not result["segments"]:
            try:
                result = _captions_from_source(job_id, start, end)
            except Exception:
                result = None

        if not result:
            raise HTTPException(
                status_code=503,
                detail=(
                    "No transcription engine available. Install one with: "
                    "pip install -r requirements-transcribe.txt"
                )
            )

        for seg in result["segments"]:
            seg["start"] = max(0.0, seg["start"])
            seg["end"] = min(length, seg["end"])
        if not result["text"]:
            result["text"] = " ".join(s["text"] for s in result["segments"])

        payload = {
            "idx": idx,
            "text": result["text"],
            "segments": result["segments"],
            "srt": _segments_to_srt(result["segments"]),
            "engine": result["engine"],
        }
        JOBS[job_id].setdefault("transcripts", {})[str(idx)] = payload
        return payload


@app.get("/api/transcript/{job_id}/{idx}")
def get_transcript_srt(job_id: str, idx: int):
    payload = JOBS[job_id].get("transcripts", {}).get(str(idx)) if job_id in JOBS else None
    if not payload:
        raise HTTPException(status_code=404, detail="No transcript yet. Run Transcribe first.")
    srt_name = f"skimreel_{idx + 1}.srt"
    return Response(
        content=payload["srt"],
        media_type="application/x-subrip",
        headers={"Content-Disposition": f'attachment; filename="{srt_name}"'},
    )


def fmt_time(seconds):
    s = max(0, int(round(seconds)))
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"

def get_stream_urls(url: str, quality: str):
    ydl_opts = base_ydl_opts()
    ydl_opts.update({
        'format': format_str_for(quality),
        'skip_download': True,
    })
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        title = info.get('title', 'Video')
        duration = info.get('duration', 0)
        
        video_url = None
        audio_url = None
        
        if 'url' in info and info.get('vcodec') != 'none':
            video_url = info['url']
        elif 'formats' in info:
            for f in info['formats']:
                if f.get('vcodec') != 'none' and f.get('acodec') == 'none' and f.get('url'):
                    video_url = f['url']
                    break
            for f in info['formats']:
                if f.get('acodec') != 'none' and f.get('vcodec') == 'none' and f.get('url'):
                    audio_url = f['url']
                    break
        
        if not video_url and 'url' in info:
            video_url = info['url']
            
        return video_url, audio_url, title, duration

def analyze_stream(video_url: str, duration: float, clip_length: float, count: int, job_id: str):
    j = JOBS[job_id]
    
    if duration <= 0:
        duration = 10.0
        
    N = max(10, min(200, int(duration / 5)))
    step = duration / N
    times = [(i + 0.5) * step for i in range(N)]
    
    cap = cv2.VideoCapture(video_url)
    if not cap.isOpened():
        raise Exception("Could not open video stream for analysis")
    
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    
    motion_scores = []
    for i, t in enumerate(times):
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ret1, frame1 = cap.read()
        if not ret1:
            motion_scores.append(0.0)
            continue
        
        cap.set(cv2.CAP_PROP_POS_MSEC, min(duration * 1000, (t + 0.7) * 1000))
        ret2, frame2 = cap.read()
        if not ret2:
            frame2 = frame1
            
        f1 = cv2.resize(cv2.cvtColor(frame1, cv2.COLOR_BGR2GRAY), (64, 36))
        f2 = cv2.resize(cv2.cvtColor(frame2, cv2.COLOR_BGR2GRAY), (64, 36))
        diff = np.mean(np.abs(f1.astype(float) - f2.astype(float)) / 255.0)
        motion_scores.append(float(diff))
        
        pct = 10 + int(70 * (i + 1) / N)
        j["pct"] = pct
        j["status"] = f"Analyzing... {i+1}/{N}"
    
    cap.release()
    
    if motion_scores:
        max_m = max(motion_scores) or 1.0
        norm_m = [m / max_m for m in motion_scores]
    else:
        norm_m = [0.0] * len(times)
        
    smoothed = []
    for i in range(len(norm_m)):
        prev_val = norm_m[i-1] if i > 0 else norm_m[i]
        next_val = norm_m[i+1] if i < len(norm_m)-1 else norm_m[i]
        smoothed.append(prev_val * 0.25 + norm_m[i] * 0.5 + next_val * 0.25)
    
    indices = sorted(range(len(smoothed)), key=lambda i: smoothed[i], reverse=True)
    pick = []
    for i in indices:
        if len(pick) >= count:
            break
        if all(abs(times[i] - times[j_idx]) >= clip_length for j_idx in pick):
            pick.append(i)
            
    moments = []
    for i in pick:
        t = times[i]
        s = max(0.0, min(t - clip_length / 2, duration - clip_length))
        e = min(duration, s + clip_length)
        score = int(round(smoothed[i] * 100))
        why = "fast action or high motion" if smoothed[i] > 0.4 else "standout energy"
        moments.append({
            "start": float(s),
            "end": float(e),
            "range": f"{fmt_time(s)} – {fmt_time(e)}",
            "score": score,
            "why": why,
            "peak": float(t),
            "transcript": ""
        })
    
    moments.sort(key=lambda m: m["score"], reverse=True)
    return moments

def process_job(job_id: str, url: str, clip_length: float, count: int, quality: str = "720"):
    j = JOBS[job_id]
    try:
        is_local_file = url.startswith("file://")
        
        if is_local_file:
            j["status"] = "Analyzing local file..."
            j["pct"] = 10
            video_path = url.replace("file://", "")
            if not os.path.exists(video_path):
                raise Exception("Local video file not found")
            
            cap = cv2.VideoCapture(video_path)
            fps = cap.get(cv2.CAP_PROP_FPS) or 30
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
            duration = total_frames / fps
            cap.release()
            
            j["video_path"] = video_path
            j["title"] = os.path.basename(video_path)
            j["duration"] = duration
        else:
            j["status"] = "Getting stream URLs..."
            j["pct"] = 5
            
            video_url, audio_url, title, duration = get_stream_urls(url, quality)
            
            if not video_url:
                raise Exception("Could not extract video stream URL")
            
            j["video_url"] = video_url
            j["audio_url"] = audio_url
            j["title"] = title
            j["duration"] = duration
        
        if duration <= 0:
            duration = 10.0
            
        j["status"] = "Analyzing video..."
        j["pct"] = 10
        
        if is_local_file:
            video_url = j["video_path"]
        else:
            video_url = j["video_url"]
        
        moments = analyze_stream(video_url, duration, clip_length, count, job_id)
        
        j["moments"] = moments
        j["done"] = True
        j["status"] = "Analysis complete. Clips will download on demand."
        j["pct"] = 100
        
    except Exception as e:
        j["done"] = True
        j["error"] = str(e)
        j["status"] = "Error"

# Serve the React build for local runs (must be after all API routes).
# Skipped on Vercel: the frontend service owns static assets there.
if not os.environ.get("VERCEL") and os.path.isdir(WEB_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(WEB_DIR, "assets")), name="assets")
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")