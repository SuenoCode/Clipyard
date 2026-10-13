// Empty string keeps requests same-origin, which is what the Vite dev server
// proxy and a Vercel rewrite both expect. Set VITE_API_BASE to the deployed API
// origin (e.g. https://api.skimreel.dev) when the SPA and API live on different
// hosts.
const API_BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "")

export const API_ORIGIN = API_BASE || window.location.origin

/**
 * Turn a non-OK response into something diagnosable.
 *
 * The API answers errors with JSON `{detail}`. When the request never reaches
 * the API at all - a missing VITE_API_BASE means this hits the SPA host, which
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
      "The API is probably not reachable: check that VITE_API_BASE is set in the " +
      "Vercel project and points at the deployed backend."
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
      `Could not reach the API at ${API_ORIGIN}. Check VITE_API_BASE and that ` +
        `the backend is running and allows this origin. (${
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
