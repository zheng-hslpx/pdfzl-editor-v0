import { useEffect, useRef, useState } from "react";
import { Alert, Loader } from "@mantine/core";

type PdfjsModule = typeof import("pdfjs-dist");

let pdfjsPromise: Promise<PdfjsModule> | null = null;

function loadPdfjs(): Promise<PdfjsModule> {
  pdfjsPromise ??= import("pdfjs-dist").then((mod) => {
    mod.GlobalWorkerOptions.workerSrc = new URL(
      "pdfjs-dist/build/pdf.worker.min.mjs",
      import.meta.url,
    ).href;
    return mod;
  });
  return pdfjsPromise;
}

export interface PdfViewerProps {
  data: Uint8Array;
  /** Render scale (CSS px per PDF pt). */
  scale?: number;
  onPageCount?: (count: number) => void;
}

/**
 * Minimal pdf.js viewer: renders every page as a canvas into a scroll
 * container. The editor (M5) extends this with a positioned text overlay.
 */
export function PdfViewer({ data, scale = 1.2, onPageCount }: PdfViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let doc: { destroy: () => void } | null = null;

    (async () => {
      try {
        const pdfjs = await loadPdfjs();
        const pdfDoc = await pdfjs.getDocument({ data }).promise;
        if (cancelled) {
          pdfDoc.destroy();
          return;
        }
        doc = pdfDoc;
        const container = containerRef.current;
        if (!container) return;
        container.textContent = "";
        onPageCount?.(pdfDoc.numPages);
        setLoading(false);

        for (let pageNo = 1; pageNo <= pdfDoc.numPages; pageNo++) {
          if (cancelled) return;
          const page = await pdfDoc.getPage(pageNo);
          const viewport = page.getViewport({ scale });
          const wrapper = document.createElement("div");
          wrapper.style.position = "relative";
          wrapper.style.marginBottom = "16px";
          wrapper.style.boxShadow = "0 0 6px rgba(0,0,0,0.25)";
          wrapper.style.width = "fit-content";
          const canvas = document.createElement("canvas");
          canvas.width = Math.floor(viewport.width);
          canvas.height = Math.floor(viewport.height);
          canvas.style.display = "block";
          wrapper.appendChild(canvas);
          container.appendChild(wrapper);
          const ctx = canvas.getContext("2d");
          if (ctx) {
            await page.render({ canvas, canvasContext: ctx, viewport }).promise;
          }
          page.cleanup();
        }
      } catch (e) {
        if (!cancelled) {
          setError(String(e));
          setLoading(false);
        }
      }
    })();

    return () => {
      cancelled = true;
      doc?.destroy();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, scale]);

  if (error) {
    return <Alert color="red" title="无法渲染 PDF">{error}</Alert>;
  }

  return (
    <div style={{ position: "relative" }}>
      {loading && <Loader size="sm" />}
      <div
        ref={containerRef}
        style={{ display: loading ? "none" : "block", overflowX: "auto" }}
      />
    </div>
  );
}
