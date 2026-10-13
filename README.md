# SkimReel

Find the good parts of a video. Point it at a YouTube or Twitch link or drop in a
local file, and it scores the footage for motion, cuts the strongest moments, and
generates clips, previews, and subtitles on demand.

## Why the backend is not on Vercel

The API was originally deployed to Vercel as a serverless function. That cannot
work, for reasons that are structural rather than fixable with configuration:

| Need | Vercel function | Why |
| --- | --- | --- |
| `ffmpeg` for clip/preview/transcode | not installed | binary must be in the image |
| Disk for downloaded sources | ephemeral, read-only outside `/tmp` | artifacts vanish on cold start |
| Long-running analysis | request-timeout bound | a Whisper pass can exceed it |
| Dependency bundle | 500 MB cap | OpenCV + `faster-whisper` exceeded it |

So the split is: **frontend on Vercel** (static, CDN-served) and **API on
Fly.io** (a container with ffmpeg and a volume). Same domain experience, no
serverless ceiling.

```
Browser ──► Vercel (SPA, static assets)
   │
   └─ CORS ─► Fly.io API  (FastAPI + ffmpeg + /data volume)
```

## Layout

```
app.py                    FastAPI: analyze, clip, preview, transcribe
Dockerfile                API image, adds ffmpeg + OpenCV's system libs
fly.toml                  Fly.io app + volume + health check
vercel.json               Frontend-only Vercel service
frontend/                 Vite + React SPA
requirements.txt          Runtime deps (OpenCV headless, no faster-whisper)
requirements-transcribe.txt  Optional local Whisper, too heavy for the bundle
```

## Local development

Two processes, the usual split:

```bash
# 1. API on :8000
pip install -r requirements.txt
uvicorn app:app --reload --port 8000

# 2. SPA on :8443, proxying /api to :8000
cd frontend
npm install
npm run dev
```

Optional local transcription:

```bash
pip install -r requirements-transcribe.txt
```

## YouTube bot check

YouTube refuses anonymous requests from datacenter IPs with *"Sign in to confirm
you're not a bot"*. There is no code change that avoids it; you need to
authenticate. Export cookies in Netscape format:

```bash
yt-dlp --cookies-from-browser chrome --cookies cookies.txt "<any url>"
```

Then point the API at the file:

```bash
export YTDLP_COOKIES_FILE=/data/cookies.txt   # path on the volume
```

Keep `cookies.txt` out of git (already ignored). It is a live account session:
anyone with it can act as that account. On Fly, prefer
`fly secrets set` over baking it into the image.

Also keep yt-dlp current — YouTube breaks older extractors often:

```bash
pip install -U yt-dlp
```

## Deploying the API to Fly.io

```bash
fly auth login
fly launch --no-deploy --copy-config
fly volumes create skimreel_data --size 10 --region ord
fly secrets set CORS_ALLOW_ORIGINS=https://<your-vercel-domain>
fly deploy
curl https://<your-app>.fly.dev/api/health
```

`/api/health` reports whether ffmpeg is present, cookies are configured, and the
download directory is writable — the three things that break silently otherwise.

Scale by adding machines, not workers: `JOBS` is in-process state, so a second
uvicorn worker would not see jobs started by the first.

## Deploying the frontend to Vercel

Set one project environment variable:

```
VITE_API_BASE=https://<your-app>.fly.dev
```

Vite inlines `VITE_API_BASE` at build time, so redeploy after changing it. The
`CORS_ALLOW_ORIGINS` value on Fly must include the Vercel domain exactly,
scheme included.

Local file uploads are capped by the host's request size limit; Vercel and Fly
both sit well below a typical video file, so large uploads will fail in
production even though they work locally.

## Configuration

See `.env.example` (API) and `frontend/.env.example` (SPA).