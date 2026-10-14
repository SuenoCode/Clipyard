import os
import re
import json
import uuid
import hashlib
import threading
import time
import subprocess
import tempfile
import cv2
import numpy as np
import shutil
import sys
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

# Cloud sync clients hold freshly written files open while they upload, which makes
# yt-dlp's final rename fail with WinError 32 on Windows. The download then dies
# before merging, leaving per-format fragments behind, and a clip cut from one of
# those comes out silent - while the request still returns 200. Detected at boot so
# the cause is named before a user waits on it.
SYNC_DIR_MARKERS = (
    ("OneDrive", "OneDrive"),
    ("Dropbox", "Dropbox"),
    ("Google Drive", "Google Drive"),
    ("GoogleDrive", "Google Drive"),
    ("iCloud", "iCloud"),
)


def _sync_provider(path: str):
    """Name the sync service owning this path, or None if it is not synced.

    OneDrive exposes its root as an environment variable, so check that first.
    The rest fall back to a substring test, which is less precise but catches the
    common 'Dropbox' or 'Google Drive' folder layouts.
    """
    try:
        resolved = os.path.abspath(path)
    except Exception:
        return None

    root = os.environ.get("OneDrive", "").strip()
    if root and os.path.abspath(root) in os.path.normcase(resolved):
        return "OneDrive"

    lowered = os.path.normcase(resolved)
    for marker, name in SYNC_DIR_MARKERS:
        if os.path.normcase(marker) in lowered:
            return name
    return None


SYNC_PROVIDER = _sync_provider(DOWNLOAD_DIR)
if SYNC_PROVIDER:
    print(
        f"[skimreel] WARNING: the downloads directory is inside a {SYNC_PROVIDER}"
        f"-synced folder ({DOWNLOAD_DIR}). The sync client locks files while it"
        " uploads them, so yt-dlp's final rename fails with WinError 32, the"
        " download never merges, and clips come out with no audio track. Set"
        " SKIMREEL_DOWNLOAD_DIR to a local path outside the sync root.",
        file=sys.stderr,
    )

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


def sweep_stale_artifacts() -> None:
    """Delete artifacts left behind by a previous process, at boot.

    DELETE /api/job/{id} only knows about jobs this process started: JOBS lives
    in memory, so a deploy, crash or restart orphans every source and preview
    already on the volume, and nothing would ever release them. This is the
    backstop for that case.

    Age, not job id, is the test, because an orphan cannot be distinguished from
    a live job by anything on disk. The window is long by default so a restart
    during a long download does not pull the source out from under it, and 0
    turns the sweep off.
    """
    try:
        ttl_hours = float(os.environ.get("SKIMREEL_ARTIFACT_TTL_HOURS", "24"))
    except ValueError:
        ttl_hours = 24.0
    if ttl_hours <= 0:
        return

    cutoff = time.time() - ttl_hours * 3600
    try:
        names = os.listdir(DOWNLOAD_DIR)
    except OSError:
        return

    removed = 0
    freed = 0
    for name in names:
        path = os.path.join(DOWNLOAD_DIR, name)
        try:
            if not os.path.isfile(path) or os.path.getmtime(path) > cutoff:
                continue
            freed += os.path.getsize(path)
            os.remove(path)
            removed += 1
        except OSError:
            continue

    if removed:
        print(
            f"[skimreel] startup sweep removed {removed} stale artifact(s), "
            f"{freed / (1024 * 1024):.0f} MB, older than {ttl_hours:g}h",
            file=sys.stderr,
        )


sweep_stale_artifacts()


def norm_quality(quality: str) -> str:
    """UI sends '1080p'; the format selectors below expect '1080'."""
    q = str(quality or "").strip().lower()
    if q.endswith("p"):
        q = q[:-1]
    return q if q in ("1080", "720", "480", "360") else "720"


def format_str_for(quality: str) -> str:
    """Build a yt-dlp selector that will not silently hand back 360p.

    The old string ended in a bare `/best`, and yt-dlp's "best" means the best
    *pre-merged* file, which on YouTube is format 18 - 640x360, unchanged since
    2010. Every clause here is height filtered, so a bare `/best` can never be
    reached.

    Within the ceiling the best available stream is chosen deliberately: if a
    source only publishes 720p, asking for 1080p yields 720p rather than an error,
    because that is the source's ceiling and not a fault in the request. The
    resolution actually obtained is probed from the downloaded file and reported
    in the job status, so a caller is never left guessing.
    """
    height = norm_quality(quality)
    return (
        f"bestvideo[height<={height}]+bestaudio/"
        f"best[height<={height}]/"
        f"bestvideo[height<={height}]"
    )


# --- yt-dlp options ---------------------------------------------------------
# YouTube answers anonymous requests from datacenter IPs with
# "Sign in to confirm you're not a bot". Cookies are the reliable fix, so both a
# Netscape cookies.txt path and a browser name are supported via the environment.

# player_client is deliberately NOT pinned.
#
# An earlier version hardcoded ["tv", "web_safari", "mweb", "web", "android"] and
# that list was the cause of the 640x360 output: every client in it was either
# dead or capped, so yt-dlp quietly fell through to format 18 (the 360p
# pre-merged stream). Pinning is worse than useless here because the set of
# clients that work changes with YouTube and with the yt-dlp version, and a stale
# pin silently costs resolution instead of erroring.
#
# Measured on yt-dlp 2026.08.19 with Node 24 available for nsig extraction:
#   default clients                 1920x1080 available  <- what we now use
#   player_client=web               no formats (PO token gated)
#   player_client=mweb              no formats (PO token gated)
# So the defaults are both the simplest and the highest quality; the only reason
# to pin would be a format check that showed defaults failing, which it does not.


def _curl_cffi_available():
    try:
        import curl_cffi  # noqa: F401
        return True
    except ImportError:
        return False


def _write_inline_cookies(text: str) -> str:
    """Materialise YTDLP_COOKIES into a file yt-dlp can read.

    yt-dlp only accepts a cookie *path*. The filename embeds a hash of the
    contents so a rotated secret never serves a stale file, and it is rewritten
    whenever the hash changes. Permissions are owner-only because this is a live
    session for a real account.
    """
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    path = os.path.join(tempfile.gettempdir(), f"skimreel-cookies-{digest}.txt")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text if text.endswith("\n") else text + "\n")
    except OSError:
        # A read-only /tmp would make transcription unavailable, but analysis and
        # clip cutting should still work, so fall back to no cookies.
        return ""
    return path


def base_ydl_opts() -> dict:
    opts = {
        'geo_bypass': True,
        'nocheckcertificate': True,
        'quiet': True,
        'no_warnings': True,
        'retries': 3,
        # No player_client / extractor_args here on purpose: pinning the client was
        # what forced the 640x360 output. Letting yt-dlp pick its own defaults is
        # both simpler and higher quality, and it means a yt-dlp upgrade can fix
        # client selection without touching this file.
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

    # Container platforms pass secrets as env vars, and shipping a cookie file
    # in the image would bake a live account session into it. YTDLP_COOKIES wins
    # only when no readable cookie file was found, so a file on the volume stays
    # authoritative.
    cookies_inline = os.environ.get("YTDLP_COOKIES", "").strip()
    if cookies_inline and not opts.get("cookiefile"):
        opts['cookiefile'] = _write_inline_cookies(cookies_inline)

    impersonate = os.environ.get("YTDLP_IMPERSONATE", "").strip()
    if impersonate and _curl_cffi_available():
        opts['impersonate'] = impersonate

    return opts


SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")
VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpg", ".mpeg", ".wmv", ".flv")


def safe_upload_name(filename: str) -> str:
    """Reduce a client-supplied filename to a safe, unique-on-disk basename.

    Keeps the extension (ffmpeg and OpenCV both sniff by it) while stripping
    directory components and anything the host filesystem may reject.
    """
    base = os.path.basename(str(filename or "").replace("\\", "/"))
    base = SAFE_NAME_RE.sub("_", base).strip("._") or "upload"
    stem, ext = os.path.splitext(base)
    if ext.lower() not in VIDEO_EXTS:
        ext = ".mp4"
    return f"{stem[:80]}{ext}"


def probe_duration(path: str) -> float:
    """Container duration in seconds.

    OpenCV reports 0 (or a wildly wrong value) for some uploads, which silently
    skews every clip offset, so ask ffprobe first and fall back to OpenCV.
    """
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
        )
        val = float(res.stdout.decode("utf-8", "ignore").strip())
        if val > 0:
            return val
    except Exception:
        pass

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return 0.0
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    cap.release()
    if fps > 0 and frames > 0:
        return float(frames) / float(fps)
    return 0.0


def _has_playable_video(path: str) -> bool:
    """True if the file has a video stream that is complete enough to cut from.

    Used to reject a source recovered by the filename glob in ensure_source,
    which by definition has not been through this code path and may be a
    fragment or a truncated download.

    Three checks, because neither one alone is sufficient. Duration is useless on
    its own: an interrupted faststart MP4 still carries a full moov atom in its
    first few megabytes, so ffprobe cheerfully reports the complete runtime of a
    file that is 1.7% downloaded. The size check is what actually catches that.
    """
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return False

    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_type,duration,bit_rate",
             "-of", "csv=p=0", path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
        )
    except Exception:
        return False

    fields = res.stdout.decode("utf-8", "ignore").strip().split(",")
    if not fields or fields[0] != "video":
        return False

    try:
        duration = float(fields[1]) if len(fields) > 1 and fields[1] else 0.0
        bit_rate = int(fields[2]) if len(fields) > 2 and fields[2] else 0
    except ValueError:
        return False

    if duration <= 0:
        return False

    # A truncated file is far smaller than duration x bitrate. The tolerance is
    # generous because container overhead and VBR make the estimate rough in both
    # directions; a genuinely complete file measures well above 1.0.
    if bit_rate > 0:
        expected_bytes = duration * bit_rate / 8
        if expected_bytes > 0 and os.path.getsize(path) < expected_bytes * 0.9:
            return False

    return True


def open_capture(path: str):
    """Open a video for frame analysis, transcoding as a last resort.

    Returns (capture, cleanup). `cleanup` removes any temporary proxy file and
    must be called by the caller. OpenCV ships without every codec, so an upload
    it cannot decode is re-encoded through ffmpeg before we give up.
    """
    cap = cv2.VideoCapture(path)
    if cap.isOpened():
        return cap, (lambda: None)

    cap.release()
    tmp_dir = tempfile.mkdtemp(prefix="skimreel-proxy-")
    proxy = os.path.join(tmp_dir, "proxy.mp4")

    def cleanup():
        shutil.rmtree(tmp_dir, ignore_errors=True)

    ffmpeg_run([
        "ffmpeg", "-y", "-v", "error", "-i", path,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-pix_fmt", "yuv420p", "-c:a", "aac", proxy,
    ])

    if not os.path.exists(proxy) or os.path.getsize(proxy) == 0:
        cleanup()
        raise Exception(
            "Could not read this video. It may be corrupt or use a codec "
            "neither OpenCV nor ffmpeg can decode."
        )

    cap = cv2.VideoCapture(proxy)
    if not cap.isOpened():
        cap.release()
        cleanup()
        raise Exception("Could not read this video. Try re-exporting it as H.264 MP4.")
    return cap, cleanup


def _cookies_configured() -> bool:
    """True if yt-dlp will be given cookies in any of the three supported forms.

    base_ydl_opts() accepts a cookie file, inline text, or a local browser, so
    health must consider all three - checking only the file form reported
    "cookies": false on installs that were working fine.
    """
    if os.environ.get("YTDLP_COOKIES_FILE", "").strip():
        return True
    if os.environ.get("YTDLP_COOKIES", "").strip():
        return True
    return bool(os.environ.get("YTDLP_COOKIES_FROM_BROWSER", "").strip())


@app.get("/api/health")
def health():
    """Liveness probe for the container host. Also reports whether yt-dlp is
    likely to pass YouTube's bot check, which is the usual reason for a
    "deployed but every link fails" report."""
    return {
        "ok": True,
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "cookies": _cookies_configured(),
        "download_dir_writable": os.access(DOWNLOAD_DIR, os.W_OK),
        "download_dir_synced": SYNC_PROVIDER,
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
    display_title = "Untitled"
    if file and file.filename:
        # The browser-supplied name is untrusted: it may carry separators or
        # characters the filesystem rejects, and it must never escape DOWNLOAD_DIR.
        safe_name = safe_upload_name(file.filename)
        video_path = os.path.join(DOWNLOAD_DIR, f"{job_id}_{safe_name}")
        with open(video_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer, length=1024 * 1024)
        original_url = f"file://{video_path}"
        # Show the name the user recognises, not the prefixed one on disk.
        display_title = os.path.basename(str(file.filename).replace("\\", "/")) or safe_name
    
    JOBS[job_id] = {
        "done": False,
        "status": "Queued",
        "pct": 0,
        "title": display_title,
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
        "source_height": j.get("source_height", 0),
        "requested_quality": j.get("quality", ""),
        "transcripts": j.get("transcripts", {})
    }

@app.delete("/api/job/{job_id}")
def delete_job(job_id: str):
    """Release a finished job's disk artifacts.

    The client calls this when the user starts a new analysis, which is the point
    at which the previous results are no longer being used. A 1080p source alone
    runs to hundreds of megabytes and every preview and clip adds to it, so a
    handful of analyses fills the volume the container mounts.

    Artifacts are matched on the job-id prefix every one of them is named with,
    which also catches yt-dlp's per-format fragments and .part files that an
    interrupted download leaves behind - those are never served but they are
    just as large.

    Only a finished job can be released: a job still running holds a download or
    an encode open, and deleting its source under a live ffmpeg fails the request
    instead of reclaiming anything. 409 keeps that visible rather than silently
    leaking the disk.
    """
    j = JOBS.get(job_id)
    if j is None:
        # Already released, or the process restarted and lost it. Report success
        # so the client never blocks a new analysis on cleanup it cannot undo.
        return {"released": True, "removed": 0}
    if not j.get("done"):
        raise HTTPException(status_code=409, detail="Job is still running")

    removed = 0
    try:
        names = os.listdir(DOWNLOAD_DIR)
    except OSError:
        names = []
    for name in names:
        # Job ids are uuids, so no other artifact's name can be a longer id's
        # prefix - this cannot delete an unrelated file.
        if not name.startswith(job_id):
            continue
        try:
            os.remove(os.path.join(DOWNLOAD_DIR, name))
            removed += 1
        except OSError:
            # A file still locked by a cloud-sync client is left for the next
            # sweep rather than failing the release.
            pass

    JOBS.pop(job_id, None)
    return {"released": True, "removed": removed}


def moment_of(job_id: str, idx: int):
    if job_id not in JOBS or not JOBS[job_id]["done"]:
        raise HTTPException(status_code=404, detail="Job not finished or not found")
    moments = JOBS[job_id]["moments"]
    if idx < 0 or idx >= len(moments):
        raise HTTPException(status_code=404, detail="Moment index out of range")
    return moments[idx]


def _fetch_source(job_id: str, original_url: str, video_path: str):
    """Download the source video with yt-dlp's own downloader.

    This has to be a real download to disk, not a stream URL handed to ffmpeg.
    The googlevideo URLs yt-dlp resolves are signed and expect the headers yt-dlp
    itself sends, so a bare `ffmpeg -i <url>` is answered with
    "Server returned 403 Forbidden". Downloading first also means the analysis pass
    and every later clip, preview and transcript read one local file instead of
    re-resolving the source.
    """
    quality = JOBS[job_id].get("quality", "720")
    ydl_opts = base_ydl_opts()
    ydl_opts.update({
        'format': format_str_for(quality),
        'outtmpl': video_path,
        'merge_output_format': 'mp4',
    })
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(original_url, download=True)
    except yt_dlp.utils.DownloadError as e:
        for leftover in (video_path + ".part", video_path):
            if os.path.exists(leftover):
                try:
                    os.remove(leftover)
                except OSError:
                    pass
        raise HTTPException(
            status_code=502,
            detail=(
                f"Could not download the source video: {e}. "
                "If this is a YouTube link, the usual cause is the bot check - "
                "close the browser holding the cookie database, then set "
                "YTDLP_COOKIES_FROM_BROWSER (e.g. chrome) or YTDLP_COOKIES and "
                "restart the API. Check /api/health to see whether cookies are "
                "being picked up."
            ),
        ) from e


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
        # Serialised, and re-checked inside the lock: two requests for the same
        # job would otherwise run yt-dlp concurrently against one output path,
        # collide on the .part file, and fail the loser's rename with WinError 32
        # on Windows while the winner's download sits complete but unrenamed.
        with artifact_lock(f"source:{job_id}"):
            if not os.path.exists(video_path):
                info = _fetch_source(job_id, original_url, video_path)
                # Record the real title and duration while the metadata is in hand.
                # Downloading the source here means process_job no longer has a
                # separate extract to call, so without this the UI would show the
                # on-disk filename, which is just the job id.
                if isinstance(info, dict):
                    if info.get("title") and j.get("title") in (None, "", "Untitled"):
                        j["title"] = info["title"]
                    if info.get("duration"):
                        j["duration"] = float(info["duration"])

    if not os.path.exists(video_path):
        found = None
        for f in os.listdir(DOWNLOAD_DIR):
            # Generated artifacts share the job-id prefix, so a preview or clip
            # could otherwise be picked up here and handed to the cutter as if it
            # were the source.
            if "_preview" in f or "_clip_" in f:
                continue
            # yt-dlp downloads each format as its own fragment and merges
            # afterwards, so a download that dies before the merge leaves these
            # behind: {job}_source.f137.mp4 is the video-only stream and
            # {job}_source.f251.webm is the audio-only one. They are named with a
            # real video extension, so the suffix test below does not catch them.
            # Handing f137 to the cutter yields a 200 and a completely silent clip,
            # which is the worst possible outcome. yt-dlp removes them on success,
            # so their presence means the download did not complete.
            if re.search(r"\.f\d{3}\.", f) or f.endswith(".part"):
                continue
            if f.startswith(job_id) and f.endswith(('.mp4', '.mkv', '.webm', '.mov', '.ts')):
                found = os.path.join(DOWNLOAD_DIR, f)
                break
        if found:
            video_path = found

    if not os.path.exists(video_path):
        raise HTTPException(status_code=404, detail="Failed to download source video")

    # Anything recovered by the glob is unverified, so probe it before the clip
    # cutter trusts it. A truncated or wrong-stream file that decodes to something
    # still produces a 200 with broken output; failing here names the real problem.
    # Audio is deliberately not required - a genuinely silent source is valid - but
    # the audio-only fragment is already excluded by the video-stream check.
    if not _has_playable_video(video_path):
        raise HTTPException(
            status_code=502,
            detail=(
                "The downloaded source file is not a usable video (no video "
                "stream, or it is truncated). This usually means the download was "
                "interrupted - commonly because the downloads folder is inside a "
                "cloud-synced location like OneDrive, which locks freshly written "
                "files and makes yt-dlp's rename fail. Set SKIMREEL_DOWNLOAD_DIR "
                "to a local path outside OneDrive and retry."
            ),
        )

    # Report the resolution actually obtained. Format selection is a ceiling, so a
    # 1080p request against a 360p source legitimately yields 360p; surfacing the
    # real height keeps that from looking like a silent downgrade.
    if not j.get("source_height"):
        cap = cv2.VideoCapture(video_path)
        if cap.isOpened():
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            cap.release()
            if h:
                j["source_height"] = h

    j["video_path"] = video_path
    return video_path


def ffmpeg_run(cmd):
    """Run ffmpeg and fail loudly.

    No call site inspects the result, so without a check here a missing binary
    surfaces as a raw FileNotFoundError traceback and a failed encode surfaces as
    a misleading "Failed to generate clip" with ffmpeg's stderr discarded.
    """
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        raise RuntimeError(
            "ffmpeg was not found on PATH. It is required for preview, "
            "Download MP4 and transcription - install it and restart the API."
        )
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-4:]
        raise RuntimeError("ffmpeg failed: " + " ".join(tail))
    return proc


def encode_to(args, out_path):
    """Publish an ffmpeg output atomically, failing with an actionable 503.

    ffmpeg writes incrementally, so an interrupted run leaves a non-empty partial
    file. Both callers treat "exists and non-empty" as a cache hit, so that
    truncation would be served permanently - encode to a temp name and rename only
    after ffmpeg reports success.

    The temp name keeps the real extension: ffmpeg picks its muxer from it, so
    `foo.mp4.part` fails to initialise with "Error opening output file".
    """
    root, ext = os.path.splitext(out_path)
    tmp = f"{root}.part{ext}"
    try:
        ffmpeg_run(["ffmpeg", *args, tmp])
        os.replace(tmp, out_path)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


@app.get("/api/clip/{job_id}/{idx}")
def get_clip(job_id: str, idx: int):
    m = moment_of(job_id, idx)
    video_path = ensure_source(job_id)

    out_clip = os.path.join(DOWNLOAD_DIR, f"{job_id}_clip_{idx}.mp4")
    if not os.path.exists(out_clip):
        with artifact_lock(f"clip:{job_id}:{idx}"):
            if not os.path.exists(out_clip):
                encode_to([
                    "-y", "-ss", str(m["start"]), "-i", video_path,
                    "-t", str(m["end"] - m["start"]), "-c", "copy",
                    "-movflags", "+faststart",
                ], out_clip)

    if os.path.exists(out_clip) and os.path.getsize(out_clip) > 0:
        return FileResponse(out_clip, media_type="video/mp4", filename=f"clip_{idx + 1}.mp4")
    raise HTTPException(status_code=500, detail="Failed to generate clip")


# Bumped whenever the preview encode settings change, so every machine with a warm
# cache picks up the new encode instead of serving stale files forever.
PREVIEW_CACHE_VERSION = 2


@app.get("/api/preview/{job_id}/{idx}")
def get_preview(job_id: str, idx: int):
    """Web-optimised re-encode of a moment so the browser can scrub it instantly.

    Re-encoded rather than stream-copied because the source can be 4K/HEVC, and a
    browser should not have to decode that to play a 30-second moment. Size and
    CRF are set for visual transparency; the encode is version-stamped so changing
    them later invalidates the cache.
    """
    m = moment_of(job_id, idx)
    video_path = ensure_source(job_id)

    out_preview = os.path.join(
        DOWNLOAD_DIR, f"{job_id}_preview{PREVIEW_CACHE_VERSION}_{idx}.mp4"
    )
    if not os.path.exists(out_preview) or os.path.getsize(out_preview) == 0:
        with artifact_lock(f"preview:{job_id}:{idx}"):
            if not os.path.exists(out_preview) or os.path.getsize(out_preview) == 0:
                encode_to([
                    "-y", "-ss", str(m["start"]), "-i", video_path,
                    "-t", str(m["end"] - m["start"]),
                    "-vf", "scale='min(1280,iw)':-2",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                    "-c:a", "aac", "-b:a", "128k",
                ], out_preview)

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
            try:
                ffmpeg_run([
                    "ffmpeg", "-y", "-ss", str(start), "-i", video_path, "-t", str(length),
                    "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav_path
                ])
            except RuntimeError as e:
                raise HTTPException(status_code=503, detail=str(e)) from e
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
            # Only reachable on a local checkout that skipped the optional
            # requirements file, or when the source has no speech at all.
            raise HTTPException(
                status_code=503,
                detail=(
                    "No speech could be transcribed from this clip. Local installs "
                    "need the optional engine: pip install -r requirements-transcribe.txt"
                ),
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

# get_stream_urls() used to hand a signed googlevideo URL to OpenCV for frame
# sampling. Those URLs expect the headers yt-dlp sends, so a bare request got 403
# and every URL job failed at "Analyzing video". process_job now downloads the
# source through yt-dlp instead, and ensure_source records the title and duration
# from the same extract, so this has no callers left.

def analyze_stream(video_url: str, duration: float, clip_length: float, count: int, job_id: str):
    j = JOBS[job_id]
    
    if duration <= 0:
        duration = 10.0
        
    N = max(10, min(200, int(duration / 5)))
    step = duration / N
    times = [(i + 0.5) * step for i in range(N)]
    
    cap, cleanup = open_capture(video_url)
    try:
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
    finally:
        cap.release()
        cleanup()
    
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
            
            duration = probe_duration(video_path)
            if duration <= 0:
                raise Exception(
                    "Could not read this video's duration. It may be corrupt "
                    "or in a format this build cannot decode."
                )

            j["video_path"] = video_path
            if not j.get("title") or j["title"] == "Untitled":
                j["title"] = os.path.basename(video_path)
            j["duration"] = duration
        else:
            j["status"] = "Downloading source..."
            j["pct"] = 5

            # Download through yt-dlp rather than streaming the signed googlevideo
            # URL into OpenCV. Those URLs are bound to the requesting IP and expect
            # the headers yt-dlp sends; a bare ffmpeg/OpenCV request gets 403, which
            # surfaced as "Error opening input: Server returned 403 Forbidden" and
            # made every URL job fail. yt-dlp already does this download for
            # ensure_source, so doing it here means one download serves both the
            # analysis and every later clip/preview/transcribe.
            video_path = ensure_source(job_id)

            if not j.get("title") or j["title"] == "Untitled":
                j["title"] = os.path.basename(video_path)
            duration = j.get("duration") or 0
            if duration <= 0:
                duration = probe_duration(video_path)
                j["duration"] = duration

        if duration <= 0:
            duration = 10.0

        j["status"] = "Analyzing video..."
        j["pct"] = 10

        video_url = j["video_path"]

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