// Where the API lives, resolved in precedence order:
//
//   1. VITE_API_BASE      build-time env var, when one is available
//   2. public/config.js   runtime file, read at page load from
//                         window.__SKIMREEL_CONFIG__.apiBase
//   3. ""                 same-origin, which the Vite dev proxy handles
//
// config.js is the normal path: the host can change in one committed line with
// no rebuild and no dashboard step.
const API_BASE = (
  import.meta.env.VITE_API_BASE ||
  window.__SKIMREEL_CONFIG__?.apiBase ||
  ""
).replace(/\/$/, "")

export const API_ORIGIN = API_BASE || window.location.origin

/**
 * Turn a non-OK response into something diagnosable.
 *
 * The API answers errors with JSON `{detail}`. When the request never reaches
 * the API at all - no API host configured means this hits the SPA host, which
 * answers 404 with HTML - the generic "Analysis failed" hides the real cause.
 */
async function describeFailure(
  res: Response,
  fallback: string,
): Promise<string> {
  const type = res.headers.get("content-type") ?? ""
  let body: string | null = null
  try {
    body = await res.text()
  } catch {
    /* body already consumed or unreadable */
  }

  if (type.includes("application/json") && body) {
    try {
      const parsed = JSON.parse(body)
      const detail = parsed?.detail
      if (typeof detail === "string" && detail) return detail
      if (Array.isArray(detail) && detail[0]?.msg) return detail[0].msg
    } catch {
      /* fall through to the generic message */
    }
  }

  // An HTML body means a static host answered instead of the API.
  if (type.includes("text/html")) {
    return (
      `${fallback} (HTTP ${res.status} from ${res.url} returned HTML, not the API). ` +
      "No API host is configured, so this went to the site itself. Set " +
      "apiBase in frontend/public/config.js, or VITE_API_BASE in Vercel, to the " +
      "deployed backend origin."
    )
  }
  return `${fallback} (HTTP ${res.status})`
}

export interface Moment {
  start: number
  end: number
  range: string
  score: number
  why: string
  peak: number
  transcript: string
}

export interface TranscriptSegment {
  start: number
  end: number
  text: string
}

export interface Transcript {
  idx: number
  text: string
  segments: TranscriptSegment[]
  srt: string
  engine: string
}

export interface JobStatus {
  done: boolean
  status: string
  pct: number
  title: string
  moments: Moment[]
  error: string | null
  duration?: number
  transcripts?: Record<string, Transcript>
}

export async function analyzeVideo(data: FormData): Promise<{ job: string }> {
  let res: Response
  try {
    res = await fetch(`${API_BASE}/api/analyze`, { method: "POST", body: data })
  } catch (cause) {
    // Network-level failure: wrong host, blocked by CORS, or the API is down.
    throw new Error(
      `Could not reach the API at ${API_ORIGIN}. Set apiBase in ` +
        `frontend/public/config.js (or VITE_API_BASE in Vercel), and allow this ` +
        `site's origin on the backend. (${
          cause instanceof Error ? cause.message : String(cause)
        })`,
    )
  }
  if (!res.ok) {
    throw new Error(await describeFailure(res, "Analysis failed"))
  }
  return res.json()
}

export async function pollJob(jobId: string): Promise<JobStatus> {
  const res = await fetch(`${API_BASE}/api/job/${jobId}`)
  if (!res.ok) {
    throw new Error(
      await describeFailure(res, "Failed to check analysis progress"),
    )
  }
  return res.json()
}

export function clipUrl(jobId: string, idx: number): string {
  return `${API_BASE}/api/clip/${jobId}/${idx}`
}

export function previewUrl(jobId: string, idx: number): string {
  return `${API_BASE}/api/preview/${jobId}/${idx}`
}

export function transcriptUrl(jobId: string, idx: number): string {
  return `${API_BASE}/api/transcript/${jobId}/${idx}`
}

export async function transcribeClip(
  jobId: string,
  idx: number,
): Promise<Transcript> {
  const res = await fetch(`${API_BASE}/api/transcribe/${jobId}/${idx}`, {
    method: "POST",
  })
  if (!res.ok) {
    throw new Error(await describeFailure(res, "Transcription failed"))
  }
  return res.json()
}

export function deepLink(url: string, startSec: number): string | null {
  try {
    const u = new URL(url)
    const h = u.hostname.replace(/^www\.|^m\./, "")
    const s = Math.floor(startSec)
    if (h === "youtu.be" || h.endsWith("youtube.com")) {
      u.searchParams.set("t", `${s}s`)
      return u.href
    }
    if (h.endsWith("twitch.tv")) {
      const h_ = Math.floor(s / 3600)
      const m_ = Math.floor((s % 3600) / 60)
      const s_ = s % 60
      u.searchParams.set("t", `${h_}h${m_}m${s_}s`)
      return u.href
    }
  } catch {
    return null
  }
  return null
}

export function fmtTime(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = String(s % 60).padStart(2, "0")
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`
}
