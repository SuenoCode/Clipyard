const API_BASE = ""

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
  const res = await fetch(`${API_BASE}/api/analyze`, {
    method: "POST",
    body: data,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Analysis failed" }))
    throw new Error(err.detail || "Analysis failed")
  }
  return res.json()
}

export async function pollJob(jobId: string): Promise<JobStatus> {
  const res = await fetch(`${API_BASE}/api/job/${jobId}`)
  if (!res.ok) {
    throw new Error("Failed to poll job status")
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
    const err = await res
      .json()
      .catch(() => ({ detail: "Transcription failed" }))
    throw new Error(err.detail || "Transcription failed")
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
