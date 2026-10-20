import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FileProvider } from "../contexts/FileContext";
import { EditorPage } from "../pages/EditorPage";

const mocks = vi.hoisted(() => ({
  openEditor: vi.fn(),
  fetchSessionBytes: vi.fn(),
  applyEdits: vi.fn(),
  addText: vi.fn(),
  addImage: vi.fn(),
  rotatePage: vi.fn(),
  deletePages: vi.fn(),
  movePage: vi.fn(),
  saveEditedAs: vi.fn(),
  closeSession: vi.fn(),
  touchSession: vi.fn(),
  openPdfDocument: vi.fn(),
}));

vi.mock("../api/editor", () => mocks);
vi.mock("../lib/pdfjs", () => ({ openPdfDocument: mocks.openPdfDocument }));

afterEach(async () => {
  cleanup();
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

function renderEditor() {
  return render(
    <MantineProvider>
      <FileProvider>
        <EditorPage />
      </FileProvider>
    </MantineProvider>,
  );
}

async function openSample(container: HTMLElement) {
  const input = container.querySelector<HTMLInputElement>('input[accept=".pdf,application/pdf"]');
  expect(input).toBeDefined();
  fireEvent.change(input!, {
    target: { files: [new File(["pdf"], "sample.pdf", { type: "application/pdf" })] },
  });
  await waitFor(() => expect(container.querySelector("textarea")).not.toBeNull());
  await waitFor(() => expect(mocks.fetchSessionBytes).toHaveBeenCalled());
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
    mocks.fetchSessionBytes.mockResolvedValue(new Uint8Array([1, 2, 3]));
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
    mocks.openPdfDocument.mockResolvedValue({
      getPage: async () => ({
        getViewport: () => ({ width: 714, height: 1010 }),
        render: () => ({ promise: Promise.resolve(), cancel: () => undefined }),
        cleanup: () => undefined,
      }),
      destroy: () => undefined,
    });
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

  it("submits anchored edits before Save As", async () => {
    const prompt = vi.spyOn(window, "prompt").mockReturnValue("saved.pdf");
    const { container } = renderEditor();
    await openSample(container);
    fireEvent.change(container.querySelector("textarea"), { target: { value: "EDITED TEXT" } });

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
    ]);
    prompt.mockRestore();
  });

  it.each([90, 180, 270])("disables coordinate editing on %i-degree pages", async (rotation) => {
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
    await openSample(container);
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
