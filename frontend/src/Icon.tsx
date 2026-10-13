export type IconName = "upload" | "link" | "arrow" | "film" | "check" | "play" | "download" | "captions" | "close" | "copy" | "spinner" | "volume" | "mute"

export default function Icon({
  name,
  size = 22,
  className,
}: {
  name: IconName
  size?: number
  className?: string
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className}
    >
      {name === "upload" && (
        <>
          <path d="M12 16V3m-5 5 5-5 5 5" />
          <path d="M4 15v5h16v-5" />
        </>
      )}
      {name === "link" && (
        <>
          <path d="m10 13 4-4" />
          <path
            d="M8 16 6 18a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0m2 1 2-2a4 4 0 0 1 6 6l-5 5a4 4 0 0 1-6 0"
            transform="translate(1 0)"
          />
        </>
      )}
      {name === "arrow" && <path d="M4 12h16m-6-6 6 6-6 6" />}
      {name === "film" && (
        <>
          <rect x="3" y="4" width="18" height="16" rx="2" />
          <path d="M7 4v16M17 4v16M3 9h4m-4 6h4m10-6h4m-4 6h4" />
        </>
      )}
      {name === "check" && <path d="m5 12 4 4L19 6" />}
      {name === "play" && (
        <path d="M7 4.5v15l12-7.5z" fill="currentColor" stroke="none" />
      )}
      {name === "download" && (
        <>
          <path d="M12 3v12m-5-5 5 5 5-5" />
          <path d="M4 17v3h16v-3" />
        </>
      )}
      {name === "captions" && (
        <>
          <rect x="2.5" y="5" width="19" height="14" rx="2.5" />
          <path d="M10 10.2a2.2 2.2 0 1 0 0 3.6M18 10.2a2.2 2.2 0 1 0 0 3.6" />
        </>
      )}
      {name === "volume" && (
        <>
          <path d="M11 5 6 9H2v6h4l5 4z" />
          <path d="M15.5 8.5a4.5 4.5 0 0 1 0 7" />
          <path d="M18.5 5.5a9 9 0 0 1 0 13" />
        </>
      )}
      {name === "mute" && (
        <>
          <path d="M11 5 6 9H2v6h4l5 4z" />
          <path d="m16 9.5 6 5m0-5-6 5" />
        </>
      )}
      {name === "close" && <path d="M6 6l12 12M18 6 6 18" />}
      {name === "copy" && (
        <>
          <rect x="9" y="9" width="11" height="11" rx="2" />
          <path d="M5 15V5a1 1 0 0 1 1-1h9" />
        </>
      )}
      {name === "spinner" && (
        <>
          <circle cx="12" cy="12" r="9" opacity="0.25" />
          <path d="M21 12a9 9 0 0 0-9-9" />
        </>
      )}
    </svg>
  )
}
