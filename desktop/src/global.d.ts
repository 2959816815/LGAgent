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

interface Window {
  desktop?: {
    getConfig(): Promise<DesktopConfig>;
    getSetupState(): Promise<SetupState>;
    getSettings(): Promise<SettingsSnapshot>;
    testDeepSeek(input: DeepSeekInput): Promise<{ ok: boolean }>;
    testSettings(input: DeepSeekInput): Promise<{ ok: boolean }>;
    saveDeepSeek(input: DeepSeekInput): Promise<DesktopConfig>;
    saveSettings(input: DeepSeekInput): Promise<DesktopConfig>;
    exportDiagnostics(): Promise<string | null>;
    getPathForFile(file: File): string;
  };
}
