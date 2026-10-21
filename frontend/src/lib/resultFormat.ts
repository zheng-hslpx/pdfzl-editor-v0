export type ResultKind = "pdf" | "zip" | "image" | "text" | "json" | "docx" | "other";

const MIME_KINDS: Record<string, ResultKind> = {
  "application/pdf": "pdf",
  "application/zip": "zip",
  "application/x-zip-compressed": "zip",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
  "application/msword": "docx",
  "text/plain": "text",
  "application/json": "json",
};

const EXTENSION_KINDS: Record<string, ResultKind> = {
  pdf: "pdf",
  zip: "zip",
  doc: "docx",
  docx: "docx",
  txt: "text",
  json: "json",
  png: "image",
  jpg: "image",
  jpeg: "image",
  gif: "image",
  bmp: "image",
  webp: "image",
  tif: "image",
  tiff: "image",
};

function kindFromMime(mimeType: string): ResultKind | null {
  const mime = mimeType.split(";", 1)[0]!.trim().toLowerCase();
  if (MIME_KINDS[mime]) return MIME_KINDS[mime]!;
  if (mime.startsWith("image/")) return "image";
  if (mime.startsWith("text/")) return "text";
  if (mime.endsWith("+json")) return "json";
  return null;
}

function kindFromFilename(filename: string): ResultKind | null {
  const extension = filename.split(".").at(-1)?.trim().toLowerCase();
  return extension ? EXTENSION_KINDS[extension] ?? null : null;
}

/** Resolve the displayed result from its actual response MIME and filename. */
export function resolveOutputKind(
  filename: string,
  mimeType: string,
  fallback: ResultKind = "other",
): ResultKind {
  const mimeKind = kindFromMime(mimeType);
  const extensionKind = kindFromFilename(filename);
  if (mimeKind && extensionKind && mimeKind !== extensionKind) return "other";
  return mimeKind ?? extensionKind ?? fallback;
}

export function resultKindLabel(kind: ResultKind): string {
  switch (kind) {
    case "pdf": return "PDF 文档";
    case "zip": return "ZIP 压缩包";
    case "image": return "图片";
    case "text": return "文本文件";
    case "json": return "JSON 文件";
    case "docx": return "Word 文档";
    default: return "未知格式";
  }
}
