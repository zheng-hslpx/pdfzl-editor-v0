import { describe, expect, it } from "vitest";
import { resolveOutputKind } from "../lib/resultFormat";

describe("resolveOutputKind", () => {
  it.each([
    ["document.pdf", "application/pdf", "other", "pdf"],
    ["document.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "pdf", "docx"],
    ["document_images.zip", "application/zip", "image", "zip"],
    ["document_page_1.png", "image/png", "zip", "image"],
    ["document.txt", "text/plain; charset=utf-8", "pdf", "text"],
    ["document.json", "application/octet-stream", "pdf", "json"],
    ["legacy.doc", "application/octet-stream", "other", "docx"],
    ["unknown.bin", "application/octet-stream", "pdf", "pdf"],
    ["unknown.bin", "application/octet-stream", "other", "other"],
    ["mismatch.docx", "application/pdf", "pdf", "other"],
  ] as const)("resolves %s (%s) to %s", (filename, mimeType, fallback, expected) => {
    expect(resolveOutputKind(filename, mimeType, fallback)).toBe(expected);
  });
});
