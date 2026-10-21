import { useEffect, useRef, useState } from "react";
import { Alert, Button, Loader, Stack, Text } from "@mantine/core";
import type { PDFDocumentLoadingTask, PDFDocumentProxy } from "pdfjs-dist";
import { openPdfDocument, PDF_OPEN_TIMEOUT_MS, withPdfOpenDeadline } from "../lib/pdfjs";

export interface PdfViewerProps {
  data: Blob | Uint8Array | ArrayBuffer;
  /** Render scale (CSS px per PDF pt). */
  scale?: number;
  onPageCount?: (count: number) => void;
}

/** PDF.js preview that holds canvases only for visible pages and their neighbors. */
export function PdfViewer({ data, scale = 1.2, onPageCount }: PdfViewerProps) {
  const [pdfDoc, setPdfDoc] = useState<PDFDocumentProxy | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loadRetry, setLoadRetry] = useState(0);
  const onPageCountRef = useRef(onPageCount);

  useEffect(() => {
    onPageCountRef.current = onPageCount;
  }, [onPageCount]);

  useEffect(() => {
    let cancelled = false;
    const abortController = new AbortController();
    let loadingTask: PDFDocumentLoadingTask | null = null;
    let objectUrl: string | null = null;
    let cleanupPromise: Promise<void> | null = null;
    const releaseObjectUrl = () => {
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
        objectUrl = null;
      }
    };
    const cleanup = () => {
      if (cleanupPromise) return cleanupPromise;
      const task = loadingTask;
      loadingTask = null;
      cleanupPromise = (async () => {
        if (task) await task.destroy().catch(() => undefined);
        releaseObjectUrl();
      })();
      return cleanupPromise;
    };
    setLoading(true);
    setError(null);
    setPdfDoc(null);
    const deadlineAt = Date.now() + PDF_OPEN_TIMEOUT_MS;

    void (async () => {
      try {
        const source = data instanceof Blob ? (objectUrl = URL.createObjectURL(data)) : data;
        const task = await withPdfOpenDeadline(
          openPdfDocument(source, {
            copyData: true,
            shouldCancel: () => cancelled || abortController.signal.aborted,
          }),
          deadlineAt,
          abortController.signal,
        );
        if (!task) {
          releaseObjectUrl();
          return;
        }
        if (cancelled) {
          await task.destroy().catch(() => undefined);
          releaseObjectUrl();
          return;
        }
        loadingTask = task;
        const loaded = await withPdfOpenDeadline(task.promise, deadlineAt, abortController.signal);
        if (cancelled) return;
        setPdfDoc(loaded);
        onPageCountRef.current?.(loaded.numPages);
        setLoading(false);
      } catch (reason) {
        if (cancelled) return;
        abortController.abort();
        await cleanup();
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : String(reason));
          setLoading(false);
        }
      }
    })();

    return () => {
      cancelled = true;
      abortController.abort();
      void cleanup();
    };
  }, [data, loadRetry]);

  if (error) {
    return (
      <Alert color="red" title="PDF 预览失败">
        <Stack gap="xs">
          <Text size="sm">{error}</Text>
          <Button size="xs" variant="light" onClick={() => setLoadRetry((attempt) => attempt + 1)}>
            重试预览
          </Button>
        </Stack>
      </Alert>
    );
  }
  if (loading || !pdfDoc) return <Loader size="sm" aria-label="正在加载 PDF 预览" />;

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
  pdfDoc: PDFDocumentProxy;
  pageNo: number;
  scale: number;
}) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [nearViewport, setNearViewport] = useState(false);
  const [rendered, setRendered] = useState(false);
  const [limited, setLimited] = useState(false);
  const [renderError, setRenderError] = useState<string | null>(null);
  const [renderRetry, setRenderRetry] = useState(0);

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
    let page: Awaited<ReturnType<PDFDocumentProxy["getPage"]>> | null = null;
    setRendered(false);
    setRenderError(null);
    void (async () => {
      try {
        page = await pdfDoc.getPage(pageNo);
        if (cancelled) return;
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
        if (!canvas) return;
        canvas.width = Math.ceil(viewport.width);
        canvas.height = Math.ceil(viewport.height);
        const context = canvas.getContext("2d");
        if (!context) throw new Error("浏览器无法创建 PDF 页面画布");
        renderTask = page.render({ canvas, canvasContext: context, viewport });
        await renderTask.promise;
        if (!cancelled) setRendered(true);
      } catch (reason) {
        if (!cancelled) setRenderError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        try {
          page?.cleanup();
        } catch {
          // A failed render must not prevent the page from showing its retry state.
        }
      }
    })();
    return () => {
      cancelled = true;
      renderTask?.cancel();
    };
  }, [nearViewport, pageNo, pdfDoc, renderRetry, scale]);

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
      {renderError && (
        <Alert color="red" title={`第 ${pageNo} 页渲染失败`} style={{ position: "absolute", inset: 8, zIndex: 1 }}>
          <Stack gap="xs">
            <Text size="xs">{renderError}</Text>
            <Button size="xs" variant="light" onClick={() => setRenderRetry((attempt) => attempt + 1)}>
              重试此页
            </Button>
          </Stack>
        </Alert>
      )}
      {!renderError && !rendered && nearViewport && <Loader size="sm" />}
      {limited && rendered && (
        <Text size="xs" c="dimmed" style={{ position: "absolute", right: 8, bottom: 4 }}>
          已降低预览分辨率以控制内存
        </Text>
      )}
      <Text size="xs" c="dimmed" ta="center">第 {pageNo} 页</Text>
    </div>
  );
}
