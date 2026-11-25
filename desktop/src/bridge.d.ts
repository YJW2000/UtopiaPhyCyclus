import type {
  EnvironmentInstallResult,
  EnvironmentStatus,
  EnvironmentToolId,
} from './settingsContract';

export interface ClipboardImage {
  name: string;
  type: 'png' | 'jpeg';
  data: string;
}

declare global {
  interface Window {
    utopia: {
      call<T = any>(method: string, params?: object): Promise<T>;
      openFile(kind: 'tex' | 'pdf'): Promise<string | null>;
      readClipboardImage(): Promise<ClipboardImage | null>;
      saveFile(kind: 'tex' | 'pdf', suggestedName?: string): Promise<string | null>;
      chooseLibrary(): Promise<string | null>;
      asset(path: string): Promise<string>;
      legacy(route: string): Promise<string>;
      detectEnvironment(): Promise<EnvironmentStatus>;
      installEnvironmentTool(id: EnvironmentToolId): Promise<EnvironmentInstallResult>;
      openEnvironmentHelp(id: EnvironmentToolId): Promise<void>;
      tintWindowControls(color: string): Promise<void>;
    };
  }
}
