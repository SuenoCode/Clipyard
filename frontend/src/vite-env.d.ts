/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

interface SkimReelRuntimeConfig {
  /** Origin of the API, e.g. https://skimreel-api.fly.dev. Empty for same-origin. */
  apiBase?: string
}

interface Window {
  __SKIMREEL_CONFIG__?: SkimReelRuntimeConfig
}
