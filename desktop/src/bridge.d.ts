import type {
  EnvironmentInstallResult,
  EnvironmentStatus,
  EnvironmentToolId,
  ModelCatalog,
} from './settingsContract';
import type { EditorDocument } from './editorContract';
import type { QuickNote } from './MagicPenSettings';

export interface PapilotBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

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
      detectEnvironment(): Promise<EnvironmentStatus>;
      installEnvironmentTool(id: EnvironmentToolId): Promise<EnvironmentInstallResult>;
      openEnvironmentHelp(id: EnvironmentToolId): Promise<void>;
      tintWindowControls(color: string): Promise<void>;
      papilotShow(bounds: PapilotBounds): Promise<void>;
      papilotBounds(bounds: PapilotBounds): Promise<void>;
      papilotHide(): Promise<void>;
      papilotOpenQuestion(id: string, revision?: number, openManuscript?: boolean): Promise<EditorDocument>;
      papilotQuickNotes(notes?: QuickNote[] | null): Promise<QuickNote[] | null>;
      papilotModels(): Promise<ModelCatalog>;
      onPapilotShortcut(listener: () => void): () => void;
    };
  }
}
