declare const __APP_VERSION__: string;

interface DesktopConfig {
  baseUrl: string;
  token: string;
}

interface SetupState {
  needsSetup: boolean;
  baseUrl: string;
  model: string;
}

interface DeepSeekInput {
  apiKey: string;
  baseUrl: string;
  model: string;
}

interface SettingsSnapshot {
  baseUrl: string;
  model: string;
  hasApiKey: boolean;
}

interface ConfiguredModel {
  id: string;
  kind: "api" | "local";
  label: string;
  base_url: string;
  model: string;
  hasKey?: boolean;
  supports_vision?: boolean;
}

interface ModelSaveEntry {
  id: string;
  kind: "api" | "local";
  label: string;
  base_url: string;
  model: string;
  api_key?: string;
  supports_vision?: boolean;
}

interface ModelTestInput {
  kind: "api" | "local";
  baseUrl: string;
  model: string;
  apiKey?: string;
}

interface Window {
  desktop?: {
    getConfig(): Promise<DesktopConfig>;
    getSetupState(): Promise<SetupState>;
    getSettings(): Promise<SettingsSnapshot>;
    getModels(): Promise<ConfiguredModel[]>;
    saveModels(models: ModelSaveEntry[]): Promise<DesktopConfig>;
    testModel(input: ModelTestInput): Promise<{ ok: boolean }>;
    testDeepSeek(input: DeepSeekInput): Promise<{ ok: boolean }>;
    testSettings(input: DeepSeekInput): Promise<{ ok: boolean }>;
    saveDeepSeek(input: DeepSeekInput): Promise<DesktopConfig>;
    saveSettings(input: DeepSeekInput): Promise<DesktopConfig>;
    exportDiagnostics(): Promise<string | null>;
    pickFolder?(): Promise<string | null>;
    getPathForFile(file: File): string;
    getUpdateStatus?(): Promise<DesktopUpdateStatus>;
    checkForUpdates?(): Promise<DesktopUpdateStatus>;
    openReleases?(): Promise<boolean>;
    installUpdate?(): Promise<DesktopUpdateStatus>;
    onUpdateStatus?(callback: (status: DesktopUpdateStatus) => void): () => void;
  };
}

interface DesktopUpdateStatus {
  status:
    | "idle"
    | "checking"
    | "current"
    | "downloading"
    | "downloaded"
    | "stalled"
    | "error"
    | "unavailable";
  version: string;
  percent: number;
  transferred: number;
  total: number;
  bytesPerSecond: number;
  message: string;
}
