import { useRef, useState, useEffect } from "react"
import ResultsView from "./ResultsView"
import LoadingView from "./LoadingView"
import Icon from "./Icon"
import { analyzeVideo, pollJob, JobStatus } from "./api"

const assetPathPrefix = "/assets"

type View = "input" | "loading" | "results" | "error"

export default function App() {
  const [files, setFiles] = useState<File[]>([])
  const [link, setLink] = useState("")
  const [duration, setDuration] = useState(90)
  const [count, setCount] = useState(15)
  const [quality, setQuality] = useState("1080p")
  const [dragging, setDragging] = useState(false)
  const [message, setMessage] = useState("")
  const [view, setView] = useState<View>("input")
  const [jobId, setJobId] = useState<string | null>(null)
  const [jobStatus, setJobStatus] = useState<JobStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const pollInterval = useRef<number | null>(null)

  function addFiles(incoming: File[]) {
    const videos = incoming.filter(
      (file) =>
        file.type.startsWith("video/") ||
        /\.(mp4|mov|mkv|webm)$/i.test(file.name),
    )
    const picked = videos.slice(0, 1)
    setFiles(picked)
    setMessage(
      picked.length ? "" : "Choose a video file: MP4, MOV, MKV, or WEBM.",
    )
  }

  function validateUrl(url: string): boolean {
    try {
      const u = new URL(url)
      return [
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtu.be",
        "twitch.tv",
        "www.twitch.tv",
      ].includes(u.hostname)
    } catch {
      return false
    }
  }

  function startAnalysis() {
    if (!files.length && !link.trim())
      return setMessage(
        "Add a local video or paste a YouTube / Twitch link to get started.",
      )
    if (link.trim() && !validateUrl(link))
      return setMessage("Please enter a valid YouTube or Twitch URL.")

    setView("loading")
    setError(null)
    setMessage("")

    const formData = new FormData()
    if (files.length) {
      formData.append("file", files[0])
    } else {
      formData.append("url", link.trim())
    }
    formData.append("length", String(duration))
    formData.append("count", String(count))
    formData.append("quality", quality)

    analyzeVideo(formData)
      .then(({ job }) => {
        setJobId(job)
        startPolling(job)
      })
      .catch((err) => {
        setView("error")
        setError(err.message)
      })
  }

  function startPolling(job: string) {
    if (pollInterval.current) clearInterval(pollInterval.current)

    const check = async () => {
      try {
        const status = await pollJob(job)
        setJobStatus(status)
        if (status.done) {
          if (pollInterval.current) clearInterval(pollInterval.current)
          if (status.error) {
            setView("error")
            setError(status.error)
          } else {
            setView("results")
          }
        }
      } catch {
        if (pollInterval.current) clearInterval(pollInterval.current)
        setView("error")
        setError("Failed to check analysis progress")
      }
    }

    check()
    pollInterval.current = window.setInterval(check, 1200)
  }

  function handleBack() {
    setView("input")
    setJobId(null)
    setJobStatus(null)
    setError(null)
    if (pollInterval.current) clearInterval(pollInterval.current)
  }

  useEffect(() => {
    return () => {
      if (pollInterval.current) clearInterval(pollInterval.current)
    }
  }, [])

  const loadingStage = jobStatus?.status || "Starting analysis..."
  const loadingPct = jobStatus?.pct || 0
  const sourceLabel = files[0]?.name || (link.trim() ? "ONLINE SOURCE" : "")

  if (view === "loading") {
    return (
      <LoadingView
        stage={loadingStage}
        pct={loadingPct}
        sourceLabel={sourceLabel}
        onCancel={handleBack}
      />
    )
  }

  if (view === "results" && jobStatus) {
    return (
      <ResultsView
        moments={jobStatus.moments}
        jobId={jobId!}
        title={jobStatus.title}
        duration={jobStatus.duration || 0}
        initialTranscripts={jobStatus.transcripts || {}}
        originalUrl={link || files[0]?.name || ""}
        onBack={handleBack}
      />
    )
  }

  if (view === "error") {
    return (
      <div className="app-shell">
        <div className="top-rule">
          <span>INDEPENDENT TOOLS. LIMITLESS CREATIVITY.</span>
          <span>EST. 2025 / V.01</span>
        </div>
        <main>
          <header className="hero">
            <div className="hero-meta">
              <span className="status-dot" style={{ background: "#e85c5c" }} />{" "}
              ERROR
            </div>
            <div className="brand">
              <span className="brand-script">Skim</span>
              <span className="brand-serif">Reel</span>
              <span className="brand-star" aria-hidden="true">
                ✳
              </span>
            </div>
            <div className="hero-bottom">
              <p>Something went wrong</p>
              <span className="creator">
                created by <strong>Sueno</strong>
              </span>
            </div>
          </header>
          <div
            className="feedback"
            style={{
              background: "#3d2323",
              borderColor: "#e85c5c",
              maxWidth: 600,
              margin: "40px auto",
              textAlign: "center",
            }}
          >
            {error}
          </div>
          <div className="action-row" style={{ justifyContent: "center" }}>
            <button
              className="analyze-button"
              onClick={handleBack}
              style={{
                background: "transparent",
                borderColor: "var(--line)",
                color: "inherit",
                boxShadow: "none",
                transform: "none",
              }}
            >
              <span>Try Again</span>
              <Icon name="arrow" size={23} />
            </button>
          </div>
          <footer>
            <span>
              SKIMREEL <span className="footer-slash">/</span> MADE FOR
              CREATORS, NOT COMPLICATIONS.
            </span>
            <span>TAKE THE RAW. MAKE IT GOLD.</span>
          </footer>
        </main>
      </div>
    )
  }

  return (
    <div className="app-shell">
      <div className="top-rule">
        <span>INDEPENDENT TOOLS. LIMITLESS CREATIVITY.</span>
        <span>EST. 2025 / V.01</span>
      </div>
      <main>
        <header className="hero">
          <div className="hero-meta">
            <span className="status-dot" /> BUILT FOR THE GOOD PARTS
          </div>
          <div className="brand">
            <span className="brand-script">Skim</span>
            <span className="brand-serif">Reel</span>
            <span className="brand-star" aria-hidden="true">
              ✳
            </span>
          </div>
          <div className="hero-bottom">
            <p>Your one click clipping tool</p>
            <span className="creator">
              created by <strong>Sueno</strong>
            </span>
          </div>
          <span className="hero-note">
            Less footage.
            <br />
            More fire.
          </span>
        </header>

        <div className="section-divider">
          <span>01 / ADD YOUR FOOTAGE</span>
          <span>
            YOUR NEXT GREAT CLIP STARTS HERE <Icon name="arrow" size={15} />
          </span>
        </div>
        <section className="source-grid" aria-label="Video sources">
          <article className="source-card">
            <div className="card-heading">
              <h2>Import Local Clips Here</h2>
              <span className="tiny-tag">LOCAL</span>
            </div>
            <button
              type="button"
              className={`drop-zone ${dragging ? "dragging" : ""}`}
              onClick={() => input.current?.click()}
              onDragOver={(e) => {
                e.preventDefault()
                setDragging(true)
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault()
                setDragging(false)
                addFiles(Array.from(e.dataTransfer.files))
              }}
            >
              <span className="upload-icon">
                <Icon name={files.length ? "check" : "upload"} size={29} />
              </span>
              <strong>
                {files.length ? "1 video selected" : "Drop your footage here"}
              </strong>
              <span>
                {files.length ? (
                  files[0].name
                ) : (
                  <>
                    or <u>browse files</u> from your device
                  </>
                )}
              </span>
              <small>MP4, MOV, MKV, WEBM</small>
              <span className="corner corner-tl" />
              <span className="corner corner-br" />
            </button>
            <input
              ref={input}
              type="file"
              accept="video/*,.mkv"
              multiple
              hidden
              onChange={(e) => addFiles(Array.from(e.target.files || []))}
            />
            <p className="card-footnote">
              <span className="status-dot" /> Your footage stays yours. Always.
            </p>
          </article>
          <article className="source-card">
            <div className="card-heading">
              <h2>Paste Youtube/Twitch Link Here</h2>
              <span className="tiny-tag">ONLINE</span>
            </div>
            <div className="link-zone">
              <div className="platforms">
                <span>
                  <Icon name="film" size={16} /> YOUTUBE
                </span>
                <span className="platform-slash">/</span>
                <span>TWITCH</span>
              </div>
              <label htmlFor="video-link">Got a link? Drop it in.</label>
              <div className="link-input">
                <Icon name="link" size={19} />
                <input
                  id="video-link"
                  type="url"
                  placeholder="https://youtube.com/watch?v=..."
                  value={link}
                  onChange={(e) => {
                    setLink(e.target.value)
                    setMessage("")
                  }}
                />
                <span className="input-arrow">
                  <Icon name="arrow" size={18} />
                </span>
              </div>
              <p>Long streams. Big moments. We'll find the gems.</p>
            </div>
            <p className="card-footnote">FROM FULL-LENGTH TO HIGHLIGHT-REEL.</p>
          </article>
        </section>

        <div className="section-divider settings-divider">
          <span>02 / MAKE IT YOURS</span>
          <span>YOU CALL THE SHOTS</span>
        </div>
        <section className="settings-panel" aria-labelledby="settings-title">
          <div className="settings-intro">
            <h2 id="settings-title">Clip Settings</h2>
            <p>
              The right cut.
              <br />
              Every time.
            </p>
            <span className="settings-mark" aria-hidden="true">
              ↗
            </span>
          </div>
          <div className="sliders">
            <div className="range-group">
              <div className="control-label">
                <label htmlFor="duration">Duration of Clips</label>
                <output htmlFor="duration">
                  {duration}
                  <small> SEC</small>
                </output>
              </div>
              <div className="range-track">
                <img src={`${assetPathPrefix}/3ceed.svg`} alt="" />
                <input
                  id="duration"
                  type="range"
                  min="5"
                  max="180"
                  value={duration}
                  onChange={(e) => setDuration(+e.target.value)}
                />
              </div>
              <div className="range-ticks">
                <span>0s</span>
                <span>90s</span>
                <span>180s</span>
              </div>
            </div>
            <div className="range-group">
              <div className="control-label">
                <label htmlFor="count">Number of Clips</label>
                <output htmlFor="count">
                  {count}
                  <small> CLIPS</small>
                </output>
              </div>
              <div className="range-track">
                <img src={`${assetPathPrefix}/3ceed.svg`} alt="" />
                <input
                  id="count"
                  type="range"
                  min="1"
                  max="30"
                  value={count}
                  onChange={(e) => setCount(+e.target.value)}
                />
              </div>
              <div className="range-ticks">
                <span>0</span>
                <span>15</span>
                <span>30</span>
              </div>
            </div>
          </div>
          <fieldset className="quality">
            <legend>Quality</legend>
            <div className="quality-options">
              {["1080p", "720p", "480p", "360p"].map((item) => (
                <label
                  key={item}
                  className={quality === item ? "selected" : ""}
                >
                  <input
                    type="radio"
                    name="quality"
                    value={item}
                    checked={quality === item}
                    onChange={() => setQuality(item)}
                  />
                  <span>{item}</span>
                  {item === "1080p" && <small>FULL HD</small>}
                </label>
              ))}
            </div>
            <p>
              Good footage deserves
              <br />a sharp finish.
            </p>
          </fieldset>
        </section>

        <div className="action-row">
          <div className="action-copy">
            <span className="mini-star" aria-hidden="true">
              ✳
            </span>
            <div>
              <strong>Find the moments that matter.</strong>
              <p>Less scrolling. More creating.</p>
            </div>
          </div>
          <button className="analyze-button" onClick={startAnalysis}>
            <img src={`${assetPathPrefix}/f9370.svg`} alt="" />
            <span>Analyze</span>
            <Icon name="arrow" size={23} />
          </button>
        </div>

        {message && (
          <div role="status" className="feedback">
            {message}
            <button onClick={() => setMessage("")} aria-label="Dismiss message">
              ×
            </button>
          </div>
        )}
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
