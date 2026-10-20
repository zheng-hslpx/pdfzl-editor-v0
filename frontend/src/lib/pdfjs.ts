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

export async function openPdfDocument(
  source: Uint8Array | string,
): Promise<PDFDocumentLoadingTask> {
  const pdfjs = await loadPdfjs();
  if (typeof source === "string") {
    return pdfjs.getDocument({
      url: source,
      disableStream: true,
      disableAutoFetch: true,
      rangeChunkSize: 64 * 1024,
    });
  }
  return pdfjs.getDocument({ data: source });
}
