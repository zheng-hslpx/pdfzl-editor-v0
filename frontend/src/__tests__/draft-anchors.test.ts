import { describe, expect, it } from "vitest";
import type { TextLine } from "../api/editor";
import { findUniqueAnchoredLine, isAnchorForCurrentLine } from "../lib/draftAnchors";
import type { DraftAnchor } from "../lib/draftStore";

const anchor: DraftAnchor = {
  lineId: "page-a:l0",
  page: 0,
  pageId: "page-a",
  revision: 0,
  originalHash: "hash-original-a",
  bbox: [40, 54.95, 201.83, 74.19],
  original: "PAGE1 FIRST ORIGINAL",
  text: "FIRST SAVED VALUE",
};

const line = (id: string, text: string, bbox: number[], originalHash: string): TextLine => ({
  id,
  pageId: "page-a",
  text,
  bbox,
  originalHash,
  fontSize: 14,
  font: "helv",
  color: "#000000",
});

describe("versioned text draft anchors", () => {
  it("resolves a saved target by page, committed content, and position after ordinal IDs reorder", () => {
    const secondRow = line("page-a:l0", "PAGE1 SECOND ORIGINAL", [40, 94.95, 221.27, 114.19], "hash-original-b");
    const firstRow = line("page-a:l1", "FIRST SAVED VALUE", [40, 54.95, 155.33, 70.62], "hash-saved-a");

    expect(findUniqueAnchoredLine([secondRow, firstRow], anchor, "FIRST SAVED VALUE")).toEqual(firstRow);
    expect(isAnchorForCurrentLine({ ...anchor, ...{
      lineId: firstRow.id,
      revision: 1,
      originalHash: firstRow.originalHash,
      bbox: firstRow.bbox,
      original: firstRow.text,
    } }, firstRow, 1)).toBe(true);
  });

  it("rejects missing or ambiguous targets instead of selecting the closest ordinal row", () => {
    const wrongRow = line("page-a:l0", "PAGE1 SECOND ORIGINAL", [40, 94.95, 221.27, 114.19], "hash-original-b");
    const firstMatch = line("page-a:l1", "FIRST SAVED VALUE", [40, 54.95, 155.33, 70.62], "hash-saved-a");
    const duplicateAtTarget = line("page-a:l2", "FIRST SAVED VALUE", [40.1, 55.0, 170.0, 71.0], "hash-saved-a-duplicate");

    expect(findUniqueAnchoredLine([wrongRow], anchor, "FIRST SAVED VALUE")).toBeNull();
    expect(findUniqueAnchoredLine([firstMatch, duplicateAtTarget], anchor, "FIRST SAVED VALUE")).toBeNull();
  });

  it("requires original content, hash, and full geometry for an unmodified target", () => {
    const changedGeometry = line("page-a:l1", anchor.original, [42, 54.95, 203.83, 74.19], anchor.originalHash);
    const changedContent = line("page-a:l1", "PAGE1 SECOND ORIGINAL", anchor.bbox, anchor.originalHash);
    const valid = line("page-a:l1", anchor.original, anchor.bbox, anchor.originalHash);

    expect(findUniqueAnchoredLine([changedGeometry], anchor)).toBeNull();
    expect(findUniqueAnchoredLine([changedContent], anchor)).toBeNull();
    expect(findUniqueAnchoredLine([valid], anchor)).toEqual(valid);
  });
});
