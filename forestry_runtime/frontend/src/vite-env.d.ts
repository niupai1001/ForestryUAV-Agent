/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Where the local scorecard viewer listens; see components/ScorecardButton.tsx. */
  readonly VITE_SCORECARD_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
