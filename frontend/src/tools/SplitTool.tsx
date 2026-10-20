import { useState } from "react";
import { TextInput } from "@mantine/core";
import { ToolFlow } from "../components/ToolFlow";
import { postFiles } from "../api/client";

/**
 * Split tool: each comma-separated range ("N" or "N-M") becomes one
 * output PDF; all outputs are returned as a ZIP.
 */
export default function SplitTool() {
  const [ranges, setRanges] = useState("1,3-5");

  return (
    <ToolFlow
      minFiles={1}
      maxFiles={1}
      accepts=".pdf,application/pdf"
      resultKind="zip"
      settings={
        <TextInput
          label="页码范围"
          description="每段生成一个 PDF：如「1,3-5」输出 2 个文件；输入 all 复制整篇"
          placeholder="例如 1,3-5 或 all"
          value={ranges}
          onChange={(e) => setRanges(e.currentTarget.value)}
        />
      }
      execute={async (managed, jobOptions) => {
        const form = new FormData();
        form.append("fileInput", managed[0]!.file, managed[0]!.name);
        form.append("pageRanges", ranges);
        return postFiles("/general/split-pages", form, jobOptions);
      }}
    />
  );
}
