/** Shared pdf.js loader (used by PdfViewer and the editor). */
import type { PDFDocumentProxy } from "pdfjs-dist";

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
  data: Uint8Array,
): Promise<PDFDocumentProxy> {
  const pdfjs = await loadPdfjs();
  return pdfjs.getDocument({ data }).promise;
}
