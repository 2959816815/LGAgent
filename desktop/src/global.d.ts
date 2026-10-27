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

interface Window {
  desktop?: {
    getConfig(): Promise<DesktopConfig>;
    getSetupState(): Promise<SetupState>;
    testDeepSeek(input: DeepSeekInput): Promise<{ ok: boolean }>;
    saveDeepSeek(input: DeepSeekInput): Promise<DesktopConfig>;
    getPathForFile(file: File): string;
  };
}
