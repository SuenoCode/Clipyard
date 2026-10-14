import { useEffect, useMemo, useRef, useState } from "react"
import Icon from "./Icon"

interface LoadingViewProps {
  stage: string
  pct: number
  /** Byte counts, speed and ETA for the transfer phase; empty once past it. */
  detail?: string
  sourceLabel: string
  /** True when the source is a link, so the first step is a download. */
  remote?: boolean
  onCancel: () => void
}

// Three stages, three bars, and the overall percentage at which each one ends.
// Mirrors PCT_TRANSFER_END / PCT_MOTION_END / PCT_PICK_END in app.py, so one
// number drives all three fills and the labels under them.
const STEPS = [
  {
    start: 0,
    end: 60,
    label: "Transferring footage",
    remoteLabel: "Downloading source",
  },
  { start: 60, end: 90, label: "Analyzing motion" },
  { start: 90, end: 99, label: "Picking moments" },
]

// Dark to light, one shade per stage, so the bar reads left to right as work
// getting closer to the result instead of three identical slates. The last
// stage is the one this app exists to do, so it gets the brightest.
const STAGE_COLORS = ["#0d5a61", "#158b95", "#22d3e0"]

/** How full one stage's bar is, given the job's overall percentage. */
function segmentFill(pct: number, step: { start: number; end: number }): number {
  if (pct >= step.end) return 100
  if (pct <= step.start) return 0
  return ((pct - step.start) / (step.end - step.start)) * 100
}

// Picking the moments is a sort and takes milliseconds, so the API can cross a
// stage boundary inside one poll and the third bar would jump from empty to full
// without its label ever appearing. The display therefore walks forward at most
// one stage per MIN_PHASE_MS, showing a skipped stage half filled, and catches
// up on the poll after that.
const MIN_PHASE_MS = 700

export default function LoadingView({
  stage,
  pct,
  detail,
  sourceLabel,
  remote,
  onCancel,
}: LoadingViewProps) {
  const steps = useMemo(
    () =>
      STEPS.map((s) =>
        remote && s.remoteLabel ? { ...s, label: s.remoteLabel } : s,
      ),
    [remote],
  )
  const [shown, setShown] = useState(pct)
  const phase = useRef(0)
  const shownAt = useRef(0)

  useEffect(() => {
    const found = steps.findIndex((s) => pct < s.end)
    const target = found === -1 ? steps.length - 1 : found

    if (target <= phase.current) {
      // Inside the phase already on screen, so its own progress comes straight
      // through; only a phase change is held back.
      setShown(Math.max(pct, steps[phase.current].start))
      return
    }

    let cancelled = false
    const advance = () => {
      if (cancelled) return
      const next = Math.min(phase.current + 1, target)
      phase.current = next
      shownAt.current = Date.now()
      // A phase the server skipped over is shown half filled, so its segment
      // reads as in progress rather than as a stalled empty one.
      const skipped = next === target ? pct : (steps[next].start + steps[next].end) / 2
      setShown(skipped)
    }

    const wait = MIN_PHASE_MS - (Date.now() - shownAt.current)
    if (wait <= 0) {
      advance()
      return
    }
    const timer = window.setTimeout(advance, wait)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [pct, steps])

  const found = steps.findIndex((s) => shown < s.end)
  // All three done: the job is ready and the results take over shortly after.
  const ready = found === -1
  const current = ready ? steps.length - 1 : found
  const active = steps.filter((s) => shown >= s.end).length
  // The number is the current stage's own progress, not the job's overall one:
  // a bar three quarters full with "87%" next to it is the mismatch that made
  // the segmented bar unreadable.
  const stagePct = ready
    ? 100
    : ((shown - steps[current].start) /
        (steps[current].end - steps[current].start)) *
      100

  return (
    <div className="app-shell">
      <div className="top-rule">
        <span>INDEPENDENT TOOLS. LIMITLESS CREATIVITY.</span>
        <span>EST. 2025 / V.01</span>
      </div>
      <main>
        <header className="hero">
          <div className="hero-meta">
            <span className="status-dot status-dot-pulse" /> ANALYZING
          </div>
          <div className="brand">
            <span className="brand-script">Skim</span>
            <span className="brand-serif">Reel</span>
            <span className="brand-star" aria-hidden="true">
              ✳
            </span>
          </div>
          <div className="hero-bottom">
            <p>Hunting the good parts</p>
            <span className="creator">
              created by <strong>Sueno</strong>
            </span>
          </div>
          <span className="hero-note">
            Hang tight.
            <br />
            We got this.
          </span>
        </header>

        <div className="section-divider">
          <span>WORKING ON IT</span>
          <span>
            {sourceLabel || "YOUR FOOTAGE"} <Icon name="arrow" size={15} />
          </span>
        </div>

        <section className="progress-panel" aria-live="polite" aria-busy="true">
          <div className="progress-head">
            <span className="progress-stage">
              {ready ? "Ready" : `${current + 1}/${steps.length} · ${stage}`}
            </span>
            <span className="progress-pct">{Math.round(stagePct)}%</span>
          </div>
          <div
            className="progress-bar"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(stagePct)}
            aria-valuetext={`${steps[current].label} ${Math.round(stagePct)}%`}
          >
            {steps.map((step, i) => (
              <div
                className={i === current && !ready ? "progress-seg current" : "progress-seg"}
                key={step.label}
              >
                <div
                  className={
                    i < active ? "progress-seg-fill done" : "progress-seg-fill"
                  }
                  style={{
                    width: `${segmentFill(shown, step)}%`,
                    background: STAGE_COLORS[i],
                  }}
                />
              </div>
            ))}
          </div>
          {detail && <p className="progress-detail">{detail}</p>}

          <ol className="progress-steps">
            {steps.map((step, i) => (
              <li
                key={step.label}
                className={i < active ? "done" : i === active ? "current" : ""}
              >
                <span
                  className="step-dot"
                  // Done and running dots take the stage's own shade, so the
                  // list under the bar reads as the same three-stage ramp.
                  // Stages not reached yet stay grey.
                  style={
                    i <= active
                      ? { borderColor: STAGE_COLORS[i], color: STAGE_COLORS[i] }
                      : undefined
                  }
                >
                  {i < active ? <Icon name="check" size={12} /> : null}
                </span>
                {step.label}
              </li>
            ))}
          </ol>

          <div className="progress-actions">
            <button className="ghost-button" onClick={onCancel}>
              <Icon name="close" size={15} /> Cancel
            </button>
            <span className="progress-hint">
              Preview and transcript unlock once the pass finishes.
            </span>
          </div>
        </section>

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
