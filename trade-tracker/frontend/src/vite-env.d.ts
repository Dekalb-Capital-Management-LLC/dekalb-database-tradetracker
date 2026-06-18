/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Production only: full URL of the deployed Trade Tracker API (e.g. Railway). */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
