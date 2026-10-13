import Icon from "./Icon"

interface LoadingViewProps {
  stage: string
  pct: number
  sourceLabel: string
  onCancel: () => void
}

const STEPS = [
  { label: "Reading footage", at: 0 },
  { label: "Finding motion", at: 12 },
  { label: "Picking moments", at: 80 },
  { label: "Cutting clips", at: 100 },
]

export default function LoadingView({
  stage,
  pct,
  sourceLabel,
  onCancel,
}: LoadingViewProps) {
  const active = STEPS.filter((s) => pct >= s.at).length - (pct >= 100 ? 1 : 0)

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
            <span className="brand-script">Clip</span>
            <span className="brand-serif">Forge</span>
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
            <span className="progress-stage">{stage}</span>
            <span className="progress-pct">{Math.round(pct)}%</span>
          </div>
          <div className="progress-track">
            <div
              className="progress-fill"
              style={{ width: `${Math.min(100, Math.max(pct, 2))}%` }}
            />
          </div>

          <ol className="progress-steps">
            {STEPS.map((step, i) => (
              <li
                key={step.label}
                className={i < active ? "done" : i === active ? "current" : ""}
              >
                <span className="step-dot">
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
            CLIPFORGE <span className="footer-slash">/</span> MADE FOR CREATORS,
            NOT COMPLICATIONS.
          </span>
          <span>TAKE THE RAW. MAKE IT GOLD.</span>
        </footer>
      </main>
    </div>
  )
}
