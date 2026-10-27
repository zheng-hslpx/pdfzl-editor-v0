import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FileProvider } from "../contexts/FileContext";
import { EditorPage } from "../pages/EditorPage";
import { createMemoryRouter, Link, RouterProvider, useLocation } from "react-router-dom";

const mocks = vi.hoisted(() => ({
  openEditor: vi.fn(),
  fetchPages: vi.fn(),
  fetchPageText: vi.fn(),
  applyEdits: vi.fn(),
  addText: vi.fn(),
  addImage: vi.fn(),
  rotatePage: vi.fn(),
  deletePages: vi.fn(),
  movePage: vi.fn(),
  saveEditedAs: vi.fn(),
  closeSession: vi.fn(),
  touchSession: vi.fn(),
  fetchHistory: vi.fn(),
  fetchHistoryPlan: vi.fn(),
  undo: vi.fn(),
  redo: vi.fn(),
  fetchSessionPdf: vi.fn(),
  openPdfDocument: vi.fn(),
  listDrafts: vi.fn(),
  createDraftId: vi.fn(),
  getDraft: vi.fn(),
  saveDraft: vi.fn(),
  deleteDraft: vi.fn(),
  clearAllDrafts: vi.fn(),
}));

vi.mock("../api/editor", () => mocks);
vi.mock("../lib/pdfjs", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/pdfjs")>()),
  openPdfDocument: mocks.openPdfDocument,
}));
vi.mock("../lib/draftStore", () => ({
  clearAllDrafts: mocks.clearAllDrafts,
  createDraftId: mocks.createDraftId,
  deleteDraft: mocks.deleteDraft,
  DRAFT_TTL_MS: 7 * 24 * 60 * 60 * 1000,
  getDraft: mocks.getDraft,
  listDrafts: mocks.listDrafts,
  saveDraft: mocks.saveDraft,
}));

afterEach(async () => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  await new Promise((resolve) => setTimeout(resolve, 250));
});

const page = {
  page: 0,
  pageId: "page-1",
  width: 595,
  height: 842,
  rotation: 0,
  lines: [{
    id: "page-1:l0",
    pageId: "page-1",
    originalHash: "hash-1",
    bbox: [72, 80, 180, 100],
    fontSize: 14,
    font: "helv",
    color: "#000000",
    text: "ORIGINAL TEXT",
  }],
};

function renderEditor(initialEntries = ["/editor"]) {
  function LocationMarker() {
    const location = useLocation();
    return <span data-testid="route-location">{location.pathname}</span>;
  }
  function EditorRoute() {
    return (
      <>
        <LocationMarker />
        <Link to="/other">离开编辑器</Link>
        <EditorPage />
      </>
    );
  }
  function OtherRoute() {
    return <><LocationMarker /><span>已离开编辑器</span></>;
  }
  const router = createMemoryRouter([
    { path: "/editor", element: <EditorRoute /> },
    { path: "/other", element: <OtherRoute /> },
    { path: "*", element: <EditorRoute /> },
  ], { initialEntries, initialIndex: initialEntries.length - 1 });
  const result = render(
    <MantineProvider>
      <FileProvider>
        <RouterProvider router={router} />
      </FileProvider>
    </MantineProvider>,
  );
  return { ...result, router };
}

async function openSample(container: HTMLElement, expectTextOverlay = true) {
  const input = container.querySelector<HTMLInputElement>('input[accept=".pdf,application/pdf"]');
  expect(input).toBeDefined();
  fireEvent.change(input!, {
    target: { files: [new File(["pdf"], "sample.pdf", { type: "application/pdf" })] },
  });
  if (expectTextOverlay) {
    await waitFor(() => expect(container.querySelector("textarea")).not.toBeNull());
  } else {
    await waitFor(() => expect(container.querySelector('[data-testid="rotation-notice-0"]')).not.toBeNull());
  }
  await waitFor(() => expect(mocks.fetchPageText).toHaveBeenCalled());
  if (expectTextOverlay) {
    expect(window.getComputedStyle(container.querySelector("textarea")!).backgroundColor).toBe("rgba(0, 0, 0, 0)");
    expect(window.getComputedStyle(container.querySelector("textarea")!).color).toBe("rgba(0, 0, 0, 0)");
  }
  await new Promise((resolve) => setTimeout(resolve, 0));
}

describe("editor dirty-draft protection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.openEditor.mockResolvedValue({
      docId: "session-1",
      originalName: "sample.pdf",
      pageCount: 1,
      revision: 0,
      pages: [page],
      textModel: [page],
    });
    mocks.fetchPages.mockResolvedValue({ docId: "session-1", revision: 0, pageCount: 1, pages: [page] });
    mocks.fetchPageText.mockResolvedValue({ docId: "session-1", revision: 0, page });
    mocks.applyEdits.mockResolvedValue({
      ok: true,
      pageCount: 1,
      revision: 1,
      pages: [page],
      textModel: [{ ...page, lines: [{ ...page.lines[0]!, text: "EDITED TEXT" }] }],
    });
    mocks.saveEditedAs.mockResolvedValue(undefined);
    mocks.closeSession.mockResolvedValue(undefined);
    mocks.touchSession.mockResolvedValue(undefined);
    mocks.fetchHistory.mockResolvedValue({
      docId: "session-1", revision: 0, canUndo: false, canRedo: false,
      undoSteps: 0, redoSteps: 0, historyBytes: 0, maxSteps: 10, maxBytes: 1024 ** 3,
    });
    mocks.fetchHistoryPlan.mockResolvedValue({
      mode: "snapshot", needsConfirmation: false, currentSteps: 0, maxSteps: 10,
      currentBytes: 0, maxBytes: 1024 ** 3, estimateBytes: 0,
      canEvictOldest: false, canSkipUndo: true,
    });
    mocks.fetchSessionPdf.mockResolvedValue(new Blob(["pdf"], { type: "application/pdf" }));
    mocks.listDrafts.mockResolvedValue([]);
    mocks.createDraftId.mockReturnValue("cache-1");
    mocks.getDraft.mockResolvedValue(undefined);
    mocks.saveDraft.mockResolvedValue(undefined);
    mocks.deleteDraft.mockResolvedValue(undefined);
    mocks.clearAllDrafts.mockResolvedValue(undefined);
    mocks.openPdfDocument.mockResolvedValue({
      promise: Promise.resolve({
        numPages: 1,
        getPage: async () => ({
          getViewport: () => ({ width: 714, height: 1010 }),
          render: () => ({ promise: Promise.resolve(), cancel: () => undefined }),
          cleanup: () => undefined,
        }),
      }),
      destroy: vi.fn().mockResolvedValue(undefined),
    });
  });

  it("shows only the focused editor or changed draft and restores canvas appearance on blur and undo", async () => {
    const { container } = renderEditor();
    await openSample(container);
    const textbox = container.querySelector("textarea")!;
    fireEvent.focus(textbox);
    expect(textbox.style.background).toBe("rgb(255, 255, 255)");
    fireEvent.blur(textbox);
    expect(textbox.style.background).toBe("transparent");
    fireEvent.change(textbox, { target: { value: "VISIBLE DRAFT" } });
    fireEvent.blur(textbox);
    expect(textbox.style.color).toBe("rgb(0, 0, 0)");
    expect(textbox.style.background).toBe("rgb(255, 255, 255)");
    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    expect(textbox.style.background).toBe("transparent");
    expect(textbox.style.color).toBe("transparent");
  });

  it("keeps the draft when closing is cancelled, and only discards after confirmation", async () => {
    const { container } = renderEditor();
    await openSample(container);
    const textbox = container.querySelector("textarea") as HTMLTextAreaElement;
    fireEvent.change(textbox, { target: { value: "LOCAL DRAFT" } });

    fireEvent.click(screen.getByTitle("关闭文件"));
    expect(await screen.findByText("有未保存的修改")).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect((container.querySelector("textarea") as HTMLTextAreaElement).value).toBe("LOCAL DRAFT");
    expect(mocks.closeSession).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTitle("关闭文件"));
    fireEvent.click(screen.getByRole("button", { name: "放弃草稿并继续" }));
    await waitFor(() => expect(screen.getByText("可视化编辑器")).toBeDefined());
    expect(mocks.closeSession).toHaveBeenCalledWith("session-1");
  });

  it("protects dirty drafts during SPA navigation and closes the session after confirmation", async () => {
    const { container } = renderEditor();
    await openSample(container);
    fireEvent.change(container.querySelector("textarea")!, { target: { value: "LOCAL DRAFT" } });

    fireEvent.click(screen.getByRole("link", { name: "离开编辑器" }));
    expect(await screen.findByText("有未保存的修改")).toBeDefined();
    expect(screen.getByTestId("route-location").textContent).toBe("/editor");
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect((container.querySelector("textarea") as HTMLTextAreaElement).value).toBe("LOCAL DRAFT");

    fireEvent.click(screen.getByRole("link", { name: "离开编辑器" }));
    fireEvent.click(screen.getByRole("button", { name: "放弃草稿并继续" }));
    expect(await screen.findByText("已离开编辑器")).toBeDefined();
    expect(screen.getByTestId("route-location").textContent).toBe("/other");
    await waitFor(() => expect(mocks.closeSession).toHaveBeenCalledWith("session-1"));
  });

  it("blocks history POP and keeps the editor session when navigation is cancelled", async () => {
    const { container, router } = renderEditor(["/other", "/editor"]);
    await openSample(container);
    fireEvent.change(container.querySelector("textarea")!, { target: { value: "POP DRAFT" } });

    act(() => { void router.navigate(-1); });
    expect(await screen.findByText("有未保存的修改")).toBeDefined();
    expect(screen.getByTestId("route-location").textContent).toBe("/editor");
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect((container.querySelector("textarea") as HTMLTextAreaElement).value).toBe("POP DRAFT");
    expect(mocks.closeSession).not.toHaveBeenCalled();

    act(() => { void router.navigate(-1); });
    fireEvent.click(await screen.findByRole("button", { name: "放弃草稿并继续" }));
    expect(await screen.findByText("已离开编辑器")).toBeDefined();
    expect(screen.getByTestId("route-location").textContent).toBe("/other");
    await waitFor(() => expect(mocks.closeSession).toHaveBeenCalledWith("session-1"));
  });

  it("submits anchored edits before Save As", async () => {
    const prompt = vi.spyOn(window, "prompt").mockReturnValue("saved.pdf");
    const { container } = renderEditor();
    await openSample(container);
    fireEvent.change(container.querySelector("textarea")!, { target: { value: "EDITED TEXT" } });

    fireEvent.click(screen.getByRole("button", { name: "另存为" }));

    await waitFor(() => expect(mocks.saveEditedAs).toHaveBeenCalledWith("session-1", "saved.pdf"));
    expect(mocks.applyEdits).toHaveBeenCalledWith("session-1", 0, [
      expect.objectContaining({
        page: 0,
        pageId: "page-1",
        lineId: "page-1:l0",
        originalHash: "hash-1",
        original: "ORIGINAL TEXT",
        text: "EDITED TEXT",
      }),
    ], expect.objectContaining({ signal: expect.any(AbortSignal) }));
    prompt.mockRestore();
  });

  it("supports undo and redo for unsaved text drafts", async () => {
    const { container } = renderEditor();
    await openSample(container);
    fireEvent.change(container.querySelector("textarea")!, { target: { value: "LOCAL DRAFT" } });

    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    expect((container.querySelector("textarea") as HTMLTextAreaElement).value).toBe("ORIGINAL TEXT");
    fireEvent.click(screen.getByRole("button", { name: "重做" }));
    expect((container.querySelector("textarea") as HTMLTextAreaElement).value).toBe("LOCAL DRAFT");
  });

  it("keeps text entered while a save is running", async () => {
    let finishSave!: (value: unknown) => void;
    mocks.applyEdits.mockReturnValue(new Promise((resolve) => { finishSave = resolve; }));
    const { container } = renderEditor();
    await openSample(container);
    fireEvent.change(container.querySelector("textarea")!, { target: { value: "SUBMITTED TEXT" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(mocks.applyEdits).toHaveBeenCalled());

    const textbox = container.querySelector("textarea") as HTMLTextAreaElement;
    expect(textbox.readOnly).toBe(false);
    fireEvent.change(textbox, { target: { value: "NEWER LOCAL TEXT" } });
    mocks.fetchPageText.mockResolvedValue({
      docId: "session-1",
      revision: 1,
      page: {
        ...page,
        lines: [{ ...page.lines[0]!, id: "page-1:l1", text: "SUBMITTED TEXT", originalHash: "hash-submitted" }],
      },
    });
    finishSave({
      ok: true,
      pageCount: 1,
      revision: 1,
      pages: [{ page: 0, pageId: "page-1", width: 595, height: 842, rotation: 0 }],
      changedPageIds: ["page-1"],
    });

    await waitFor(() => expect((container.querySelector("textarea") as HTMLTextAreaElement).value).toBe("NEWER LOCAL TEXT"));
    expect(await screen.findByText(/保存期间的新修改仍保留/)).toBeDefined();
    expect(await screen.findByText("未保存")).toBeDefined();
  });

  it("shows a retryable editor preview error and recovers on retry", async () => {
    const failedTask = {
      promise: new Promise<never>((_resolve, reject) => {
        window.setTimeout(() => reject(new Error("temporary PDF read failure")), 0);
      }),
      destroy: vi.fn().mockResolvedValue(undefined),
    };
    const document = {
      numPages: 1,
      getPage: vi.fn().mockResolvedValue({
        getViewport: () => ({ width: 714, height: 1010 }),
        render: () => ({ promise: Promise.resolve(), cancel: vi.fn() }),
        cleanup: vi.fn(),
      }),
    };
    const retryTask = { promise: Promise.resolve(document), destroy: vi.fn().mockResolvedValue(undefined) };
    mocks.openPdfDocument.mockResolvedValueOnce(failedTask).mockResolvedValueOnce(retryTask);
    const { container } = renderEditor();
    await openSample(container);

    expect(await screen.findByText("temporary PDF read failure")).toBeDefined();
    expect(screen.getByRole("button", { name: "重试预览" })).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "重试预览" }));
    await waitFor(() => expect(mocks.openPdfDocument).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("第 1 页")).toBeDefined();
    expect(screen.queryByText("temporary PDF read failure")).toBeNull();
    expect(failedTask.destroy).toHaveBeenCalledTimes(1);
  });

  it("shows and retries an editor canvas rendering error", async () => {
    const getContext = vi.spyOn(HTMLCanvasElement.prototype, "getContext")
      .mockReturnValue({} as CanvasRenderingContext2D);
    let attempts = 0;
    const document = {
      numPages: 1,
      getPage: vi.fn().mockResolvedValue({
        getViewport: () => ({ width: 714, height: 1010 }),
        render: () => ({
          promise: ++attempts === 1 ? Promise.reject(new Error("editor canvas failure")) : Promise.resolve(),
          cancel: vi.fn(),
        }),
        cleanup: vi.fn(),
      }),
    };
    mocks.openPdfDocument.mockResolvedValue({
      promise: Promise.resolve(document),
      destroy: vi.fn().mockResolvedValue(undefined),
    });
    const { container } = renderEditor();
    await openSample(container);

    expect(await screen.findByText("editor canvas failure")).toBeDefined();
    expect(screen.getByRole("button", { name: "重试此页" })).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "重试此页" }));
    await waitFor(() => expect(document.getPage).toHaveBeenCalledTimes(2));
    await waitFor(() => expect((container.querySelector("canvas") as HTMLCanvasElement).style.display).toBe("block"));
    expect(attempts).toBe(2);
    getContext.mockRestore();
  });

  it("keeps an overflowing replacement draft after the backend rejects the save", async () => {
    const overflowMessage = "替换文字在第 1 页无法完整放入编辑区域，请缩短文字后重试";
    const replacement = "LONG REPLACEMENT ".repeat(120);
    mocks.applyEdits.mockRejectedValue(new Error(overflowMessage));
    const { container } = renderEditor();
    await openSample(container);

    fireEvent.change(container.querySelector("textarea")!, { target: { value: replacement } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByText(overflowMessage)).toBeDefined();
    expect((container.querySelector("textarea") as HTMLTextAreaElement).value).toBe(replacement);
    expect(await screen.findByText("未保存")).toBeDefined();
    expect((screen.getByRole("button", { name: "保存" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("requires a non-sensitive confirmation before enabling local recovery and can clear it", async () => {
    const { container } = renderEditor();
    await openSample(container);
    fireEvent.change(container.querySelector("textarea")!, { target: { value: "CACHE ME" } });

    fireEvent.click(screen.getByRole("button", { name: "开启草稿恢复" }));
    expect(await screen.findByText(/缓存未加密/)).toBeDefined();
    expect(mocks.saveDraft).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "我确认文档非敏感，开启缓存" }));

    await waitFor(() => expect(mocks.saveDraft).toHaveBeenCalled());
    expect(mocks.fetchSessionPdf).toHaveBeenCalledWith("session-1");
    expect(mocks.saveDraft).toHaveBeenCalledWith(expect.objectContaining({
      id: "cache-1",
      name: "sample.pdf",
      blob: expect.any(Blob),
      edits: [expect.objectContaining({ text: "CACHE ME", originalHash: "hash-1" })],
      expiresAt: expect.any(Number),
    }));

    fireEvent.click(screen.getByRole("button", { name: "清除本地缓存" }));
    await waitFor(() => expect(mocks.deleteDraft).toHaveBeenCalledWith("cache-1"));
  });

  it("reports quota failure as an unsaved cache and offers a retry", async () => {
    mocks.saveDraft.mockRejectedValue(new DOMException("quota", "QuotaExceededError"));
    const { container } = renderEditor();
    await openSample(container);
    fireEvent.click(screen.getByRole("button", { name: "开启草稿恢复" }));
    expect(await screen.findByText(/缓存未加密/)).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "我确认文档非敏感，开启缓存" }));

    expect(await screen.findByText("未保存缓存")).toBeDefined();
    expect(await screen.findByText("未保存本地缓存：浏览器空间不足")).toBeDefined();
    expect(screen.getByRole("button", { name: "重试缓存" })).toBeDefined();
  });

  it("restores cached text by page, hash, and bounding box with empty server history", async () => {
    const record = {
      id: "cache-restore",
      name: "sample.pdf",
      pageCount: 1,
      sizeBytes: 3,
      createdAt: 1,
      updatedAt: 2,
      expiresAt: Date.now() + 100_000,
      editCount: 1,
      blob: new Blob(["pdf"], { type: "application/pdf" }),
      edits: [{ page: 0, originalHash: "hash-1", bbox: [72, 80, 180, 100], original: "ORIGINAL TEXT", text: "RECOVERED TEXT" }],
    };
    mocks.listDrafts.mockResolvedValue([{ ...record, blob: undefined, edits: undefined }]);
    mocks.getDraft.mockResolvedValue(record);
    mocks.openEditor.mockResolvedValue({
      docId: "restored-session", originalName: "sample.pdf", pageCount: 1, revision: 0,
      pages: [{ page: 0, pageId: "new-page-id", width: 595, height: 842, rotation: 0 }],
    });
    mocks.fetchPageText.mockResolvedValue({
      docId: "restored-session", revision: 0,
      page: { ...page, pageId: "new-page-id", lines: [{ ...page.lines[0]!, id: "new-page-id:l0", pageId: "new-page-id" }] },
    });
    const { container } = renderEditor();
    fireEvent.click(await screen.findByRole("button", { name: "恢复" }));

    await waitFor(() => expect(container.querySelector("textarea")).not.toBeNull());
    await waitFor(() => expect((container.querySelector("textarea") as HTMLTextAreaElement).value).toBe("RECOVERED TEXT"));
    expect(mocks.openEditor).toHaveBeenCalledWith(expect.any(File), expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(mocks.fetchHistory).toHaveBeenCalledWith("restored-session");
    expect(await screen.findByText(/撤销历史从空状态开始/)).toBeDefined();
  });

  it("loads text and renders only pages near the viewport", async () => {
    const observers: Array<{
      callback: IntersectionObserverCallback;
      targets: Element[];
    }> = [];
    class MockIntersectionObserver {
      readonly callback: IntersectionObserverCallback;
      readonly targets: Element[] = [];
      constructor(callback: IntersectionObserverCallback) {
        this.callback = callback;
        observers.push(this);
      }
      observe(target: Element) { this.targets.push(target); }
      unobserve() {}
      disconnect() {}
      takeRecords() { return []; }
      root = null;
      rootMargin = "";
      thresholds = [0];
    }
    vi.stubGlobal("IntersectionObserver", MockIntersectionObserver);
    const pages = [0, 1, 2].map((index) => ({
      page: index,
      pageId: `page-${index + 1}`,
      width: 595,
      height: 842,
      rotation: 0,
    }));
    mocks.openEditor.mockResolvedValue({
      docId: "session-1",
      originalName: "large.pdf",
      pageCount: pages.length,
      revision: 0,
      pages,
    });

    const { container } = renderEditor();
    const input = container.querySelector<HTMLInputElement>('input[accept=".pdf,application/pdf"]')!;
    fireEvent.change(input, {
      target: { files: [new File(["pdf"], "large.pdf", { type: "application/pdf" })] },
    });
    await waitFor(() => expect(container.querySelectorAll("[data-page-number]")).toHaveLength(2));

    for (const observer of observers) {
      for (const target of observer.targets) {
        const visible = target.getAttribute("data-page-number") === "0";
        observer.callback(
          [{ target, isIntersecting: visible } as IntersectionObserverEntry],
          observer as unknown as IntersectionObserver,
        );
      }
    }

    await waitFor(() => expect(mocks.fetchPageText).toHaveBeenCalledTimes(1));
    expect(mocks.fetchPageText).toHaveBeenCalledWith("session-1", 0);
    expect(mocks.openPdfDocument).toHaveBeenCalledWith(
      "/api/v1/edit/session-1/download",
      expect.objectContaining({ shouldCancel: expect.any(Function) }),
    );
    const textbox = container.querySelector("textarea")!;
    fireEvent.focus(textbox);
    expect(textbox.style.background).toBe("rgb(255, 255, 255)");
    const observer = observers.find((entry) => entry.targets.some((target) => target.getAttribute("data-page-number") === "0"))!;
    const target = observer.targets.find((node) => node.getAttribute("data-page-number") === "0")!;
    act(() => observer.callback(
      [{ target, isIntersecting: false } as IntersectionObserverEntry],
      observer as unknown as IntersectionObserver,
    ));
    await waitFor(() => expect(container.querySelector("textarea")).toBeNull());
    act(() => observer.callback(
      [{ target, isIntersecting: true } as IntersectionObserverEntry],
      observer as unknown as IntersectionObserver,
    ));
    await waitFor(() => expect(container.querySelector("textarea")).not.toBeNull());
    expect(container.querySelector("textarea")!.style.background).toBe("transparent");
    expect(container.querySelector("textarea")!.style.color).toBe("transparent");
  });

  it("keeps a 300-page document within a small rendered window and requests metadata by slice", async () => {
    const pages = Array.from({ length: 300 }, (_, index) => ({
      page: index,
      pageId: `page-${index + 1}`,
      width: 595,
      height: 842,
      rotation: 0,
    }));
    mocks.openEditor.mockResolvedValue({
      docId: "session-1",
      originalName: "three-hundred-pages.pdf",
      pageCount: 300,
      revision: 0,
      pages: pages.slice(0, 50),
    });
    mocks.fetchPages.mockImplementation(async (docId: string, offset: number, limit: number) => ({
      docId,
      revision: 0,
      pageCount: 300,
      pages: pages.slice(offset, offset + limit),
    }));
    const { container } = renderEditor();
    const input = container.querySelector<HTMLInputElement>('input[accept=".pdf,application/pdf"]')!;
    fireEvent.change(input, {
      target: { files: [new File(["pdf"], "three-hundred-pages.pdf", { type: "application/pdf" })] },
    });
    await waitFor(() => expect(container.querySelectorAll("[data-page-number]").length).toBeLessThan(10));
    expect(container.querySelectorAll("[data-page-number]")).not.toHaveLength(300);

    await new Promise((resolve) => setTimeout(resolve, 20));
    const list = container.querySelector("[aria-label='PDF 页面列表']")!;
    vi.spyOn(list, "getBoundingClientRect").mockImplementation(() => ({
      top: -window.scrollY,
      bottom: -window.scrollY + 300_000,
      left: 0,
      right: 1000,
      width: 1000,
      height: 300_000,
      x: 0,
      y: -window.scrollY,
      toJSON: () => ({}),
    } as DOMRect));
    Object.defineProperty(window, "scrollY", { configurable: true, value: 100 * (842 * 1.2 + 16) });
    expect(window.scrollY).toBeGreaterThan(100_000);
    fireEvent.scroll(window);
    fireEvent.scroll(document);
    await waitFor(() => expect(container.querySelector("[aria-label='PDF 页面列表']")?.getAttribute("data-rendered-page-start")).toBe("99"));
    await waitFor(() => expect(mocks.fetchPages).toHaveBeenCalledWith("session-1", 100, 50));
    expect(container.querySelectorAll("[data-page-number]").length).toBeLessThan(10);
  });

  it.each([90, 180, 270])("hides text overlays and disables coordinate editing on %i-degree pages", async (rotation) => {
    const rotatedPage = { ...page, rotation };
    mocks.openEditor.mockResolvedValue({
      docId: "session-1",
      originalName: "sample.pdf",
      pageCount: 1,
      revision: 0,
      pages: [rotatedPage],
      textModel: [rotatedPage],
    });
    const { container } = renderEditor();
    await openSample(container, false);
    expect(container.querySelector("textarea")).toBeNull();
    expect(screen.getByTestId("rotation-notice-0").textContent).toContain("文字覆盖层已隐藏");
    const pageSurface = container.querySelector("canvas")?.parentElement;
    expect(pageSurface).toBeDefined();

    fireEvent.click(screen.getByRole("radio", { name: "加文字" }));
    fireEvent.click(pageSurface!);
    expect(await screen.findByText("旋转页暂不支持坐标编辑，请先归一化页面")).toBeDefined();

    fireEvent.click(screen.getByRole("radio", { name: "加图片" }));
    fireEvent.click(pageSurface!);
    fireEvent.click(screen.getByRole("radio", { name: "视觉遮挡" }));
    fireEvent.mouseDown(pageSurface!, { clientX: 10, clientY: 10 });
    fireEvent.mouseMove(pageSurface!, { clientX: 50, clientY: 50 });
    fireEvent.mouseUp(pageSurface!);

    expect(mocks.addText).not.toHaveBeenCalled();
    expect(mocks.addImage).not.toHaveBeenCalled();
    expect(mocks.applyEdits).not.toHaveBeenCalled();
  });
});
