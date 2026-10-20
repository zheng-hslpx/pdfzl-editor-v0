import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  ColorInput,
  FileButton,
  Group,
  Modal,
  NumberInput,
  Paper,
  Progress,
  SegmentedControl,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import {
  IconArrowLeft,
  IconArrowBackUp,
  IconArrowForwardUp,
  IconDeviceFloppy,
  IconDownload,
  IconZoomIn,
  IconZoomOut,
} from "@tabler/icons-react";
import { useNavigate } from "react-router-dom";
import { openPdfDocument } from "../lib/pdfjs";
import { formatSize, useFiles } from "../contexts/FileContext";
import * as editorApi from "../api/editor";
import {
  extractError,
  getErrorCode,
  type HistoryCapacityPlan,
  type HistoryPolicy,
  type JobOptions,
  type JobProgress,
} from "../api/client";
import { takePendingEditorFile } from "../lib/pendingFile";
import type { PageMeta, PageModel, TextLine } from "../api/editor";
import {
  clearAllDrafts,
  createDraftId,
  deleteDraft,
  DRAFT_TTL_MS,
  getDraft,
  listDrafts,
  saveDraft,
  type CachedDraft,
  type CachedDraftSummary,
  type DraftAnchor,
} from "../lib/draftStore";

type EditMode = "select" | "text" | "image" | "redact" | "rotate" | "delete" | "move";

const MODES: { value: EditMode; label: string }[] = [
  { value: "select", label: "选择/改字" },
  { value: "text", label: "加文字" },
  { value: "image", label: "加图片" },
  { value: "redact", label: "视觉遮挡" },
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

function createPageSlots(pageCount: number, loaded: PageMeta[]): PageMeta[] {
  const known = new Map(loaded.map((page) => [page.page, page]));
  return Array.from({ length: pageCount }, (_, page) =>
    known.get(page) ?? {
      page,
      pageId: `pending-${page}`,
      width: 595,
      height: 842,
      rotation: 0,
    },
  );
}

export function EditorPage() {
  const { files } = useFiles();
  const navigate = useNavigate();

  // session state
  const [docId, setDocId] = useState<string | null>(null);
  const docIdRef = useRef<string | null>(null);
  const revisionRef = useRef(0);
  const updateRevision = (value: number) => {
    revisionRef.current = value;
  };
  const [originalName, setOriginalName] = useState("document.pdf");
  const [pages, setPages] = useState<PageMeta[]>([]);
  const pagesRef = useRef<PageMeta[]>([]);
  const pageListRef = useRef<HTMLDivElement>(null);
  const [scrollMetrics, setScrollMetrics] = useState({ scrollY: 0, viewportHeight: 800, listTop: 0 });
  const [pageModels, setPageModels] = useState<Record<string, PageModel>>({});
  const pageModelsRef = useRef<Record<string, PageModel>>({});
  const pageTextRequests = useRef(new Map<string, Promise<void>>());
  const pageInfoRequests = useRef(new Map<string, Promise<void>>());
  const visiblePages = useRef(new Set<number>());
  const [pdfReloadKey, setPdfReloadKey] = useState(0);
  const [pdfDoc, setPdfDoc] = useState<Awaited<ReturnType<typeof openPdfDocument>> | null>(null);

  // editing state
  const [scale, setScale] = useState(1.2);
  const [mode, setMode] = useState<EditMode>("select");
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [dirty, setDirty] = useState(false);
  const [hasServerChanges, setHasServerChanges] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pendingAction, setPendingAction] = useState<{
    run: () => Promise<void>;
    requireExport: boolean;
  } | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [findQuery, setFindQuery] = useState("");
  const [jobProgress, setJobProgress] = useState<JobProgress | null>(null);
  const jobController = useRef<AbortController | null>(null);
  const retainAllTextModels = useRef(false);
  const [historyState, setHistoryState] = useState<editorApi.HistoryState | null>(null);
  const [historyPrompt, setHistoryPrompt] = useState<HistoryCapacityPlan | null>(null);
  const historyChoice = useRef<((policy: HistoryPolicy | "cancel") => void) | null>(null);
  const draftUndo = useRef<Record<string, string>[]>([]);
  const draftRedo = useRef<Record<string, string>[]>([]);
  const [, setDraftHistoryVersion] = useState(0);
  const [savingDraft, setSavingDraft] = useState(false);
  const [draftCacheEnabled, setDraftCacheEnabled] = useState(false);
  const [draftCacheStatus, setDraftCacheStatus] = useState<"disabled" | "saving" | "saved" | "error">("disabled");
  const [cacheConfirmOpen, setCacheConfirmOpen] = useState(false);
  const [cachedDrafts, setCachedDrafts] = useState<CachedDraftSummary[]>([]);
  const [restoringDraftId, setRestoringDraftId] = useState<string | null>(null);
  const draftCacheId = useRef<string | null>(null);
  const draftCacheCreatedAt = useRef<number | null>(null);
  const draftCacheBlob = useRef<Blob | null>(null);
  const draftCacheNeedsRefresh = useRef(false);
  const draftCacheEnabledRef = useRef(false);
  const draftAnchors = useRef<Record<string, DraftAnchor>>({});
  const draftEditBurst = useRef<{ lineId: string; at: number } | null>(null);
  const cacheWriteTimer = useRef<number | null>(null);
  const localCacheWriteQueue = useRef<Promise<unknown>>(Promise.resolve());

  // transient interaction state
  const [moveSource, setMoveSource] = useState<number | null>(null);
  const [addTextAt, setAddTextAt] = useState<{ page: number; x: number; y: number } | null>(null);

  const imageInputRef = useRef<HTMLInputElement>(null);
  const pendingImagePage = useRef<{ page: number; x: number; y: number; pageId: string } | null>(null);
  const editsRef = useRef<Record<string, string>>({});

  const replacePages = useCallback((next: PageMeta[]) => {
    pagesRef.current = next;
    setPages(next);
  }, []);

  const replacePageModels = useCallback((next: Record<string, PageModel>) => {
    pageModelsRef.current = next;
    setPageModels(next);
  }, []);

  const updatePageModels = useCallback((update: (previous: Record<string, PageModel>) => Record<string, PageModel>) => {
    setPageModels((previous) => {
      const next = update(previous);
      pageModelsRef.current = next;
      return next;
    });
  }, []);

  const notify = useCallback((msg: string) => {
    setToast(msg);
    window.setTimeout(() => setToast((t) => (t === msg ? null : t)), 2600);
  }, []);

  const refreshCachedDraftList = useCallback(async () => {
    try {
      setCachedDrafts(await listDrafts());
    } catch {
      setCachedDrafts([]);
    }
  }, []);

  useEffect(() => {
    void refreshCachedDraftList();
  }, [refreshCachedDraftList]);

  const draftAnchorsForCurrentEdits = useCallback((): DraftAnchor[] => {
    return Object.entries(editsRef.current).flatMap(([lineId, text]) => {
      const anchor = draftAnchors.current[lineId];
      if (!anchor) throw new Error("有文字草稿缺少定位信息，缓存未保存");
      return text !== anchor.original ? [{ ...anchor, lineId, bbox: [...anchor.bbox], text }] : [];
    });
  }, []);

  const persistLocalDraft = useCallback((refreshDocument = false, showError = true): Promise<boolean> => {
    const id = docIdRef.current;
    const cacheId = draftCacheId.current;
    if (!id || !cacheId || !draftCacheEnabledRef.current) return Promise.resolve(false);
    if (refreshDocument) draftCacheNeedsRefresh.current = true;
    const operation = localCacheWriteQueue.current.then(async () => {
      if (
        !draftCacheEnabledRef.current
        || draftCacheId.current !== cacheId
        || docIdRef.current !== id
      ) return false;
      setDraftCacheStatus("saving");
      try {
        const expectedRevision = revisionRef.current;
        let blob = draftCacheBlob.current;
        if (draftCacheNeedsRefresh.current || !blob) blob = await editorApi.fetchSessionPdf(id);
        if (!blob) throw new Error("没有可缓存的文档内容");
        const anchorsByPage = new Map<number, Array<[string, string, DraftAnchor]>>();
        for (const [lineId, text] of Object.entries(editsRef.current)) {
          const anchor = draftAnchors.current[lineId];
          if (!anchor) throw new Error("有文字草稿缺少定位信息，缓存未保存");
          const group = anchorsByPage.get(anchor.page) ?? [];
          group.push([lineId, text, anchor]);
          anchorsByPage.set(anchor.page, group);
        }
        for (const [pageNo, entries] of anchorsByPage) {
          const response = await editorApi.fetchPageText(id, pageNo);
          if (response.revision !== expectedRevision || revisionRef.current !== expectedRevision) {
            throw new Error("文档版本已变化，草稿缓存未保存");
          }
          for (const [lineId, text, anchor] of entries) {
            const line = response.page.lines.find((item) => item.id === (anchor.lineId || lineId))
              ?? response.page.lines
                .map((item) => ({
                  item,
                  distance: item.bbox.reduce((sum, value, index) => sum + Math.abs(value - (anchor.bbox[index] ?? value)), 0),
                }))
                .filter((item) => item.distance <= 4)
                .sort((a, b) => a.distance - b.distance)[0]?.item;
            if (!line) throw new Error("有文字草稿无法重新定位，缓存未保存");
            draftAnchors.current[lineId] = {
              lineId: line.id,
              page: pageNo,
              originalHash: line.originalHash,
              bbox: [...line.bbox],
              original: line.text,
              text,
            };
          }
        }
        const now = Date.now();
        const editsToStore = draftAnchorsForCurrentEdits();
        const draft: CachedDraft = {
          id: cacheId,
          name: originalName,
          pageCount: pagesRef.current.length,
          sizeBytes: blob.size,
          createdAt: draftCacheCreatedAt.current ?? now,
          updatedAt: now,
          expiresAt: now + DRAFT_TTL_MS,
          editCount: editsToStore.length,
          blob,
          edits: editsToStore,
        };
        await saveDraft(draft);
        draftCacheBlob.current = blob;
        draftCacheNeedsRefresh.current = false;
        draftCacheCreatedAt.current = draft.createdAt;
        setDraftCacheStatus("saved");
        void refreshCachedDraftList();
        return true;
      } catch (err) {
        setDraftCacheStatus("error");
        if (showError) {
          const isQuotaError = typeof err === "object" && err !== null && "name" in err
            && (err as { name?: unknown }).name === "QuotaExceededError";
          const detail = isQuotaError
            ? "浏览器空间不足"
            : err instanceof Error ? err.message : String(err);
          notify(`未保存本地缓存：${detail}`);
        }
        return false;
      }
    });
    localCacheWriteQueue.current = operation.then(() => undefined, () => undefined);
    return operation;
  }, [draftAnchorsForCurrentEdits, notify, originalName, refreshCachedDraftList]);

  const enableDraftCache = useCallback(async () => {
    if (!docIdRef.current) return;
    const now = Date.now();
    const id = draftCacheId.current ?? createDraftId();
    draftCacheId.current = id;
    draftCacheCreatedAt.current = now;
    draftCacheEnabledRef.current = true;
    setDraftCacheEnabled(true);
    setDraftCacheStatus("saving");
    setCacheConfirmOpen(false);
    const saved = await persistLocalDraft(true);
    if (saved) notify("本地恢复缓存已开启；缓存未加密，7 天后过期");
  }, [notify, persistLocalDraft]);

  const clearDocumentCache = useCallback(async () => {
    const id = draftCacheId.current;
    if (!id) return;
    draftCacheEnabledRef.current = false;
    draftCacheId.current = null;
    setDraftCacheEnabled(false);
    try {
      await localCacheWriteQueue.current;
      await deleteDraft(id);
      draftCacheCreatedAt.current = null;
      draftCacheBlob.current = null;
      draftCacheNeedsRefresh.current = false;
      setDraftCacheStatus("disabled");
      await refreshCachedDraftList();
      notify("已清除此文档的本地恢复缓存");
    } catch (err) {
      notify(`清除本地缓存失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }, [notify, refreshCachedDraftList]);

  const clearEveryCache = useCallback(async () => {
    draftCacheEnabledRef.current = false;
    draftCacheId.current = null;
    setDraftCacheEnabled(false);
    try {
      await localCacheWriteQueue.current;
      await clearAllDrafts();
      draftCacheCreatedAt.current = null;
      draftCacheBlob.current = null;
      draftCacheNeedsRefresh.current = false;
      draftCacheEnabledRef.current = false;
      setDraftCacheEnabled(false);
      setDraftCacheStatus("disabled");
      setCachedDrafts([]);
      notify("已清除全部本地恢复缓存");
    } catch (err) {
      notify(`清除本地缓存失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }, [notify]);

  const requestHistoryConfirmation = useCallback(
    (plan: HistoryCapacityPlan) => new Promise<HistoryPolicy | "cancel">((resolve) => {
      historyChoice.current = resolve;
      setHistoryPrompt(plan);
    }),
    [],
  );

  const resolveHistoryChoice = useCallback((policy: HistoryPolicy | "cancel") => {
    const resolve = historyChoice.current;
    historyChoice.current = null;
    setHistoryPrompt(null);
    resolve?.(policy);
  }, []);

  const refreshHistory = useCallback(async (id: string) => {
    try {
      const value = await editorApi.fetchHistory(id);
      if (docIdRef.current === id && revisionRef.current === value.revision) setHistoryState(value);
    } catch {
      // The page remains usable if history status cannot be refreshed.
    }
  }, []);

  const ensurePageMetadata = useCallback(async (pageNo: number): Promise<PageMeta | undefined> => {
    const id = docIdRef.current;
    if (!id || pageNo < 0) return undefined;
    const current = pagesRef.current[pageNo];
    if (current && !current.pageId.startsWith("pending-")) return current;
    const offset = Math.floor(pageNo / 50) * 50;
    const key = `${id}:${revisionRef.current}:${offset}`;
    let request = pageInfoRequests.current.get(key);
    if (!request) {
      const expectedRevision = revisionRef.current;
      request = editorApi.fetchPages(id, offset, 50).then((response) => {
        if (docIdRef.current !== id || revisionRef.current !== expectedRevision || response.revision !== expectedRevision) return;
        const next = [...pagesRef.current];
        for (const page of response.pages) next[page.page] = page;
        replacePages(next);
      }).finally(() => pageInfoRequests.current.delete(key));
      pageInfoRequests.current.set(key, request);
    }
    await request;
    return pagesRef.current[pageNo];
  }, [replacePages]);

  const ensurePageTextModel = useCallback(async (pageNo: number): Promise<void> => {
    const id = docIdRef.current;
    if (!id) return;
    const meta = await ensurePageMetadata(pageNo);
    if (!meta || meta.pageId.startsWith("pending-")) return;
    if (pageModelsRef.current[meta.pageId]) return;
    const revision = revisionRef.current;
    const key = `${id}:${revision}:${meta.pageId}`;
    let request = pageTextRequests.current.get(key);
    if (!request) {
      request = editorApi.fetchPageText(id, pageNo).then((response) => {
        if (
          docIdRef.current !== id ||
          revisionRef.current !== revision ||
          response.revision !== revision ||
          response.page.pageId !== meta.pageId
        ) return;
        for (const line of response.page.lines) {
          if (Object.prototype.hasOwnProperty.call(editsRef.current, line.id)) {
            draftAnchors.current[line.id] = {
              lineId: line.id,
              page: pageNo,
              originalHash: line.originalHash,
              bbox: [...line.bbox],
              original: line.text,
              text: editsRef.current[line.id]!,
            };
          }
        }
        const hasDraft = Object.keys(editsRef.current).some((lineId) => lineId.startsWith(`${meta.pageId}:`));
        if (visiblePages.current.has(pageNo) || retainAllTextModels.current || hasDraft) {
          updatePageModels((previous) => ({ ...previous, [meta.pageId]: response.page }));
        }
      }).finally(() => pageTextRequests.current.delete(key));
      pageTextRequests.current.set(key, request);
    }
    await request;
  }, [ensurePageMetadata, updatePageModels]);

  const pageLayout = useMemo(() => {
    const offsets: number[] = [];
    let totalHeight = 0;
    for (const page of pages) {
      offsets.push(totalHeight);
      totalHeight += Math.max(120, page.height * scale) + 16;
    }
    return { offsets, totalHeight };
  }, [pages, scale]);

  const virtualRange = useMemo(() => {
    if (!pages.length) return { start: 0, end: 0 };
    const top = Math.max(0, scrollMetrics.scrollY - scrollMetrics.listTop);
    const bottom = top + Math.max(1, scrollMetrics.viewportHeight);
    const firstIntersecting = (position: number) => {
      let low = 0;
      let high = pages.length;
      while (low < high) {
        const mid = (low + high) >>> 1;
        const page = pages[mid]!;
        const pageBottom = pageLayout.offsets[mid]! + Math.max(120, page.height * scale);
        if (pageBottom < position) low = mid + 1;
        else high = mid;
      }
      return Math.min(low, pages.length - 1);
    };
    const first = firstIntersecting(top);
    const last = firstIntersecting(bottom);
    return {
      start: Math.max(0, first - 1),
      end: Math.min(pages.length, last + 2),
    };
  }, [pageLayout.offsets, pages, scale, scrollMetrics]);

  useEffect(() => {
    let frame = 0;
    const update = () => {
      if (frame) return;
      frame = window.setTimeout(() => {
        frame = 0;
        const scrollY = window.scrollY || document.documentElement.scrollTop || document.body.scrollTop || 0;
        const rect = pageListRef.current?.getBoundingClientRect();
        setScrollMetrics({
          scrollY,
          viewportHeight: window.innerHeight || 800,
          listTop: rect ? rect.top + scrollY : 0,
        });
      });
    };
    update();
    window.addEventListener("scroll", update, { passive: true });
    document.addEventListener("scroll", update, { passive: true, capture: true });
    window.addEventListener("resize", update);
    return () => {
      window.removeEventListener("scroll", update);
      document.removeEventListener("scroll", update, true);
      window.removeEventListener("resize", update);
      if (frame) window.clearTimeout(frame);
    };
  }, [docId]);

  useEffect(() => {
    if (busy) return;
    for (let pageNo = virtualRange.start; pageNo < virtualRange.end; pageNo += 1) {
      void ensurePageMetadata(pageNo).catch(() => undefined);
    }
  }, [busy, ensurePageMetadata, virtualRange.end, virtualRange.start]);

  const onPageViewport = useCallback((pageNo: number, nearViewport: boolean) => {
    if (nearViewport) {
      visiblePages.current.add(pageNo);
      void ensurePageTextModel(pageNo).catch((err) => {
        if (getErrorCode(err) !== "SESSION_BUSY") notify(`无法读取第 ${pageNo + 1} 页文本：${String(err)}`);
      });
      return;
    }
    visiblePages.current.delete(pageNo);
    const meta = pagesRef.current[pageNo];
    if (!meta || meta.pageId.startsWith("pending-")) return;
    const hasDraft = Object.keys(editsRef.current).some((lineId) => lineId.startsWith(`${meta.pageId}:`));
    if (hasDraft || retainAllTextModels.current) return;
    updatePageModels((previous) => {
      if (!previous[meta.pageId]) return previous;
      const next = { ...previous };
      delete next[meta.pageId];
      return next;
    });
  }, [ensurePageTextModel, notify, updatePageModels]);

  const runEditorJob = useCallback(async <T,>(operation: (options: JobOptions) => Promise<T>): Promise<T> => {
    const controller = new AbortController();
    jobController.current = controller;
    setJobProgress(null);
    try {
      return await operation({
        signal: controller.signal,
        onProgress: setJobProgress,
        onHistoryConfirm: requestHistoryConfirmation,
      });
    } finally {
      if (jobController.current === controller) jobController.current = null;
      setJobProgress(null);
    }
  }, [requestHistoryConfirmation]);

  const cancelEditorJob = useCallback(() => jobController.current?.abort(), []);

  const allLines: LineWithPage[] = useMemo(
    () => Object.values(pageModels).flatMap((page) => page.lines.map((line) => ({ ...line, page: page.page }))),
    [pageModels],
  );

  const applyDraftValue = useCallback((lineId: string, text: string, recordUndo = true) => {
    const previous = { ...editsRef.current };
    const next = { ...previous, [lineId]: text };
    if (recordUndo) {
      draftUndo.current.push(previous);
    }
    draftRedo.current = [];
    setDraftHistoryVersion((value) => value + 1);
    editsRef.current = next;
    setEdits(next);
    setDirty(true);
  }, []);

  const recordDraftChange = useCallback((lineId: string, text: string) => {
    if (editsRef.current[lineId] === text) return;
    const now = Date.now();
    const previousBurst = draftEditBurst.current;
    const startsUndoStep = !previousBurst || previousBurst.lineId !== lineId || now - previousBurst.at > 700;
    draftEditBurst.current = { lineId, at: now };
    if (!startsUndoStep) {
      applyDraftValue(lineId, text, false);
      return;
    }
    const currentBytes = [...draftUndo.current, ...draftRedo.current]
      .reduce((total, state) => total + JSON.stringify(state).length * 2, 0);
    const estimateBytes = JSON.stringify(editsRef.current).length * 2;
    const atLimit = draftUndo.current.length >= 10 || currentBytes + estimateBytes > 1024 * 1024 * 1024;
    if (!atLimit) {
      applyDraftValue(lineId, text);
      return;
    }
    const plan: HistoryCapacityPlan = {
      mode: "inverse",
      needsConfirmation: true,
      currentSteps: draftUndo.current.length + draftRedo.current.length,
      maxSteps: 10,
      currentBytes,
      maxBytes: 1024 * 1024 * 1024,
      estimateBytes,
      canEvictOldest: draftUndo.current.length > 0,
      canSkipUndo: true,
    };
    void requestHistoryConfirmation(plan).then((policy) => {
      if (policy === "cancel") return;
      if (policy === "evict_oldest") draftUndo.current.shift();
      if (policy === "no_undo") {
        draftUndo.current = [];
        draftRedo.current = [];
      }
      applyDraftValue(lineId, text, policy !== "no_undo");
    });
  }, [applyDraftValue, requestHistoryConfirmation]);

  const undoDraft = useCallback(() => {
    const previous = draftUndo.current.pop();
    if (!previous) return false;
    draftEditBurst.current = null;
    draftRedo.current.push({ ...editsRef.current });
    editsRef.current = previous;
    setEdits(previous);
    setDirty(Object.keys(previous).length > 0);
    setDraftHistoryVersion((value) => value + 1);
    return true;
  }, []);

  const redoDraft = useCallback(() => {
    const next = draftRedo.current.pop();
    if (!next) return false;
    draftEditBurst.current = null;
    draftUndo.current.push({ ...editsRef.current });
    editsRef.current = next;
    setEdits(next);
    setDirty(Object.keys(next).length > 0);
    setDraftHistoryVersion((value) => value + 1);
    return true;
  }, []);

  const findMatches = useMemo(() => {
    const q = findQuery.trim().toLowerCase();
    if (!q) return new Set<string>();
    return new Set(allLines.filter((l) => l.text.toLowerCase().includes(q)).map((l) => l.id));
  }, [findQuery, allLines]);

  useEffect(() => {
    if (!findQuery.trim() || !docId) {
      retainAllTextModels.current = false;
      return;
    }
    retainAllTextModels.current = true;
    let cancelled = false;
    let nextPage = 0;
    const loadWorker = async () => {
      while (!cancelled && nextPage < pagesRef.current.length) {
        const pageNo = nextPage++;
        try {
          await ensurePageTextModel(pageNo);
        } catch {
          // Keep the rest of the search usable if one page cannot be read.
        }
      }
    };
    void Promise.all([loadWorker(), loadWorker(), loadWorker()]);
    return () => {
      cancelled = true;
      retainAllTextModels.current = false;
    };
  }, [docId, ensurePageTextModel, findQuery]);

  // Open the session URL with PDF.js range requests. This avoids fetching the
  // whole PDF into the page and lets canvases request only visible pages.
  useEffect(() => {
    if (!docId) {
      setPdfDoc(null);
      return;
    }
    let cancelled = false;
    let doc: { destroy: () => void } | null = null;
    setPdfDoc(null);
    openPdfDocument(`/api/v1/edit/${docId}/download`).then((d) => {
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
  }, [docId, pdfReloadKey]);

  const applyResult = useCallback(
    async (result: editorApi.EditorResult, refreshCache = true) => {
      replacePages(result.pages);
      const metaById = new Map(result.pages.map((page) => [page.pageId, page]));
      const changed = new Set(result.changedPageIds ?? []);
      const nextModels: Record<string, PageModel> = {};
      for (const [pageId, model] of Object.entries(pageModelsRef.current)) {
        const meta = metaById.get(pageId);
        if (!meta || changed.has(pageId)) continue;
        nextModels[pageId] = {
          ...model,
          page: meta.page,
          width: meta.width,
          height: meta.height,
          rotation: meta.rotation,
        };
      }
      replacePageModels(nextModels);
      pageTextRequests.current.clear();
      updateRevision(result.revision);
      setHasServerChanges(true);
      if (docIdRef.current) void refreshHistory(docIdRef.current);
      setPdfReloadKey((key) => key + 1);
      if (refreshCache && draftCacheEnabledRef.current) void persistLocalDraft(true, false);
    },
    [persistLocalDraft, refreshHistory, replacePageModels, replacePages],
  );

  useEffect(() => {
    if (!docId || !draftCacheEnabled) return;
    setDraftCacheStatus("saving");
    if (cacheWriteTimer.current !== null) window.clearTimeout(cacheWriteTimer.current);
    cacheWriteTimer.current = window.setTimeout(() => {
      cacheWriteTimer.current = null;
      void persistLocalDraft(false);
    }, 650);
    return () => {
      if (cacheWriteTimer.current !== null) {
        window.clearTimeout(cacheWriteTimer.current);
        cacheWriteTimer.current = null;
      }
    };
  }, [docId, draftCacheEnabled, edits, persistLocalDraft]);

  const openFileNow = useCallback(
    async (file: File) => {
      setBusy(true);
      let openedId: string | null = null;
      try {
        const result = await runEditorJob((options) => editorApi.openEditor(file, options));
        openedId = result.docId;
        if (docId && draftCacheEnabledRef.current) await persistLocalDraft(false, false);
        if (docId) await editorApi.closeSession(docId);
        docIdRef.current = result.docId;
        setDocId(result.docId);
        updateRevision(result.revision);
        setOriginalName(result.originalName);
        const initial = result.pages.filter((page) => page.page < 50);
        replacePages(createPageSlots(result.pageCount, initial));
        replacePageModels({});
        pageTextRequests.current.clear();
        pageInfoRequests.current.clear();
        visiblePages.current.clear();
        editsRef.current = {};
        draftAnchors.current = {};
        draftUndo.current = [];
        draftRedo.current = [];
        draftEditBurst.current = null;
        setDraftHistoryVersion((value) => value + 1);
        setEdits({});
        setDirty(false);
        draftCacheId.current = null;
        draftCacheCreatedAt.current = null;
        draftCacheBlob.current = null;
        draftCacheNeedsRefresh.current = false;
        draftCacheEnabledRef.current = false;
        setDraftCacheEnabled(false);
        setDraftCacheStatus("disabled");
        setHasServerChanges(false);
        setPdfReloadKey((key) => key + 1);
        setHistoryState(null);
        void refreshHistory(result.docId);
        void refreshCachedDraftList();
        notify(`已打开 ${result.originalName}（${result.pageCount} 页）`);
      } catch (err) {
        if (openedId) void editorApi.closeSession(openedId).catch(() => undefined);
        notify(await extractError(err));
      } finally {
        setBusy(false);
      }
    },
    [docId, notify, persistLocalDraft, refreshCachedDraftList, refreshHistory, replacePageModels, replacePages, runEditorJob],
  );

  const restoreCachedDraft = useCallback(async (cacheId: string) => {
    setRestoringDraftId(cacheId);
    try {
      const record = await getDraft(cacheId);
      if (!record || record.expiresAt <= Date.now()) {
        if (record) await deleteDraft(cacheId);
        await refreshCachedDraftList();
        notify("本地恢复缓存已过期或不存在");
        return;
      }
      const file = new File([record.blob], record.name, { type: "application/pdf" });
      await openFileNow(file);
      const id = docIdRef.current;
      if (!id) return;
      setBusy(true);
      try {
        const restored: Record<string, string> = {};
        const restoredAnchors: Record<string, DraftAnchor> = {};
        let unmatched = 0;
        const byPage = new Map<number, DraftAnchor[]>();
        for (const anchor of record.edits) {
          const group = byPage.get(anchor.page) ?? [];
          group.push(anchor);
          byPage.set(anchor.page, group);
        }
        for (const [pageNo, anchors] of byPage) {
          const response = await editorApi.fetchPageText(id, pageNo);
          if (response.revision !== revisionRef.current) {
            unmatched += anchors.length;
            continue;
          }
          const used = new Set<string>();
          for (const anchor of anchors) {
            const line = response.page.lines
              .filter((candidate) => !used.has(candidate.id) && candidate.originalHash === anchor.originalHash)
              .map((candidate) => ({
                candidate,
                distance: candidate.bbox.reduce((sum, value, index) => sum + Math.abs(value - (anchor.bbox[index] ?? value)), 0),
              }))
              .filter((item) => item.distance <= 4)
              .sort((a, b) => a.distance - b.distance)[0]?.candidate;
            if (!line) {
              unmatched += 1;
              continue;
            }
            used.add(line.id);
            restored[line.id] = anchor.text;
            restoredAnchors[line.id] = {
              lineId: line.id,
              page: pageNo,
              originalHash: line.originalHash,
              bbox: [...line.bbox],
              original: line.text,
              text: anchor.text,
            };
          }
        }
        editsRef.current = restored;
        draftAnchors.current = restoredAnchors;
        setEdits(restored);
        setDirty(Object.keys(restored).length > 0);
        draftUndo.current = [];
        draftRedo.current = [];
        draftEditBurst.current = null;
        setDraftHistoryVersion((value) => value + 1);
        if (unmatched === 0) {
          draftCacheId.current = record.id;
          draftCacheCreatedAt.current = record.createdAt;
          draftCacheBlob.current = record.blob;
          draftCacheNeedsRefresh.current = false;
          draftCacheEnabledRef.current = true;
          setDraftCacheEnabled(true);
          setDraftCacheStatus("saved");
          void persistLocalDraft(false, false);
          notify(`已恢复 ${record.name}，文字草稿 ${Object.keys(restored).length} 处；撤销历史从空状态开始`);
        } else {
          draftCacheId.current = null;
          draftCacheEnabledRef.current = false;
          setDraftCacheEnabled(false);
          setDraftCacheStatus("disabled");
          notify(`文档已恢复，${Object.keys(restored).length} 处草稿已定位，另有 ${unmatched} 处未能定位；原缓存已保留`);
        }
      } finally {
        setBusy(false);
      }
    } catch (err) {
      notify(`恢复本地草稿失败：${err instanceof Error ? err.message : String(err)}`);
      await refreshCachedDraftList();
    } finally {
      setRestoringDraftId(null);
    }
  }, [notify, openFileNow, persistLocalDraft, refreshCachedDraftList]);

  const requestAction = useCallback(
    (action: () => Promise<void>, requireExport = false) => {
      if (busy) return;
      if (dirty || (requireExport && hasServerChanges)) {
        setPendingAction({ run: action, requireExport });
        return;
      }
      void action();
    },
    [busy, dirty, hasServerChanges],
  );

  const openFile = useCallback(
    (file: File) => requestAction(() => openFileNow(file), true),
    [openFileNow, requestAction],
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

  const handleSave = useCallback(async (): Promise<boolean> => {
    if (!docId) return false;
    const submittedEdits = { ...editsRef.current };
    setBusy(true);
    setSavingDraft(true);
    try {
      const expectedRevision = revisionRef.current;
      const lines = new Map<string, LineWithPage>();
      for (const model of Object.values(pageModelsRef.current)) {
        for (const line of model.lines) lines.set(line.id, { ...line, page: model.page });
      }
      for (const lineId of Object.keys(submittedEdits)) {
        if (lines.has(lineId)) continue;
        const anchor = draftAnchors.current[lineId];
        const pageNo = anchor?.page ?? pagesRef.current.find((page) => lineId.startsWith(`${page.pageId}:`))?.page;
        if (pageNo === undefined) throw new Error("有一处草稿无法定位到原页面，草稿已保留");
        const response = await editorApi.fetchPageText(docId, pageNo);
        if (response.revision !== expectedRevision || revisionRef.current !== expectedRevision) {
          throw new Error("文档版本已变化，草稿未保存且仍保留在编辑器中");
        }
        const match = response.page.lines.find((line) => line.id === lineId)
          ?? (anchor && response.page.lines.find((line) =>
            line.originalHash === anchor.originalHash
            && line.bbox.every((value, index) => Math.abs(value - (anchor.bbox[index] ?? value)) <= 1),
          ));
        if (!match) throw new Error("草稿锚点已变化，草稿未保存且仍保留在编辑器中");
        lines.set(lineId, { ...match, page: pageNo });
        draftAnchors.current[lineId] = {
          lineId: match.id,
          page: pageNo,
          originalHash: match.originalHash,
          bbox: [...match.bbox],
          original: match.text,
          text: submittedEdits[lineId]!,
        };
      }
      const changed = Object.entries(submittedEdits).filter(([id, text]) => lines.get(id)?.text !== text);
      if (changed.length === 0) {
        const remaining = { ...editsRef.current };
        for (const [id, text] of Object.entries(submittedEdits)) {
          if (remaining[id] === text) delete remaining[id];
        }
        editsRef.current = remaining;
        setEdits(remaining);
        setDirty(Object.keys(remaining).length > 0);
        notify(Object.keys(remaining).length ? "保存期间有新修改，请再次保存；当前草稿已保留" : "没有需要保存的修改");
        return Object.keys(remaining).length === 0;
      }
      const payload = changed.map(([id, text]) => {
        const line = lines.get(id)!;
        return {
          page: line.page,
          pageId: line.pageId,
          lineId: line.id,
          originalHash: line.originalHash,
          bbox: line.bbox,
          original: line.text,
          text,
          fontSize: line.fontSize,
        };
      });
      const result = await runEditorJob((options) =>
        editorApi.applyEdits(docId, expectedRevision, payload, options),
      );
      await applyResult(result, false);
      const remaining = { ...editsRef.current };
      for (const [id, submittedText] of Object.entries(submittedEdits)) {
        if (remaining[id] === submittedText) delete remaining[id];
      }
      editsRef.current = remaining;
      setEdits(remaining);
      setDirty(Object.keys(remaining).length > 0);
      if (draftCacheEnabledRef.current) void persistLocalDraft(true, false);
      notify(Object.keys(remaining).length
        ? `已提交 ${changed.length} 处修改；保存期间的新修改仍保留，请再次保存`
        : `已保存 ${changed.length} 处修改`);
      return Object.keys(remaining).length === 0;
    } catch (err) {
      notify(await extractError(err));
      return false;
    } finally {
      setSavingDraft(false);
      setBusy(false);
    }
  }, [docId, applyResult, notify, persistLocalDraft, runEditorJob]);

  const handleSaveAs = useCallback(async (): Promise<boolean> => {
    if (!docId) return false;
    if (Object.keys(editsRef.current).length > 0 && !(await handleSave())) return false;
    if (Object.keys(editsRef.current).length > 0) return false;
    const name = window.prompt("另存为文件名：", originalName);
    if (!name) return false;
    try {
      await editorApi.saveEditedAs(docId, name);
      setHasServerChanges(false);
      notify(`已导出 ${name}`);
      return true;
    } catch (err) {
      notify(await extractError(err));
      return false;
    }
  }, [docId, dirty, handleSave, originalName, notify]);

  const continueAfterSave = useCallback(async () => {
    const pending = pendingAction;
    if (!pending) return;
    const shouldExport = pending.requireExport || hasServerChanges;
    if (dirty && !(await handleSave())) return;
    if (shouldExport && !(await handleSaveAs())) return;
    setPendingAction(null);
    await pending.run();
  }, [dirty, handleSave, handleSaveAs, hasServerChanges, pendingAction]);

  const continueAfterDiscard = useCallback(async () => {
    const pending = pendingAction;
    if (!pending) return;
    editsRef.current = {};
    draftAnchors.current = {};
    draftUndo.current = [];
    draftRedo.current = [];
    draftEditBurst.current = null;
    setEdits({});
    setDirty(false);
    setDraftHistoryVersion((value) => value + 1);
    if (draftCacheEnabledRef.current) await persistLocalDraft(false, false);
    setPendingAction(null);
    await pending.run();
  }, [pendingAction, persistLocalDraft]);

  const closeCurrentFile = useCallback(async () => {
    if (!docId) return;
    setBusy(true);
    try {
      if (draftCacheEnabledRef.current) await persistLocalDraft(false, false);
      await editorApi.closeSession(docId);
      docIdRef.current = null;
      setDocId(null);
      updateRevision(0);
      replacePages([]);
      replacePageModels({});
      editsRef.current = {};
      draftAnchors.current = {};
      draftUndo.current = [];
      draftRedo.current = [];
      draftEditBurst.current = null;
      draftCacheId.current = null;
      draftCacheCreatedAt.current = null;
      draftCacheBlob.current = null;
      draftCacheNeedsRefresh.current = false;
      draftCacheEnabledRef.current = false;
      setEdits({});
      setDirty(false);
      setDraftCacheEnabled(false);
      setDraftCacheStatus("disabled");
      setHasServerChanges(false);
      setHistoryState(null);
      await refreshCachedDraftList();
    } catch (err) {
      notify(await extractError(err));
    } finally {
      setBusy(false);
    }
  }, [docId, notify, persistLocalDraft, refreshCachedDraftList, replacePageModels, replacePages]);

  // warn on tab close with unsaved edits
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (dirty || hasServerChanges) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty, hasServerChanges]);

  useEffect(() => {
    const handler = (event: MouseEvent) => {
      if (busy || (!dirty && !hasServerChanges)) return;
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const target = event.target instanceof Element ? event.target.closest<HTMLAnchorElement>("a[href]") : null;
      if (!target || target.target === "_blank") return;
      const url = new URL(target.href, window.location.href);
      if (url.origin !== window.location.origin || url.href === window.location.href) return;
      event.preventDefault();
      event.stopPropagation();
      setPendingAction({
        run: async () => navigate(`${url.pathname}${url.search}${url.hash}`),
        requireExport: hasServerChanges,
      });
    };
    document.addEventListener("click", handler, true);
    return () => document.removeEventListener("click", handler, true);
  }, [busy, dirty, hasServerChanges, navigate]);

  useEffect(() => {
    if (!docId) return;
    const heartbeat = window.setInterval(() => {
      void editorApi.touchSession(docId).catch(() => undefined);
    }, 5 * 60 * 1000);
    const closeOnPageHide = () => {
      const url = `/api/v1/edit/${docId}/close`;
      if (!navigator.sendBeacon(url, new Blob())) {
        void fetch(url, { method: "POST", keepalive: true }).catch(() => undefined);
      }
    };
    window.addEventListener("pagehide", closeOnPageHide);
    return () => {
      window.clearInterval(heartbeat);
      window.removeEventListener("pagehide", closeOnPageHide);
      void editorApi.closeSession(docId).catch(() => undefined);
    };
  }, [docId]);

  const performHistoryAction = useCallback(async (action: "undo" | "redo") => {
    if (!docId || busy || dirty) return;
    setBusy(true);
    try {
      const result = await runEditorJob((options) =>
        action === "undo"
          ? editorApi.undo(docId, revisionRef.current, options)
          : editorApi.redo(docId, revisionRef.current, options),
      );
      await applyResult(result);
      notify(action === "undo" ? "已撤销上一步文档修改" : "已重做文档修改");
    } catch (err) {
      notify(await extractError(err));
    } finally {
      setBusy(false);
    }
  }, [applyResult, busy, dirty, docId, notify, runEditorJob]);

  const handleUndo = useCallback(() => {
    if (busy) return;
    if (undoDraft()) return;
    if (!dirty) void performHistoryAction("undo");
  }, [busy, dirty, performHistoryAction, undoDraft]);

  const handleRedo = useCallback(() => {
    if (busy) return;
    if (redoDraft()) return;
    if (!dirty) void performHistoryAction("redo");
  }, [busy, dirty, performHistoryAction, redoDraft]);

  const removeCachedDraft = useCallback(async (id: string) => {
    try {
      await deleteDraft(id);
      if (draftCacheId.current === id) {
        draftCacheId.current = null;
        draftCacheCreatedAt.current = null;
        draftCacheBlob.current = null;
        draftCacheNeedsRefresh.current = false;
        draftCacheEnabledRef.current = false;
        setDraftCacheEnabled(false);
        setDraftCacheStatus("disabled");
      }
      await refreshCachedDraftList();
      notify("已清除此文档的本地恢复缓存");
    } catch (err) {
      notify(`清除本地缓存失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }, [notify, refreshCachedDraftList]);

  // ------------------------------------------------------------------
  // page-level actions
  // ------------------------------------------------------------------

  const onPageClick = useCallback(
    (pageNo: number, pt: { x: number; y: number }) => {
      if (!docId) return;
      const page = pages[pageNo];
      if (!page) return;
      if (page.pageId.startsWith("pending-")) {
        notify("页面信息仍在加载，请稍候再操作");
        return;
      }
      if (page.rotation !== 0 && (mode === "text" || mode === "image" || mode === "redact")) {
        notify("旋转页暂不支持坐标编辑，请先归一化页面");
        return;
      }
      if (mode === "rotate") {
        requestAction(async () => {
          setBusy(true);
          try {
            const result = await runEditorJob((options) =>
              editorApi.rotatePage(docId, revisionRef.current, pageNo, 90, options),
            );
            await applyResult(result);
            notify(`第 ${pageNo + 1} 页已旋转 90°`);
          } catch (err) {
            notify(await extractError(err));
          } finally {
            setBusy(false);
          }
        });
      } else if (mode === "delete") {
        if (!window.confirm(`确定删除第 ${pageNo + 1} 页？`)) return;
        requestAction(async () => {
          setBusy(true);
          try {
            const result = await runEditorJob((options) =>
              editorApi.deletePages(docId, revisionRef.current, [pageNo], options),
            );
            await applyResult(result);
            notify(`已删除第 ${pageNo + 1} 页`);
          } catch (err) {
            notify(await extractError(err));
          } finally {
            setBusy(false);
          }
        });
      } else if (mode === "move") {
        if (moveSource === null) {
          setMoveSource(pageNo);
          notify(`已选第 ${pageNo + 1} 页为源，点击目标页完成移动`);
        } else if (moveSource !== pageNo) {
          requestAction(async () => {
            setBusy(true);
            try {
              const result = await runEditorJob((options) =>
                editorApi.movePage(docId, revisionRef.current, moveSource, pageNo, options),
              );
              await applyResult(result);
              notify(`第 ${moveSource + 1} 页已移动到第 ${pageNo + 1} 页`);
            } catch (err) {
              notify(await extractError(err));
            } finally {
              setMoveSource(null);
              setBusy(false);
            }
          });
        } else {
          setMoveSource(null);
        }
      } else if (mode === "text") {
        setAddTextAt({ page: pageNo, x: pt.x, y: pt.y });
      } else if (mode === "image") {
        requestAction(async () => {
          pendingImagePage.current = { page: pageNo, x: pt.x, y: pt.y, pageId: page.pageId };
          imageInputRef.current?.click();
        });
      }
    },
    [docId, pages, mode, moveSource, notify, applyResult, requestAction, runEditorJob],
  );

  const onImagePicked = useCallback(
    async (file: File | null) => {
      const pos = pendingImagePage.current;
      if (!file || !pos || !docId) return;
      requestAction(async () => {
        setBusy(true);
        try {
          await applyResult(
            await runEditorJob((options) =>
              editorApi.addImage(docId, revisionRef.current, { image: file, ...pos, w: 120, h: 80 }, options),
            ),
          );
          notify("图片已添加");
        } catch (err) {
          notify(await extractError(err));
        } finally {
          setBusy(false);
          pendingImagePage.current = null;
        }
      });
    },
    [docId, notify, applyResult, requestAction, runEditorJob],
  );

  const onRedactRect = useCallback(
    async (pageNo: number, rect: number[]) => {
      if (!docId) return;
      const page = pages[pageNo];
      if (!page || page.rotation !== 0) {
        notify("旋转页暂不支持坐标编辑，请先归一化页面");
        return;
      }
      requestAction(async () => {
        setBusy(true);
        try {
          await applyResult(
            await runEditorJob((options) => editorApi.applyEdits(
              docId,
              revisionRef.current,
              [{ kind: "cover", page: pageNo, pageId: page.pageId, bbox: rect, original: "", text: "" }],
              options,
            )),
          );
          notify("已添加视觉遮挡；这不等同于安全脱敏");
        } catch (err) {
          notify(await extractError(err));
        } finally {
          setBusy(false);
        }
      });
    },
    [docId, pages, notify, applyResult, requestAction, runEditorJob],
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
        {cachedDrafts.length > 0 && (
          <Paper withBorder p="sm" radius="md" w="min(620px, 94vw)">
            <Stack gap="xs">
              <Group justify="space-between">
                <Text fw={600} size="sm">本机恢复缓存</Text>
                <Button size="xs" color="red" variant="light" onClick={() => void clearEveryCache()}>
                  清除全部缓存
                </Button>
              </Group>
              <Text size="xs" c="dimmed">缓存未加密，保存后 7 天过期。选择文档可恢复；撤销/重做历史不会恢复。</Text>
              {cachedDrafts.map((draft) => (
                <Group key={draft.id} justify="space-between" wrap="nowrap">
                  <Text size="xs" style={{ flex: 1 }} lineClamp={1}>
                    {draft.name} · {draft.pageCount} 页 · {formatSize(draft.sizeBytes)} · {draft.editCount} 处文字草稿
                  </Text>
                  <Button size="xs" loading={restoringDraftId === draft.id} onClick={() => void restoreCachedDraft(draft.id)}>
                    恢复
                  </Button>
                  <Button size="xs" color="red" variant="subtle" onClick={() => void removeCachedDraft(draft.id)}>
                    清除
                  </Button>
                </Group>
              ))}
            </Stack>
          </Paper>
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
      <Modal
        opened={cacheConfirmOpen}
        onClose={() => setCacheConfirmOpen(false)}
        title="开启本机草稿恢复"
        centered
      >
        <Stack gap="sm">
          <Alert color="yellow" title="请先确认文档不敏感">
            此功能会把当前 PDF 和文字草稿存入浏览器 IndexedDB。缓存未加密，7 天后自动过期；可随时清除。请只对非敏感文档开启。
          </Alert>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setCacheConfirmOpen(false)}>取消</Button>
            <Button loading={draftCacheStatus === "saving"} onClick={() => void enableDraftCache()}>
              我确认文档非敏感，开启缓存
            </Button>
          </Group>
        </Stack>
      </Modal>
      <Modal
        opened={pendingAction !== null}
        onClose={() => setPendingAction(null)}
        title="有未保存的修改"
        centered
      >
        <Stack gap="sm">
          <Text size="sm">
            {pendingAction?.requireExport || hasServerChanges
              ? "当前编辑只保存在临时会话中。保存会先提交文字草稿，再另存为文件。"
              : "继续操作前需要处理文字草稿。"}
          </Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setPendingAction(null)}>
              取消
            </Button>
            <Button color="red" variant="light" onClick={() => void continueAfterDiscard()}>
              放弃草稿并继续
            </Button>
            <Button onClick={() => void continueAfterSave()}>
              {pendingAction?.requireExport || hasServerChanges ? "另存为并继续" : "保存并继续"}
            </Button>
          </Group>
        </Stack>
      </Modal>
      <Modal
        opened={historyPrompt !== null}
        onClose={() => resolveHistoryChoice("cancel")}
        title="撤销历史容量提示"
        centered
      >
        <Stack gap="sm">
          <Text size="sm">
            当前历史 {historyPrompt?.currentSteps ?? 0}/{historyPrompt?.maxSteps ?? 10} 步，
            占用 {formatSize(historyPrompt?.currentBytes ?? 0)}；本次快照预计
            {formatSize(historyPrompt?.estimateBytes ?? 0)}。历史上限为 10 步或 1 GiB。
          </Text>
          <Text size="sm" c="dimmed">
            选择如何继续。容量不足时不会静默丢弃撤销能力。
          </Text>
          <Group justify="flex-end" wrap="wrap">
            <Button variant="default" onClick={() => resolveHistoryChoice("cancel")}>
              取消本次操作
            </Button>
            <Button color="orange" variant="light" onClick={() => resolveHistoryChoice("no_undo")}>
              本次不保留撤销继续
            </Button>
            {historyPrompt?.canEvictOldest && (
              <Button onClick={() => resolveHistoryChoice("evict_oldest")}>
                淘汰最旧历史继续
              </Button>
            )}
          </Group>
        </Stack>
      </Modal>
      {/* toolbar */}
      <Paper withBorder p="xs" radius="md" style={{ position: "sticky", top: 0, zIndex: 10 }}>
        <Group justify="space-between" wrap="wrap" gap="xs">
          <Group gap="xs">
            <ActionIcon
              variant="subtle"
              onClick={() => requestAction(closeCurrentFile, true)}
              title="关闭文件"
            >
              <IconArrowLeft size={18} />
            </ActionIcon>
            <Text fw={600} size="sm">
              {originalName}
            </Text>
            {dirty && <Badge color="orange" variant="light">未保存</Badge>}
            {!dirty && hasServerChanges && <Badge color="yellow" variant="light">待另存</Badge>}
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
            <Button
              size="xs"
              variant="default"
              leftSection={<IconArrowBackUp size={14} />}
              disabled={busy || (draftUndo.current.length === 0 && (dirty || !historyState?.canUndo))}
              onClick={handleUndo}
              title={dirty ? "撤销文字草稿" : "撤销文档修改"}
            >
              撤销
            </Button>
            <Button
              size="xs"
              variant="default"
              leftSection={<IconArrowForwardUp size={14} />}
              disabled={busy || (draftRedo.current.length === 0 && (dirty || !historyState?.canRedo))}
              onClick={handleRedo}
              title={dirty ? "重做文字草稿" : "重做文档修改"}
            >
              重做
            </Button>
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
            <Button
              size="xs"
              variant={draftCacheEnabled ? "light" : "default"}
              color={draftCacheEnabled ? "red" : undefined}
              onClick={() => draftCacheEnabled ? void clearDocumentCache() : setCacheConfirmOpen(true)}
            >
              {draftCacheEnabled ? "清除本地缓存" : "开启草稿恢复"}
            </Button>
            {draftCacheEnabled && draftCacheStatus === "error" && (
              <Button size="xs" color="orange" variant="light" onClick={() => void persistLocalDraft(draftCacheNeedsRefresh.current || !draftCacheBlob.current)}>
                重试缓存
              </Button>
            )}
            {draftCacheEnabled && (
              <Text size="xs" c={draftCacheStatus === "error" ? "red" : draftCacheStatus === "saved" ? "green" : "dimmed"}>
                {draftCacheStatus === "saving" ? "缓存保存中" : draftCacheStatus === "saved" ? "已缓存" : "未保存缓存"}
              </Text>
            )}
          </Group>
        </Group>
      </Paper>

      {toast && (
        <Alert color="blue" w="100%">
          {toast}
        </Alert>
      )}

      {jobProgress && (
        <Alert color="blue" title={jobProgress.state === "queued" ? "任务排队中" : "任务处理中"}>
          <Group justify="space-between">
            <Stack gap={4} style={{ flex: 1 }}>
              <Text size="sm">
                {jobProgress.progress?.phase ?? jobProgress.state}
                {jobProgress.progress?.total
                  ? `：${jobProgress.progress.completed ?? 0}/${jobProgress.progress.total} ${jobProgress.progress.unit ?? ""}`
                  : ""}
              </Text>
              {jobProgress.progress?.total ? (
                <Progress value={Math.min(100, ((jobProgress.progress.completed ?? 0) / jobProgress.progress.total) * 100)} />
              ) : null}
              {jobProgress.cancelTooLate && <Text size="xs">已进入提交阶段，取消不再生效。</Text>}
            </Stack>
            {busy && !jobProgress.cancelTooLate && (
              <Button size="xs" color="red" variant="light" onClick={cancelEditorJob}>
                取消任务
              </Button>
            )}
          </Group>
        </Alert>
      )}

      {mode === "redact" && (
        <Alert color="yellow" title="视觉遮挡限制">
          白色覆盖不保证从图片、附件、元数据或其他 PDF 对象中清除敏感信息，请勿用作安全脱敏。
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
      <div
        ref={pageListRef}
        aria-label="PDF 页面列表"
        data-rendered-page-start={virtualRange.start}
        data-rendered-page-end={virtualRange.end}
        style={{ position: "relative", height: pageLayout.totalHeight, minHeight: 120 }}
      >
        {pages.slice(virtualRange.start, virtualRange.end).map((p) => (
          <div
            key={p.page}
            style={{
              position: "absolute",
              top: pageLayout.offsets[p.page] ?? 0,
              left: "50%",
              transform: "translateX(-50%)",
            }}
          >
            <EditorCanvas
              page={p}
              pageModel={pageModels[p.pageId]}
              busy={busy}
              textEditLocked={busy && !savingDraft}
              scale={scale}
              pdfDoc={pdfDoc}
              mode={mode}
              edits={edits}
              findMatches={findMatches}
              moveSource={moveSource}
              addTextAt={addTextAt?.page === p.page ? addTextAt : null}
              onLineChange={(id, text) => {
                if (busy && !savingDraft) return;
                const line = allLines.find((candidate) => candidate.id === id);
                if (line) {
                  draftAnchors.current[id] = {
                    lineId: id,
                    page: line.page,
                    originalHash: line.originalHash,
                    bbox: [...line.bbox],
                    original: line.text,
                    text,
                  };
                }
                recordDraftChange(id, text);
              }}
              onPageClick={onPageClick}
              onPageViewport={onPageViewport}
              onRedactRect={onRedactRect}
              onAddTextConfirm={async (text, fontSize, color) => {
                if (!addTextAt || !docId) return;
                const target = pages[addTextAt.page];
                if (!target || target.rotation !== 0) {
                  notify("旋转页暂不支持坐标编辑，请先归一化页面");
                  setAddTextAt(null);
                  return;
                }
                requestAction(async () => {
                  setBusy(true);
                  try {
                    await applyResult(
                      await runEditorJob((options) => editorApi.addText(docId, revisionRef.current, {
                        page: addTextAt.page,
                        pageId: target.pageId,
                        x: addTextAt.x,
                        y: addTextAt.y + fontSize,
                        text,
                        fontSize,
                        color,
                      }, options)),
                    );
                    notify("文字已添加");
                  } catch (err) {
                    notify(await extractError(err));
                  } finally {
                    setBusy(false);
                    setAddTextAt(null);
                  }
                });
              }}
              onAddTextCancel={() => setAddTextAt(null)}
            />
          </div>
        ))}
      </div>
    </Stack>
  );
}

// ---------------------------------------------------------------------------
// single page canvas + text overlays
// ---------------------------------------------------------------------------

interface EditorCanvasProps {
  page: PageMeta;
  pageModel?: PageModel;
  busy: boolean;
  textEditLocked: boolean;
  scale: number;
  pdfDoc: { getPage: (n: number) => Promise<unknown> } | null;
  mode: EditMode;
  edits: Record<string, string>;
  findMatches: Set<string>;
  moveSource: number | null;
  addTextAt: { page: number; x: number; y: number } | null;
  onLineChange: (id: string, text: string) => void;
  onPageClick: (pageNo: number, pt: { x: number; y: number }) => void;
  onPageViewport: (pageNo: number, nearViewport: boolean) => void;
  onRedactRect: (pageNo: number, rect: number[]) => void;
  onAddTextConfirm: (text: string, fontSize: number, color: string) => void;
  onAddTextCancel: () => void;
}

function EditorCanvas(props: EditorCanvasProps) {
  const {
    page, pageModel, busy, textEditLocked, scale, pdfDoc, mode, edits, findMatches, moveSource, addTextAt,
    onLineChange, onPageClick, onPageViewport, onRedactRect, onAddTextConfirm, onAddTextCancel,
  } = props;
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [dragStart, setDragStart] = useState<{ x: number; y: number } | null>(null);
  const [dragRect, setDragRect] = useState<{ x: number; y: number; w: number; h: number } | null>(null);
  const [nearViewport, setNearViewport] = useState(false);
  const [rendered, setRendered] = useState(false);
  const [previewLimited, setPreviewLimited] = useState(false);

  const widthCss = page.width * scale;
  const heightCss = page.height * scale;

  useEffect(() => {
    const node = containerRef.current;
    if (!node) return;
    if (!("IntersectionObserver" in window)) {
      setNearViewport(true);
      onPageViewport(page.page, true);
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        const near = entry?.isIntersecting ?? false;
        setNearViewport(near);
        onPageViewport(page.page, near);
      },
      { rootMargin: "1100px 0px" },
    );
    observer.observe(node);
    return () => {
      observer.disconnect();
      onPageViewport(page.page, false);
    };
  }, [onPageViewport, page.page]);

  useEffect(() => {
    if (nearViewport && !busy && !pageModel && !page.pageId.startsWith("pending-")) {
      onPageViewport(page.page, true);
    }
  }, [busy, nearViewport, onPageViewport, page.page, page.pageId, pageModel]);

  // render the page canvas
  useEffect(() => {
    if (!pdfDoc || !canvasRef.current || !nearViewport) {
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
      let renderScale = scale * dpr;
      const requestedPixels = page.width * page.height * renderScale * renderScale;
      const limit = 24_000_000;
      if (requestedPixels > limit) {
        renderScale *= Math.sqrt(limit / requestedPixels);
        setPreviewLimited(true);
      } else {
        setPreviewLimited(false);
      }
      const viewport = p.getViewport({ scale: renderScale, rotation: page.rotation });
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
      if (!cancelled) setRendered(true);
    })().catch(() => undefined);
    return () => {
      cancelled = true;
      renderTask?.cancel();
    };
  }, [nearViewport, pdfDoc, page.height, page.page, page.rotation, page.width, scale]);

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
        data-page-number={page.page}
        style={{
          position: "relative",
          width: widthCss,
          height: heightCss,
          boxShadow: "0 0 8px rgba(0,0,0,0.25)",
          cursor: mode === "select" ? "default" : "crosshair",
          outline: moveSource === page.page ? "3px solid #339af0" : undefined,
        }}
        onMouseDown={(e) => {
          if (mode !== "redact" || page.rotation !== 0 || busy) return;
          const pt = ptFromEvent(e.clientX, e.clientY);
          setDragStart(pt);
          setDragRect({ x: pt.x, y: pt.y, w: 0, h: 0 });
        }}
        onMouseMove={(e) => {
          if (mode !== "redact" || page.rotation !== 0 || busy || !dragStart) return;
          const pt = ptFromEvent(e.clientX, e.clientY);
          setDragRect({
            x: Math.min(dragStart.x, pt.x),
            y: Math.min(dragStart.y, pt.y),
            w: Math.abs(pt.x - dragStart.x),
            h: Math.abs(pt.y - dragStart.y),
          });
        }}
        onMouseUp={() => {
          if (mode !== "redact" || page.rotation !== 0 || busy || !dragRect) return;
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
          style={{
            display: rendered ? "block" : "none",
            position: "absolute",
            left: 0,
            top: 0,
            width: widthCss,
            height: heightCss,
          }}
        />
        {nearViewport && !rendered && <Text size="xs" c="dimmed">正在渲染第 {page.page + 1} 页…</Text>}

        {/* text overlays */}
        <div style={{ pointerEvents: overlayPointerEvents }}>
          {(pageModel?.lines ?? []).map((line) => {
            const [x0, y0, x1, y1] = line.bbox;
            const value = edits[line.id] ?? line.text;
            const fontStyle = FONT_STYLES[line.font] ?? FONT_STYLES.helv!;
            const height = Math.max((y1 - y0) * scale, line.fontSize * scale * 1.35);
            return (
              <textarea
                key={line.id}
                value={value}
                readOnly={mode !== "select" || page.rotation !== 0 || textEditLocked}
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
                  background: findMatches.has(line.id) ? "#fff3bf" : "#fff",
                  border: "none",
                  outline: "none",
                  resize: "none",
                  overflow: "hidden",
                  padding: 0,
                  margin: 0,
                  fontFamily: fontStyle.family,
                  fontWeight: fontStyle.weight,
                  fontStyle: fontStyle.style,
                  cursor: mode === "select" && page.rotation === 0 && !busy ? "text" : "default",
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
        {page.rotation !== 0 && "（已旋转，坐标编辑已禁用）"}
        {previewLimited && "（已降低预览分辨率）"}
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
