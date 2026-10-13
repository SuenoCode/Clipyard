import { useState, useEffect, useRef } from "react"
import Icon from "./Icon"
import {
  Moment,
  Transcript,
  clipUrl,
  previewUrl,
  transcriptUrl,
  deepLink,
  fmtTime,
  transcribeClip,
} from "./api"

interface ResultsViewProps {
  moments: Moment[]
  jobId: string
  title: string
  duration: number
  initialTranscripts: Record<string, Transcript>
  originalUrl: string
  onBack: () => void
}

export default function ResultsView({
  moments,
  jobId,
  title,
  duration,
  initialTranscripts,
  originalUrl,
  onBack,
}: ResultsViewProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [timelineData, setTimelineData] = useState<number[]>([])
  const [previews, setPreviews] = useState<Record<number, boolean>>({})
  const [downloading, setDownloading] = useState<Record<number, boolean>>({})
  const [transcripts, setTranscripts] =
    useState<Record<string, Transcript>>(initialTranscripts)
  const [transcribing, setTranscribing] = useState<Record<number, boolean>>({})
  const [transcribeError, setTranscribeError] =
    useState<Record<number, string>>({})
  const [copied, setCopied] = useState<Record<number, boolean>>({})

  useEffect(() => {
    const maxScore = Math.max(...moments.map((m) => m.score), 1)
    setTimelineData(moments.map((m) => (m.score / maxScore) * 100))
  }, [moments])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext("2d")
    if (!ctx) return

    const bars = moments.length
    if (!bars) return
    const barWidth = canvas.width / bars
    ctx.clearRect(0, 0, canvas.width, canvas.height)
    ctx.fillStyle = "#1ec1cb"
    for (let i = 0; i < bars; i++) {
      const h = ((timelineData[i] ?? 0) / 100) * canvas.height
      ctx.fillRect(i * barWidth, canvas.height - h, barWidth - 1, h)
    }
  }, [timelineData, moments])

  async function downloadClip(idx: number) {
    if (downloading[idx]) return
    setDownloading((d) => ({ ...d, [idx]: true }))
    try {
      const res = await fetch(clipUrl(jobId, idx))
      if (!res.ok) throw new Error("Download failed")
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = `skimreel_${idx + 1}_${fmtTime(moments[idx].start).replace(/:/g, "-")}.mp4`
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      alert("Failed to download clip")
    } finally {
      setDownloading((d) => ({ ...d, [idx]: false }))
    }
  }

  async function runTranscribe(idx: number) {
    if (transcribing[idx]) return
    setTranscribing((t) => ({ ...t, [idx]: true }))
    setTranscribeError((e) => ({ ...e, [idx]: "" }))
    try {
      const result = await transcribeClip(jobId, idx)
      setTranscripts((t) => ({ ...t, [String(idx)]: result }))
    } catch (e) {
      setTranscribeError((s) => ({
        ...s,
        [idx]: e instanceof Error ? e.message : "Transcription failed",
      }))
    } finally {
      setTranscribing((t) => ({ ...t, [idx]: false }))
    }
  }

  async function copyTranscript(idx: number) {
    const data = transcripts[String(idx)]
    if (!data) return
    try {
      await navigator.clipboard.writeText(data.text)
      setCopied((c) => ({ ...c, [idx]: true }))
      window.setTimeout(() => setCopied((c) => ({ ...c, [idx]: false })), 1600)
    } catch {
      setTranscribeError((s) => ({
        ...s,
        [idx]: "Clipboard blocked by the browser.",
      }))
    }
  }

  const total = duration || Math.max(...moments.map((m) => m.end), 1)

  return (
    <div className="app-shell">
      <div className="top-rule">
        <span>INDEPENDENT TOOLS. LIMITLESS CREATIVITY.</span>
        <span>EST. 2025 / V.01</span>
      </div>

      <main>
        <header className="hero">
          <div className="hero-meta">
            <span className="status-dot" /> ANALYSIS COMPLETE
          </div>
          <div className="brand">
            <span className="brand-script">Skim</span>
            <span className="brand-serif">Reel</span>
            <span className="brand-star" aria-hidden="true">
              ✳
            </span>
          </div>
          <div className="hero-bottom">
            <p>{title}</p>
            <span className="creator">
              created by <strong>Sueno</strong>
            </span>
          </div>
          <span className="hero-note">
            {moments.length} moments found.
            <br />
            Pick your fire.
          </span>
        </header>

        <div className="section-divider">
          <span>03 / YOUR MOMENTS</span>
          <span>
            THE GOOD PARTS, ISOLATED <Icon name="arrow" size={15} />
          </span>
        </div>

        <div className="timeline-card">
          <h3>Excitement Timeline</h3>
          <canvas
            ref={canvasRef}
            width={800}
            height={80}
            className="timeline-canvas"
          />
          <div className="timeline-moments">
            {moments.map((m, i) => (
              <div
                key={i}
                className="timeline-moment"
                style={{
                  left: `${Math.min(98, Math.max(2, (m.start / total) * 100))}%`,
                }}
                title={`${m.range} - score ${m.score}`}
              >
                <span className="moment-dot" />
                <span className="moment-label">{fmtTime(m.start)}</span>
              </div>
            ))}
          </div>
          <div className="timeline-scale">
            <span>0:00</span>
            <span>{fmtTime(total)}</span>
          </div>
        </div>

        <section className="moments-grid" aria-label="Best moments">
          {moments.map((m, i) => {
            const transcript = transcripts[String(i)]
            const isPreviewing = !!previews[i]
            const isBusy = !!transcribing[i]
            const hasError = !!transcribeError[i]

            return (
              <article key={i} className="moment-card">
                <div className="moment-header">
                  <div className="moment-rank">{i + 1}</div>
                  <div className="moment-meta">
                    <div className="moment-range">{m.range}</div>
                    <div className="moment-score">Score: {m.score}</div>
                  </div>
                </div>

                {isPreviewing ? (
                  <div className="preview-wrap">
                    <video
                      className="preview-player"
                      src={previewUrl(jobId, i)}
                      controls
                      autoPlay
                      playsInline
                      preload="metadata"
                    />
                    <button
                      className="preview-close"
                      onClick={() => setPreviews((p) => ({ ...p, [i]: false }))}
                      aria-label="Close preview"
                    >
                      <Icon name="close" size={14} />
                    </button>
                  </div>
                ) : (
                  <button
                    className="preview-open"
                    onClick={() => setPreviews((p) => ({ ...p, [i]: true }))}
                  >
                    <span className="preview-play">
                      <Icon name="play" size={16} />
                    </span>
                    Preview clip
                  </button>
                )}

                <div className="moment-why">{m.why}</div>

                {transcript ? (
                  <div className="transcript-block">
                    <p className="transcript-text">{transcript.text}</p>
                    <div className="transcript-tools">
                      <span className="transcript-engine">
                        {transcript.engine}
                      </span>
                      <button
                        className="mini-button"
                        onClick={() => copyTranscript(i)}
                      >
                        <Icon name={copied[i] ? "check" : "copy"} size={13} />{" "}
                        {copied[i] ? "Copied" : "Copy"}
                      </button>
                      <a
                        className="mini-button"
                        href={transcriptUrl(jobId, i)}
                        download
                      >
                        <Icon name="download" size={13} /> .SRT
                      </a>
                    </div>
                  </div>
                ) : null}

                {hasError && (
                  <p className="transcript-error">{transcribeError[i]}</p>
                )}

                <div className="moment-actions">
                  <button
                    onClick={() => downloadClip(i)}
                    className="download-btn"
                    disabled={!!downloading[i]}
                  >
                    {downloading[i] ? (
                      <Icon name="spinner" size={16} className="spin" />
                    ) : (
                      <Icon name="download" size={16} />
                    )}
                    {downloading[i] ? "Preparing..." : "Download MP4"}
                  </button>
                  <button
                    onClick={() => runTranscribe(i)}
                    className="transcribe-btn"
                    disabled={isBusy || !!transcript}
                  >
                    {isBusy ? (
                      <Icon name="spinner" size={16} className="spin" />
                    ) : (
                      <Icon name="captions" size={16} />
                    )}
                    {isBusy
                      ? "Transcribing..."
                      : transcript
                        ? "Transcribed"
                        : "Transcribe"}
                  </button>
                  {deepLink(originalUrl, m.start) && (
                    <a
                      href={deepLink(originalUrl, m.start)!}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="open-btn"
                    >
                      <Icon name="link" size={16} /> Open at {fmtTime(m.start)}
                    </a>
                  )}
                </div>
              </article>
            )
          })}
        </section>

        <div className="action-row">
          <div className="action-copy">
            <span className="mini-star" aria-hidden="true">
              ✳
            </span>
            <div>
              <strong>Want another set?</strong>
              <p>Different cuts. Different heat.</p>
            </div>
          </div>
          <button
            className="analyze-button"
            onClick={onBack}
            style={{
              background: "transparent",
              borderColor: "var(--line)",
              color: "inherit",
              boxShadow: "none",
            }}
          >
            <span>New Analysis</span>
            <Icon name="arrow" size={23} />
          </button>
        </div>

        <footer>
          <span>
            SKIMREEL <span className="footer-slash">/</span> MADE FOR CREATORS,
            NOT COMPLICATIONS.
          </span>
          <span>TAKE THE RAW. MAKE IT GOLD.</span>
        </footer>
      </main>
    </div>
  )
}
