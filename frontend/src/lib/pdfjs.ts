/** Shared pdf.js loader (used by PdfViewer and the editor). */
import type { PDFDocumentLoadingTask } from "pdfjs-dist";

type PdfjsModule = typeof import("pdfjs-dist");

let pdfjsPromise: Promise<PdfjsModule> | null = null;

export function loadPdfjs(): Promise<PdfjsModule> {
  pdfjsPromise ??= import("pdfjs-dist").then((mod) => {
    mod.GlobalWorkerOptions.workerSrc = new URL(
      "pdfjs-dist/build/pdf.worker.min.mjs",
      import.meta.url,
    ).href;
    return mod;
  });
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
