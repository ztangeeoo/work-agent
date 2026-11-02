/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** CopilotKit 公开授权 key（留空则用自托管占位值） */
  readonly VITE_COPILOTKIT_KEY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
