import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

export interface ManagedFile {
  id: string;
  name: string;
  size: number;
  type: string;
  file: File;
}

interface FileContextValue {
  files: ManagedFile[];
  addFiles: (incoming: FileList | File[]) => void;
  removeFile: (id: string) => void;
  moveFile: (id: string, direction: -1 | 1) => void;
  clear: () => void;
}

const FileContext = createContext<FileContextValue | null>(null);

let counter = 0;
const nextId = () => `f${Date.now().toString(36)}-${counter++}`;

export function FileProvider({ children }: { children: ReactNode }) {
  const [files, setFiles] = useState<ManagedFile[]>([]);

  const addFiles = useCallback((incoming: FileList | File[]) => {
    const arr = Array.from(incoming);
    if (arr.length === 0) return;
    setFiles((prev) => [
      ...prev,
      ...arr.map((file) => ({
        id: nextId(),
        name: file.name,
        size: file.size,
        type: file.type,
        file,
      })),
    ]);
  }, []);

  const removeFile = useCallback((id: string) => {
    setFiles((prev) => prev.filter((f) => f.id !== id));
  }, []);

  const moveFile = useCallback((id: string, direction: -1 | 1) => {
    setFiles((prev) => {
      const idx = prev.findIndex((f) => f.id === id);
      const target = idx + direction;
      if (idx < 0 || target < 0 || target >= prev.length) return prev;
      const next = [...prev];
      const a = next[idx]!;
      const b = next[target]!;
      next[idx] = b;
      next[target] = a;
      return next;
    });
  }, []);

  const clear = useCallback(() => setFiles([]), []);

  const value = useMemo(
    () => ({ files, addFiles, removeFile, moveFile, clear }),
    [files, addFiles, removeFile, moveFile, clear],
  );
  return <FileContext.Provider value={value}>{children}</FileContext.Provider>;
}

export function useFiles(): FileContextValue {
  const ctx = useContext(FileContext);
  if (!ctx) throw new Error("useFiles must be used within FileProvider");
  return ctx;
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}
