import { useLayoutEffect, useRef, useState } from "react"
import Icon from "./Icon"
import { describeFailure, previewUrl } from "./api"

interface PreviewPlayerProps {
  jobId: string
  idx: number
  onClose: () => void
}

export default function PreviewPlayer({
  jobId,
  idx,
  onClose,
}: PreviewPlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [muted, setMuted] = useState(false)
  const [audioBlocked, setAudioBlocked] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Opening a preview is a click, so playback is kicked off imperatively while
  // the user activation is still live. The preview carries an audio track, so
  // the browser would refuse an autoPlay attribute outright.
  useLayoutEffect(() => {
    const el = videoRef.current
    if (!el) return
    el.muted = false
    el.play().catch(() => {
      const node = videoRef.current
      if (!node) return
      setAudioBlocked(true)
      node.muted = true
      setMuted(true)
      node.play().catch(() => {})
    })
  }, [])

  // The native control bar writes muted straight onto the element, so mirror
  // it back into state to keep the overlay button in sync.
  useLayoutEffect(() => {
    const el = videoRef.current
    if (!el) return
    const sync = () => setMuted(el.muted)
    el.addEventListener("volumechange", sync)
    return () => el.removeEventListener("volumechange", sync)
  }, [])

  // A failed load surfaces as a silent black box otherwise: the media element
  // reports neither status nor body. Re-fetch on the error path only, so a healthy
  // preview costs nothing extra. A failed encode re-enters get_preview behind the
  // same artifact lock, and the API publishes previews atomically, so there is no
  // truncated artifact to be served in place of the error.
  async function reportFailure() {
    const fallback = "This preview could not be played."
    try {
      const res = await fetch(previewUrl(jobId, idx))
      setError(res.ok ? fallback : await describeFailure(res, fallback))
    } catch {
      setError(fallback)
    }
  }

  function toggleMuted() {
    const el = videoRef.current
    if (!el) return
    el.muted = !el.muted
    if (!el.muted) setAudioBlocked(false)
    setMuted(el.muted)
  }

  return (
    <div className="preview-wrap">
      {error ? (
        <p className="preview-error">{error}</p>
      ) : (
        <video
          ref={videoRef}
          className="preview-player"
          src={previewUrl(jobId, idx)}
          controls
          playsInline
          preload="metadata"
          onError={reportFailure}
        />
      )}
      {!error && (
        <>
          <button
            className="preview-mute"
            onClick={toggleMuted}
            aria-pressed={muted}
            aria-label={muted ? "Unmute preview" : "Mute preview"}
            title={muted ? "Unmute preview" : "Mute preview"}
          >
            <Icon name={muted ? "mute" : "volume"} size={14} />
          </button>
          {audioBlocked && (
            <span className="preview-audio-hint">Tap to unmute</span>
          )}
        </>
      )}
      <button
        className="preview-close"
        onClick={onClose}
        aria-label="Close preview"
      >
        <Icon name="close" size={14} />
      </button>
    </div>
  )
}
