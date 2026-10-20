import { useEffect, useRef, useState } from "react";
import { Alert, Loader, Text } from "@mantine/core";

type PdfjsModule = typeof import("pdfjs-dist");
type PdfDocument = Awaited<ReturnType<PdfjsModule["getDocument"]>["promise"]>;
type PdfLoadingTask = ReturnType<PdfjsModule["getDocument"]>;

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

/** PDF.js preview that holds canvases only for visible pages and their neighbors. */
export function PdfViewer({ data, scale = 1.2, onPageCount }: PdfViewerProps) {
  const [pdfDoc, setPdfDoc] = useState<PdfDocument | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let loadingTask: PdfLoadingTask | null = null;
    setLoading(true);
    setError(null);
    setPdfDoc(null);

    void (async () => {
      try {
        const pdfjs = await loadPdfjs();
        const task = pdfjs.getDocument({ data });
        loadingTask = task;
        if (cancelled) {
          await task.destroy();
          return;
        }
        const loaded = await task.promise;
        if (cancelled) return;
        setPdfDoc(loaded);
        onPageCount?.(loaded.numPages);
        setLoading(false);
      } catch (reason) {
        if (!cancelled) {
          setError(String(reason));
          setLoading(false);
        }
      }
    })();

    return () => {
      cancelled = true;
      if (loadingTask) void loadingTask.destroy().catch(() => undefined);
    };
  }, [data, onPageCount]);

  if (error) return <Alert color="red" title="无法渲染 PDF">{error}</Alert>;
  if (loading || !pdfDoc) return <Loader size="sm" />;

  return (
    <div style={{ display: "grid", justifyItems: "center", gap: 16 }}>
      {Array.from({ length: pdfDoc.numPages }, (_, index) => (
        <PdfPreviewPage key={index} pdfDoc={pdfDoc} pageNo={index + 1} scale={scale} />
      ))}
    </div>
  );
}

function PdfPreviewPage({
  pdfDoc,
  pageNo,
  scale,
}: {
  pdfDoc: PdfDocument;
  pageNo: number;
  scale: number;
}) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [nearViewport, setNearViewport] = useState(false);
  const [rendered, setRendered] = useState(false);
  const [limited, setLimited] = useState(false);

  useEffect(() => {
    const node = wrapperRef.current;
    if (!node) return;
    if (!("IntersectionObserver" in window)) {
      setNearViewport(true);
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => setNearViewport(entry?.isIntersecting ?? false),
      { rootMargin: "900px 0px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!nearViewport || !canvasRef.current) {
      setRendered(false);
      const canvas = canvasRef.current;
      if (canvas) {
        canvas.width = 0;
        canvas.height = 0;
      }
      return;
    }
    let cancelled = false;
    let renderTask: { promise: Promise<void>; cancel: () => void } | null = null;
    void (async () => {
      const page = await pdfDoc.getPage(pageNo);
      if (cancelled) {
        page.cleanup();
        return;
      }
      const dpr = window.devicePixelRatio || 1;
      let renderScale = scale * dpr;
      const baseViewport = page.getViewport({ scale: renderScale });
      const pixels = baseViewport.width * baseViewport.height;
      if (pixels > 24_000_000) {
        renderScale *= Math.sqrt(24_000_000 / pixels);
        setLimited(true);
      } else {
        setLimited(false);
      }
      const viewport = page.getViewport({ scale: renderScale });
      const canvas = canvasRef.current;
      if (!canvas) {
        page.cleanup();
        return;
      }
      canvas.width = Math.ceil(viewport.width);
      canvas.height = Math.ceil(viewport.height);
      const context = canvas.getContext("2d");
      if (context) {
        renderTask = page.render({ canvas, canvasContext: context, viewport });
        await renderTask.promise;
      }
      page.cleanup();
      if (!cancelled) setRendered(true);
    })().catch((reason) => {
      if (!cancelled) console.error("PDF preview page render failed", pageNo, reason);
    });
    return () => {
      cancelled = true;
      renderTask?.cancel();
    };
  }, [nearViewport, pageNo, pdfDoc, scale]);

  return (
    <div
      ref={wrapperRef}
      style={{
        position: "relative",
        width: "min(100%, 900px)",
        minHeight: Math.max(240, 842 * scale),
        boxShadow: "0 0 6px rgba(0,0,0,0.18)",
        background: "white",
      }}
    >
      <canvas
        ref={canvasRef}
        style={{
          display: rendered ? "block" : "none",
          width: "100%",
          height: "auto",
        }}
      />
      {!rendered && nearViewport && <Loader size="sm" />}
      {limited && rendered && (
        <Text size="xs" c="dimmed" style={{ position: "absolute", right: 8, bottom: 4 }}>
          已降低预览分辨率以控制内存
        </Text>
      )}
      <Text size="xs" c="dimmed" ta="center">第 {pageNo} 页</Text>
    </div>
  );
}
