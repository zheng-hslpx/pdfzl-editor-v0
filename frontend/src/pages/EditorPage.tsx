import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  ColorInput,
  FileButton,
  Group,
  NumberInput,
  Paper,
  SegmentedControl,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import {
  IconArrowLeft,
  IconDeviceFloppy,
  IconDownload,
  IconZoomIn,
  IconZoomOut,
} from "@tabler/icons-react";
import { openPdfDocument } from "../lib/pdfjs";
import { useFiles } from "../contexts/FileContext";
import * as editorApi from "../api/editor";
import { extractError } from "../api/client";
import { takePendingEditorFile } from "../lib/pendingFile";
import type { PageModel, TextLine } from "../api/editor";

type EditMode = "select" | "text" | "image" | "redact" | "rotate" | "delete" | "move";

const MODES: { value: EditMode; label: string }[] = [
  { value: "select", label: "选择/改字" },
  { value: "text", label: "加文字" },
  { value: "image", label: "加图片" },
  { value: "redact", label: "涂抹" },
  { value: "rotate", label: "旋转" },
  { value: "delete", label: "删页" },
  { value: "move", label: "移动页" },
];

const FONT_STYLES: Record<string, { family: string; weight?: number; style?: string }> = {
  helv: { family: "Helvetica, Arial, sans-serif" },
  hebo: { family: "Helvetica, Arial, sans-serif", weight: 700 },
  heit: { family: "Helvetica, Arial, sans-serif", style: "italic" },
  "china-s": { family: '"Microsoft YaHei", "PingFang SC", sans-serif' },
};

interface LineWithPage extends TextLine {
  page: number;
}

export function EditorPage() {
  const { files } = useFiles();

  // session state
  const [docId, setDocId] = useState<string | null>(null);
  const [originalName, setOriginalName] = useState("document.pdf");
  const [pages, setPages] = useState<PageModel[]>([]);
  const [pdfBytes, setPdfBytes] = useState<Uint8Array | null>(null);
  const [pdfDoc, setPdfDoc] = useState<Awaited<ReturnType<typeof openPdfDocument>> | null>(null);

  // editing state
  const [scale, setScale] = useState(1.2);
  const [mode, setMode] = useState<EditMode>("select");
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [findQuery, setFindQuery] = useState("");

  // transient interaction state
  const [moveSource, setMoveSource] = useState<number | null>(null);
  const [addTextAt, setAddTextAt] = useState<{ page: number; x: number; y: number } | null>(null);

  const imageInputRef = useRef<HTMLInputElement>(null);
  const pendingImagePage = useRef<{ page: number; x: number; y: number } | null>(null);

  const notify = useCallback((msg: string) => {
    setToast(msg);
    window.setTimeout(() => setToast((t) => (t === msg ? null : t)), 2600);
  }, []);

  const allLines: LineWithPage[] = useMemo(
    () => pages.flatMap((p) => p.lines.map((l) => ({ ...l, page: p.page }))),
    [pages],
  );

  const findMatches = useMemo(() => {
    const q = findQuery.trim().toLowerCase();
    if (!q) return new Set<string>();
    return new Set(allLines.filter((l) => l.text.toLowerCase().includes(q)).map((l) => l.id));
  }, [findQuery, allLines]);

  // load pdf.js document whenever bytes change
  useEffect(() => {
    if (!pdfBytes) {
      setPdfDoc(null);
      return;
    }
    let cancelled = false;
    let doc: { destroy: () => void } | null = null;
    openPdfDocument(pdfBytes).then((d) => {
      if (cancelled) {
        d.destroy();
        return;
      }
      doc = d;
      setPdfDoc(d);
    });
    return () => {
      cancelled = true;
      doc?.destroy();
    };
  }, [pdfBytes]);

  // refresh rendered bytes from the session (after server-side mutations)
  const refreshBytes = useCallback(async (id: string) => {
    setPdfBytes(await editorApi.fetchSessionBytes(id));
  }, []);

  const applyResult = useCallback(
    async (result: editorApi.EditorResult, id: string) => {
      setPages(result.textModel);
      await refreshBytes(id);
    },
    [refreshBytes],
  );

  const openFile = useCallback(
    async (file: File) => {
      setBusy(true);
      try {
        const result = await editorApi.openEditor(file);
        setDocId(result.docId);
        setOriginalName(result.originalName);
        setPages(result.textModel);
        setEdits({});
        setDirty(false);
        // fetch rendered bytes
        setPdfBytes(await editorApi.fetchSessionBytes(result.docId));
        notify(`已打开 ${result.originalName}（${result.pageCount} 页）`);
      } catch (err) {
        notify(await extractError(err));
      } finally {
        setBusy(false);
      }
    },
    [notify],
  );

  // pick up a file handed over from a tool result ("在编辑器中打开")
  const pickedUp = useRef(false);
  useEffect(() => {
    if (pickedUp.current) return;
    const pending = takePendingEditorFile();
    if (pending) {
      pickedUp.current = true;
      void openFile(pending);
    }
  }, [openFile]);

  // ------------------------------------------------------------------
  // save / save-as
  // ------------------------------------------------------------------

  const handleSave = useCallback(async () => {
    if (!docId) return;
    const changed = Object.entries(edits).filter(([id, text]) => {
      const line = allLines.find((l) => l.id === id);
      return line && line.text !== text;
    });
    if (changed.length === 0) {
      notify("没有需要保存的修改");
      return;
    }
    setBusy(true);
    try {
      const payload = changed.map(([id, text]) => {
        const line = allLines.find((l) => l.id === id)!;
        return {
          page: line.page,
          bbox: line.bbox,
          original: line.text,
          text,
          fontSize: line.fontSize,
        };
      });
      const result = await editorApi.applyEdits(docId, payload);
      setPages(result.textModel);
      setEdits({});
      setDirty(false);
      await refreshBytes(docId);
      notify(`已保存 ${changed.length} 处修改`);
    } catch (err) {
      notify(await extractError(err));
    } finally {
      setBusy(false);
    }
  }, [docId, edits, allLines, notify, refreshBytes]);

  const handleSaveAs = useCallback(async () => {
    if (!docId) return;
    const name = window.prompt("另存为文件名：", originalName);
    if (!name) return;
    try {
      await editorApi.saveEditedAs(docId, name);
      notify(`已导出 ${name}`);
    } catch (err) {
      notify(await extractError(err));
    }
  }, [docId, originalName, notify]);

  // warn on tab close with unsaved edits
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (dirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);

  // ------------------------------------------------------------------
  // page-level actions
  // ------------------------------------------------------------------

  const onPageClick = useCallback(
    async (pageNo: number, pt: { x: number; y: number }) => {
      if (!docId) return;
      if (mode === "rotate") {
        setBusy(true);
        try {
          await applyResult(await editorApi.rotatePage(docId, pageNo, 90), docId);
          notify(`第 ${pageNo + 1} 页已旋转 90°`);
        } catch (err) {
          notify(await extractError(err));
        } finally {
          setBusy(false);
        }
      } else if (mode === "delete") {
        if (!window.confirm(`确定删除第 ${pageNo + 1} 页？`)) return;
        setBusy(true);
        try {
          await applyResult(await editorApi.deletePages(docId, [pageNo]), docId);
          notify(`已删除第 ${pageNo + 1} 页`);
        } catch (err) {
          notify(await extractError(err));
        } finally {
          setBusy(false);
        }
      } else if (mode === "move") {
        if (moveSource === null) {
          setMoveSource(pageNo);
          notify(`已选第 ${pageNo + 1} 页为源，点击目标页完成移动`);
        } else if (moveSource !== pageNo) {
          setBusy(true);
          try {
            await applyResult(await editorApi.movePage(docId, moveSource, pageNo), docId);
            notify(`第 ${moveSource + 1} 页已移动到第 ${pageNo + 1} 页`);
          } catch (err) {
            notify(await extractError(err));
          } finally {
            setMoveSource(null);
            setBusy(false);
          }
        } else {
          setMoveSource(null);
        }
      } else if (mode === "text") {
        setAddTextAt({ page: pageNo, x: pt.x, y: pt.y });
      } else if (mode === "image") {
        pendingImagePage.current = { page: pageNo, x: pt.x, y: pt.y };
        imageInputRef.current?.click();
      }
    },
    [docId, mode, moveSource, notify, applyResult],
  );

  const onImagePicked = useCallback(
    async (file: File | null) => {
      const pos = pendingImagePage.current;
      if (!file || !pos || !docId) return;
      setBusy(true);
      try {
        await applyResult(
          await editorApi.addImage(docId, { image: file, ...pos, w: 120, h: 80 }),
          docId,
        );
        notify("图片已添加");
      } catch (err) {
        notify(await extractError(err));
      } finally {
        setBusy(false);
        pendingImagePage.current = null;
      }
    },
    [docId, notify, applyResult],
  );

  const onRedactRect = useCallback(
    async (pageNo: number, rect: number[]) => {
      if (!docId) return;
      setBusy(true);
      try {
        await applyResult(
          await editorApi.applyEdits(docId, [
            { page: pageNo, bbox: rect, original: "", text: "" },
          ]),
          docId,
        );
        notify("已涂抹所选区域");
      } catch (err) {
        notify(await extractError(err));
      } finally {
        setBusy(false);
      }
    },
    [docId, notify, applyResult],
  );

  // ------------------------------------------------------------------
  // render
  // ------------------------------------------------------------------

  if (!docId) {
    return (
      <Stack align="center" gap="lg" py="xl">
        <Title order={3}>可视化编辑器</Title>
        <Text c="dimmed" ta="center" maw={480}>
          打开 PDF 后可直接修改文本内容：点击文字行即可编辑，支持添加文字/图片、涂抹、
          旋转/删除/移动页面，以及保存与另存为。
        </Text>
        <FileButton onChange={(f) => f && openFile(f)} accept=".pdf,application/pdf">
          {(props) => (
            <Button size="md" loading={busy} {...props}>
              打开 PDF 文件
            </Button>
          )}
        </FileButton>
        {files.length > 0 && (
          <Stack gap={4}>
            <Text size="sm" c="dimmed">
              或打开已选文件：
            </Text>
            {files.slice(0, 5).map((f) => (
              <Button
                key={f.id}
                variant="light"
                size="xs"
                onClick={() => openFile(f.file)}
              >
                {f.name}
              </Button>
            ))}
          </Stack>
        )}
        {toast && (
          <Alert color="blue" w={420}>
            {toast}
          </Alert>
        )}
      </Stack>
    );
  }

  return (
    <Stack gap="sm">
      {/* toolbar */}
      <Paper withBorder p="xs" radius="md" style={{ position: "sticky", top: 0, zIndex: 10 }}>
        <Group justify="space-between" wrap="wrap" gap="xs">
          <Group gap="xs">
            <ActionIcon
              variant="subtle"
              onClick={() => {
                setDocId(null);
                setPdfBytes(null);
                setPages([]);
              }}
              title="关闭文件"
            >
              <IconArrowLeft size={18} />
            </ActionIcon>
            <Text fw={600} size="sm">
              {originalName}
            </Text>
            {dirty && <Badge color="orange" variant="light">未保存</Badge>}
            <Text size="xs" c="dimmed">
              {pages.length} 页
            </Text>
          </Group>

          <SegmentedControl
            size="xs"
            data={MODES}
            value={mode}
            onChange={(v) => {
              setMode(v as EditMode);
              setMoveSource(null);
            }}
          />

          <Group gap="xs">
            <TextInput
              size="xs"
              placeholder="查找文本…"
              value={findQuery}
              onChange={(e) => setFindQuery(e.currentTarget.value)}
              style={{ width: 130 }}
            />
            {findQuery && (
              <Text size="xs" c="dimmed">
                {findMatches.size} 处
              </Text>
            )}
            <ActionIcon
              variant="default"
              onClick={() => setScale((s) => Math.max(0.4, +(s - 0.2).toFixed(2)))}
              title="缩小"
            >
              <IconZoomOut size={16} />
            </ActionIcon>
            <Text size="xs" w={36} ta="center">
              {Math.round(scale * 100)}%
            </Text>
            <ActionIcon
              variant="default"
              onClick={() => setScale((s) => Math.min(3, +(s + 0.2).toFixed(2)))}
              title="放大"
            >
              <IconZoomIn size={16} />
            </ActionIcon>
            <Button
              size="xs"
              leftSection={<IconDeviceFloppy size={14} />}
              loading={busy}
              onClick={handleSave}
            >
              保存
            </Button>
            <Button
              size="xs"
              variant="default"
              leftSection={<IconDownload size={14} />}
              onClick={handleSaveAs}
            >
              另存为
            </Button>
          </Group>
        </Group>
      </Paper>

      {toast && (
        <Alert color="blue" w="100%">
          {toast}
        </Alert>
      )}

      <input
        ref={imageInputRef}
        type="file"
        accept="image/*"
        style={{ display: "none" }}
        onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          void onImagePicked(f ?? null);
        }}
      />

      {/* pages */}
      <Stack align="center" gap="md" pb="xl">
        {pages.map((p) => (
          <EditorCanvas
            key={p.page}
            page={p}
            scale={scale}
            pdfDoc={pdfDoc}
            mode={mode}
            edits={edits}
            findMatches={findMatches}
            moveSource={moveSource}
            addTextAt={addTextAt?.page === p.page ? addTextAt : null}
            onLineChange={(id, text) => {
              setEdits((prev) => ({ ...prev, [id]: text }));
              setDirty(true);
            }}
            onPageClick={onPageClick}
            onRedactRect={onRedactRect}
            onAddTextConfirm={async (text, fontSize, color) => {
              if (!addTextAt || !docId) return;
              setBusy(true);
              try {
                await applyResult(
                  await editorApi.addText(docId, {
                    page: addTextAt.page,
                    x: addTextAt.x,
                    y: addTextAt.y + fontSize,
                    text,
                    fontSize,
                    color,
                  }),
                  docId,
                );
                notify("文字已添加");
              } catch (err) {
                notify(await extractError(err));
              } finally {
                setBusy(false);
                setAddTextAt(null);
              }
            }}
            onAddTextCancel={() => setAddTextAt(null)}
          />
        ))}
      </Stack>
    </Stack>
  );
}

// ---------------------------------------------------------------------------
// single page canvas + text overlays
// ---------------------------------------------------------------------------

interface EditorCanvasProps {
  page: PageModel;
  scale: number;
  pdfDoc: { getPage: (n: number) => Promise<unknown> } | null;
  mode: EditMode;
  edits: Record<string, string>;
  findMatches: Set<string>;
  moveSource: number | null;
  addTextAt: { page: number; x: number; y: number } | null;
  onLineChange: (id: string, text: string) => void;
  onPageClick: (pageNo: number, pt: { x: number; y: number }) => void;
  onRedactRect: (pageNo: number, rect: number[]) => void;
  onAddTextConfirm: (text: string, fontSize: number, color: string) => void;
  onAddTextCancel: () => void;
}

function EditorCanvas(props: EditorCanvasProps) {
  const {
    page, scale, pdfDoc, mode, edits, findMatches, moveSource, addTextAt,
    onLineChange, onPageClick, onRedactRect, onAddTextConfirm, onAddTextCancel,
  } = props;
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [dragStart, setDragStart] = useState<{ x: number; y: number } | null>(null);
  const [dragRect, setDragRect] = useState<{ x: number; y: number; w: number; h: number } | null>(null);

  const widthCss = page.width * scale;
  const heightCss = page.height * scale;

  // render the page canvas
  useEffect(() => {
    if (!pdfDoc || !canvasRef.current) return;
    let cancelled = false;
    let renderTask: { promise: Promise<void>; cancel: () => void } | null = null;
    (async () => {
      const p = (await pdfDoc.getPage(page.page + 1)) as {
        getViewport: (o: { scale: number; rotation: number }) => { width: number; height: number };
        render: (o: Record<string, unknown>) => { promise: Promise<void>; cancel: () => void };
        cleanup: () => void;
      };
      if (cancelled) {
        p.cleanup();
        return;
      }
      const dpr = window.devicePixelRatio || 1;
      const viewport = p.getViewport({ scale: scale * dpr, rotation: page.rotation });
      const canvas = canvasRef.current;
      if (!canvas) return;
      canvas.width = Math.floor(viewport.width);
      canvas.height = Math.floor(viewport.height);
      const ctx = canvas.getContext("2d");
      if (ctx) {
        renderTask = p.render({ canvas, canvasContext: ctx, viewport });
        await renderTask.promise;
      }
      p.cleanup();
    })().catch(() => undefined);
    return () => {
      cancelled = true;
      renderTask?.cancel();
    };
  }, [pdfDoc, page.page, page.rotation, scale]);

  const ptFromEvent = (clientX: number, clientY: number) => {
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect) return { x: 0, y: 0 };
    return {
      x: (clientX - rect.left) / scale,
      y: (clientY - rect.top) / scale,
    };
  };

  const overlayPointerEvents = mode === "select" ? "auto" : "none";

  return (
    <div style={{ position: "relative" }}>
      <div
        ref={containerRef}
        style={{
          position: "relative",
          width: widthCss,
          height: heightCss,
          boxShadow: "0 0 8px rgba(0,0,0,0.25)",
          cursor: mode === "select" ? "default" : "crosshair",
          outline: moveSource === page.page ? "3px solid #339af0" : undefined,
        }}
        onMouseDown={(e) => {
          if (mode !== "redact") return;
          const pt = ptFromEvent(e.clientX, e.clientY);
          setDragStart(pt);
          setDragRect({ x: pt.x, y: pt.y, w: 0, h: 0 });
        }}
        onMouseMove={(e) => {
          if (mode !== "redact" || !dragStart) return;
          const pt = ptFromEvent(e.clientX, e.clientY);
          setDragRect({
            x: Math.min(dragStart.x, pt.x),
            y: Math.min(dragStart.y, pt.y),
            w: Math.abs(pt.x - dragStart.x),
            h: Math.abs(pt.y - dragStart.y),
          });
        }}
        onMouseUp={() => {
          if (mode !== "redact" || !dragRect) return;
          if (dragRect.w > 3 && dragRect.h > 3) {
            onRedactRect(page.page, [
              dragRect.x, dragRect.y, dragRect.x + dragRect.w, dragRect.y + dragRect.h,
            ]);
          }
          setDragStart(null);
          setDragRect(null);
        }}
        onClick={(e) => {
          if (mode === "select" || mode === "redact") return;
          const pt = ptFromEvent(e.clientX, e.clientY);
          onPageClick(page.page, pt);
        }}
      >
        <canvas
          ref={canvasRef}
          style={{ position: "absolute", left: 0, top: 0, width: widthCss, height: heightCss }}
        />

        {/* text overlays */}
        <div style={{ pointerEvents: overlayPointerEvents }}>
          {page.lines.map((line) => {
            const [x0, y0, x1, y1] = line.bbox;
            const value = edits[line.id] ?? line.text;
            const fontStyle = FONT_STYLES[line.font] ?? FONT_STYLES.helv!;
            const height = Math.max((y1 - y0) * scale, line.fontSize * scale * 1.35);
            return (
              <textarea
                key={line.id}
                value={value}
                readOnly={mode !== "select" || page.rotation !== 0}
                onChange={(e) => onLineChange(line.id, e.target.value)}
                title={page.rotation !== 0 ? "旋转页暂不支持编辑，请先用旋转工具归一化" : undefined}
                style={{
                  position: "absolute",
                  left: x0 * scale,
                  top: y0 * scale,
                  width: (x1 - x0) * scale,
                  height,
                  fontSize: line.fontSize * scale,
                  lineHeight: 1.15,
                  color: line.color,
                  background:
                    findMatches.has(line.id) ? "rgba(255, 235, 59, 0.45)" : "transparent",
                  border: "none",
                  outline: "none",
                  resize: "none",
                  overflow: "hidden",
                  padding: 0,
                  margin: 0,
                  fontFamily: fontStyle.family,
                  fontWeight: fontStyle.weight,
                  fontStyle: fontStyle.style,
                  cursor: mode === "select" && page.rotation === 0 ? "text" : "default",
                }}
              />
            );
          })}
        </div>

        {/* redaction drag preview */}
        {dragRect && dragRect.w > 0 && (
          <div
            style={{
              position: "absolute",
              left: dragRect.x * scale,
              top: dragRect.y * scale,
              width: dragRect.w * scale,
              height: dragRect.h * scale,
              background: "rgba(255, 0, 0, 0.25)",
              border: "1px dashed red",
              pointerEvents: "none",
            }}
          />
        )}

        {/* add-text popover */}
        {addTextAt && (
          <AddTextPopover
            x={addTextAt.x * scale}
            y={addTextAt.y * scale}
            onConfirm={onAddTextConfirm}
            onCancel={onAddTextCancel}
          />
        )}
      </div>
      <Text size="xs" c="dimmed" ta="center" mt={4}>
        第 {page.page + 1} 页
        {page.rotation !== 0 && "（已旋转，只读）"}
      </Text>
    </div>
  );
}

function AddTextPopover({
  x, y, onConfirm, onCancel,
}: {
  x: number;
  y: number;
  onConfirm: (text: string, fontSize: number, color: string) => void;
  onCancel: () => void;
}) {
  const [text, setText] = useState("");
  const [fontSize, setFontSize] = useState<number | string>(14);
  const [color, setColor] = useState("#000000");

  return (
    <Paper
      withBorder
      shadow="md"
      p="xs"
      radius="md"
      style={{
        position: "absolute",
        left: Math.max(0, x),
        top: y + 24,
        zIndex: 20,
        width: 220,
        background: "white",
      }}
      onClick={(e) => e.stopPropagation()}
    >
      <Stack gap={6}>
        <TextInput
          size="xs"
          placeholder="输入文字…"
          value={text}
          onChange={(e) => setText(e.currentTarget.value)}
          autoFocus
        />
        <Group gap={6}>
          <NumberInput
            size="xs"
            value={fontSize}
            onChange={setFontSize}
            min={6}
            max={72}
            w={80}
          />
          <ColorInput size="xs" value={color} onChange={setColor} format="hex" w={110} />
        </Group>
        <Group gap={6} justify="flex-end">
          <Button size="compact-xs" variant="default" onClick={onCancel}>
            取消
          </Button>
          <Button
            size="compact-xs"
            disabled={!text.trim()}
            onClick={() => onConfirm(text, Number(fontSize) || 14, color)}
          >
            添加
          </Button>
        </Group>
      </Stack>
    </Paper>
  );
}
