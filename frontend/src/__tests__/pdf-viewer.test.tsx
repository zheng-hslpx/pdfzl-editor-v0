import { MantineProvider } from "@mantine/core";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { PDFDocumentLoadingTask, PDFDocumentProxy } from "pdfjs-dist";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ openPdfDocument: vi.fn() }));
vi.mock("../lib/pdfjs", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/pdfjs")>()),
  openPdfDocument: mocks.openPdfDocument,
}));

import { PdfViewer } from "../components/PdfViewer";

function renderViewer(data = new Blob(["test-pdf"])) {
  return render(
    <MantineProvider>
      <PdfViewer data={data} />
    </MantineProvider>,
  );
}

function makePdfPage(renderAttempt: () => Promise<void>) {
  return {
    getViewport: () => ({ width: 100, height: 140 }),
    render: () => ({ promise: renderAttempt(), cancel: vi.fn() }),
    cleanup: vi.fn(),
  };
}

function makeDocument(page: ReturnType<typeof makePdfPage>) {
  return {
    numPages: 1,
    getPage: vi.fn().mockResolvedValue(page),
  } as unknown as PDFDocumentProxy;
}

describe("PdfViewer failure recovery", () => {
  let createObjectUrlDescriptor: PropertyDescriptor | undefined;
  let revokeObjectUrlDescriptor: PropertyDescriptor | undefined;
  let getContextDescriptor: PropertyDescriptor | undefined;

  beforeEach(() => {
    mocks.openPdfDocument.mockReset();
    createObjectUrlDescriptor = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
    revokeObjectUrlDescriptor = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");
    getContextDescriptor = Object.getOwnPropertyDescriptor(HTMLCanvasElement.prototype, "getContext");
    let urlNumber = 0;
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => `blob:pdf-preview-${++urlNumber}`),
    });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
      configurable: true,
      value: vi.fn(() => ({})),
    });
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    if (createObjectUrlDescriptor) Object.defineProperty(URL, "createObjectURL", createObjectUrlDescriptor);
    else delete (URL as typeof URL & { createObjectURL?: unknown }).createObjectURL;
    if (revokeObjectUrlDescriptor) Object.defineProperty(URL, "revokeObjectURL", revokeObjectUrlDescriptor);
    else delete (URL as typeof URL & { revokeObjectURL?: unknown }).revokeObjectURL;
    if (getContextDescriptor) Object.defineProperty(HTMLCanvasElement.prototype, "getContext", getContextDescriptor);
  });

  it("shows a load error with retry when the PDF.js task rejects", async () => {
    const destroy = vi.fn().mockResolvedValue(undefined);
    const document = makeDocument(makePdfPage(() => Promise.resolve()));
    mocks.openPdfDocument
      .mockRejectedValueOnce(new Error("Invalid PDF structure"))
      .mockResolvedValueOnce({ promise: Promise.resolve(document), destroy } satisfies Partial<PDFDocumentLoadingTask>);

    renderViewer();

    expect(await screen.findByText(/Invalid PDF structure/)).toBeDefined();
    expect(screen.getByRole("button", { name: "重试预览" })).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "重试预览" }));
    expect(await screen.findByText("第 1 页")).toBeDefined();
    expect(mocks.openPdfDocument).toHaveBeenCalledTimes(2);
  });

  it("times out a hung open, destroys before revoking, and allows a successful retry", async () => {
    vi.useFakeTimers();
    const lifecycle: string[] = [];
    let resolveFirst!: (document: PDFDocumentProxy) => void;
    const firstTask = {
      promise: new Promise<PDFDocumentProxy>((resolve) => { resolveFirst = resolve; }),
      destroy: vi.fn(async () => { lifecycle.push("destroy"); }),
    };
    const secondTask = {
      promise: Promise.resolve(makeDocument(makePdfPage(() => Promise.resolve()))),
      destroy: vi.fn(async () => { lifecycle.push("destroy-retry"); }),
    };
    mocks.openPdfDocument.mockResolvedValueOnce(firstTask).mockResolvedValueOnce(secondTask);
    const revoke = vi.mocked(URL.revokeObjectURL);
    revoke.mockImplementation(() => { lifecycle.push("revoke"); });

    renderViewer();
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });

    expect(screen.getByText(/加载超时/)).toBeDefined();
    expect(firstTask.destroy).toHaveBeenCalledTimes(1);
    expect(lifecycle.slice(0, 2)).toEqual(["destroy", "revoke"]);
    fireEvent.click(screen.getByRole("button", { name: "重试预览" }));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(screen.getByText("第 1 页")).toBeDefined();
    expect(mocks.openPdfDocument).toHaveBeenCalledTimes(2);
    resolveFirst(makeDocument(makePdfPage(() => Promise.resolve())));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(screen.queryByText("PDF 预览失败")).toBeNull();
  });

  it("destroys an in-flight task before releasing its Blob URL on unmount", async () => {
    const lifecycle: string[] = [];
    const task = {
      promise: new Promise<PDFDocumentProxy>(() => undefined),
      destroy: vi.fn(async () => { lifecycle.push("destroy"); }),
    };
    mocks.openPdfDocument.mockResolvedValue(task);
    vi.mocked(URL.revokeObjectURL).mockImplementation(() => { lifecycle.push("revoke"); });

    const { unmount } = renderViewer();
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    unmount();
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(task.destroy).toHaveBeenCalledTimes(1);
    expect(lifecycle).toEqual(["destroy", "revoke"]);
  });

  it("shows render failures and retries the affected page", async () => {
    let attempts = 0;
    const page = makePdfPage(() => {
      attempts += 1;
      return attempts === 1 ? Promise.reject(new Error("canvas render failed")) : Promise.resolve();
    });
    const document = makeDocument(page);
    mocks.openPdfDocument.mockResolvedValue({
      promise: Promise.resolve(document),
      destroy: vi.fn().mockResolvedValue(undefined),
    });

    const { container } = renderViewer();

    expect(await screen.findByRole("button", { name: "重试此页" })).toBeDefined();
    expect(screen.getByText("canvas render failed")).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "重试此页" }));
    await waitFor(() => expect(document.getPage).toHaveBeenCalledTimes(2));
    await waitFor(() => expect((container.querySelector("canvas") as HTMLCanvasElement).style.display).toBe("block"));
    expect(attempts).toBe(2);
  });
});
