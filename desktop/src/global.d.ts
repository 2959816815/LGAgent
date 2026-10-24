interface DesktopConfig {
  baseUrl: string;
  token: string;
}

interface Window {
  desktop?: {
    getConfig(): Promise<DesktopConfig>;
    getPathForFile(file: File): string;
  };
}
