import { beforeEach, describe, expect, it, vi } from "vitest";

const { getDocument } = vi.hoisted(() => ({ getDocument: vi.fn() }));

vi.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: { workerSrc: "" },
  getDocument,
}));

import { openPdfDocument } from "../lib/pdfjs";

describe("openPdfDocument byte ownership", () => {
  const task = { promise: Promise.resolve({}), destroy: vi.fn().mockResolvedValue(undefined) };

  beforeEach(() => {
    getDocument.mockReset();
    task.destroy.mockReset().mockResolvedValue(undefined);
  });

  it.each([
    ["Uint8Array", () => new Uint8Array([1, 2, 3])],
    ["ArrayBuffer", () => new Uint8Array([1, 2, 3]).buffer],
  ])("copies %s before the worker takes buffer ownership", async (_label, createSource) => {
    getDocument.mockImplementation(({ data }: { data: Uint8Array | ArrayBuffer }) => {
      const buffer = data instanceof Uint8Array ? data.buffer : data;
      structuredClone(buffer, { transfer: [buffer] });
      return task;
    });
    const source = createSource();
    const sourceBuffer = source instanceof Uint8Array ? source.buffer : source;

    await openPdfDocument(source, { copyData: true });

    expect(sourceBuffer.byteLength).toBe(3);
    expect(getDocument).toHaveBeenCalledTimes(1);
  });

  it("does not create a task when the owner was cleaned up during module loading", async () => {
    const result = await openPdfDocument(new Uint8Array([1]), {
      copyData: true,
      shouldCancel: () => true,
    });

    expect(result).toBeNull();
    expect(getDocument).not.toHaveBeenCalled();
  });

  it("uses bundled CMaps and standard fonts for URL-based range loads", async () => {
    getDocument.mockReturnValue(task);

    await openPdfDocument("/api/v1/edit/session-1/download");

    expect(getDocument).toHaveBeenCalledWith(expect.objectContaining({
      url: "/api/v1/edit/session-1/download",
      cMapUrl: `${window.location.origin}/cmaps/`,
      cMapPacked: true,
      standardFontDataUrl: `${window.location.origin}/standard_fonts/`,
      disableStream: true,
    }));
  });
});
