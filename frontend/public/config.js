// Runtime configuration for the SkimReel SPA.
//
// This file is served as-is (Vite copies public/ verbatim) and read at page
// load, so the API host can change without touching application code or
// rebuilding. It is committed on purpose: it holds a public URL, not a secret.
window.__SKIMREEL_CONFIG__ = {
  // Origin of the FastAPI backend: no trailing slash, no /api suffix.
  // Leave empty for same-origin, which is what `npm run dev` expects via the
  // Vite proxy.
  //
  // VITE_API_BASE, if set, takes precedence over this value.
  apiBase: "",
}
