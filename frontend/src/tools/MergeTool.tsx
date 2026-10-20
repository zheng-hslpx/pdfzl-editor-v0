import { Text } from "@mantine/core";
import { ToolFlow } from "../components/ToolFlow";
import { postFiles } from "../api/client";

/**
 * Merge tool: files are merged in the order shown in the file list
 * (reorderable via the up/down buttons in the Files step).
 */
export default function MergeTool() {
  return (
    <ToolFlow
      minFiles={2}
      maxFiles={-1}
      accepts=".pdf,application/pdf"
      resultKind="pdf"
      settings={
        <Text size="sm" c="dimmed">
          合并顺序即文件列表顺序（可在上一步用 ↑ ↓ 调整）。合并时会保留各文件的书签目录（TOC）。
        </Text>
      }
      execute={async (managed, jobOptions) => {
        const form = new FormData();
        managed.forEach((f) => form.append("fileInput", f.file, f.name));
        return postFiles("/general/merge-pdfs", form, jobOptions);
      }}
    />
  );
}
