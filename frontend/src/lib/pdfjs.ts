/** Shared pdf.js loader (used by PdfViewer and the editor). */
import type { PDFDocumentLoadingTask } from "pdfjs-dist";

type PdfjsModule = typeof import("pdfjs-dist");

let pdfjsPromise: Promise<PdfjsModule> | null = null;

export const PDF_OPEN_TIMEOUT_MS = 60_000;

export class PdfOpenTimeoutError extends Error {
  constructor() {
    super("PDF 预览加载超时，请重试。");
    this.name = "PdfOpenTimeoutError";
  }
}

/** Wait until a shared open deadline while allowing the owning component to cancel. */
export function withPdfOpenDeadline<T>(
  promise: Promise<T>,
  deadlineAt: number,
  signal: AbortSignal,
): Promise<T> {
  if (signal.aborted) {
    const error = new Error("PDF 预览加载已取消。");
    error.name = "AbortError";
    return Promise.reject(error);
  }
  const remainingMs = deadlineAt - Date.now();
  if (remainingMs <= 0) return Promise.reject(new PdfOpenTimeoutError());

  return new Promise<T>((resolve, reject) => {
    let settled = false;
    const timer = globalThis.setTimeout(() => settle("reject", new PdfOpenTimeoutError()), remainingMs);
    const onAbort = () => {
      const error = new Error("PDF 预览加载已取消。");
      error.name = "AbortError";
      settle("reject", error);
    };
    const settle = (kind: "resolve" | "reject", value: T | unknown) => {
      if (settled) return;
      settled = true;
      globalThis.clearTimeout(timer);
      signal.removeEventListener("abort", onAbort);
      if (kind === "resolve") resolve(value as T);
      else reject(value);
    };

    signal.addEventListener("abort", onAbort, { once: true });
    promise.then(
      (value) => settle("resolve", value),
      (reason: unknown) => settle("reject", reason),
    );
  });
}

export function loadPdfjs(): Promise<PdfjsModule> {
  if (!pdfjsPromise) {
    const loading = import("pdfjs-dist").then((mod) => {
      mod.GlobalWorkerOptions.workerSrc = new URL(
        "pdfjs-dist/build/pdf.worker.min.mjs",
        import.meta.url,
      ).href;
      return mod;
    });
    pdfjsPromise = loading;
    void loading.catch(() => {
      if (pdfjsPromise === loading) pdfjsPromise = null;
    });
  }
  return pdfjsPromise;
}

export interface OpenPdfOptions {
  /** Copy typed-array input before giving ownership of its buffer to pdf.js. */
  copyData?: boolean;
  /** Avoid creating a loading task if the owning component was already cleaned up. */
  shouldCancel?: () => boolean;
}

function localPdfResources() {
  return {
    cMapUrl: new URL(`${import.meta.env.BASE_URL}cmaps/`, window.location.origin).href,
    cMapPacked: true,
    standardFontDataUrl: new URL(`${import.meta.env.BASE_URL}standard_fonts/`, window.location.origin).href,
    wasmUrl: new URL(`${import.meta.env.BASE_URL}wasm/`, window.location.origin).href,
  };
}

export function openPdfDocument(
  source: Uint8Array | ArrayBuffer | string,
  options?: OpenPdfOptions & { shouldCancel?: undefined },
): Promise<PDFDocumentLoadingTask>;
export function openPdfDocument(
  source: Uint8Array | ArrayBuffer | string,
  options: OpenPdfOptions & { shouldCancel: () => boolean },
): Promise<PDFDocumentLoadingTask | null>;
export async function openPdfDocument(
  source: Uint8Array | ArrayBuffer | string,
  options: OpenPdfOptions = {},
): Promise<PDFDocumentLoadingTask | null> {
  const pdfjs = await loadPdfjs();
  if (options.shouldCancel?.()) return null;
  const resources = localPdfResources();
  if (typeof source === "string") {
    return pdfjs.getDocument({
      ...resources,
      url: source,
      disableStream: true,
      disableAutoFetch: true,
      rangeChunkSize: 64 * 1024,
    });
  }
  const data = options.copyData
    ? source instanceof Uint8Array ? source.slice() : source.slice(0)
    : source;
  return pdfjs.getDocument({ ...resources, data });
}
