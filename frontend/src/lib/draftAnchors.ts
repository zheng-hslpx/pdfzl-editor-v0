import type { TextLine } from "../api/editor";
import type { DraftAnchor } from "./draftStore";

const ORIGINAL_BBOX_TOLERANCE = 0.5;
const REBASED_POSITION_TOLERANCE = 3;

function bboxMatches(left: number[], right: number[], tolerance: number): boolean {
  return left.length === 4 && right.length === 4
    && left.every((value, index) => Math.abs(value - (right[index] ?? Number.NaN)) <= tolerance);
}

function sameReplacementLocation(line: TextLine, anchor: DraftAnchor): boolean {
  const [lineX, lineY, , lineBottom] = line.bbox;
  const [anchorX, anchorY, , anchorBottom] = anchor.bbox;
  return Number.isFinite(lineX) && Number.isFinite(lineY)
    && Number.isFinite(anchorX) && Number.isFinite(anchorY)
    && Math.abs(lineX! - anchorX!) <= REBASED_POSITION_TOLERANCE
    && Math.abs(lineY! - anchorY!) <= REBASED_POSITION_TOLERANCE
    && lineBottom! >= anchorY! - REBASED_POSITION_TOLERANCE
    && anchorBottom! >= lineY! - REBASED_POSITION_TOLERANCE;
}

/** 不依赖可重复使用的顺序行 ID 来解析文本身份。 */
export function findUniqueAnchoredLine(
  lines: TextLine[],
  anchor: DraftAnchor,
  expectedText = anchor.original,
): TextLine | null {
  const isOriginalContent = expectedText === anchor.original;
  const matches = lines.filter((line) => line.pageId === anchor.pageId
    && line.text === expectedText
    && (isOriginalContent
      ? line.originalHash === anchor.originalHash && bboxMatches(line.bbox, anchor.bbox, ORIGINAL_BBOX_TOLERANCE)
      : sameReplacementLocation(line, anchor)));
  return matches.length === 1 ? matches[0]! : null;
}

export function isAnchorForCurrentLine(
  anchor: DraftAnchor,
  line: TextLine,
  revision: number,
): boolean {
  return anchor.revision === revision
    && anchor.pageId === line.pageId
    && anchor.originalHash === line.originalHash
    && anchor.original === line.text
    && bboxMatches(anchor.bbox, line.bbox, ORIGINAL_BBOX_TOLERANCE);
}
