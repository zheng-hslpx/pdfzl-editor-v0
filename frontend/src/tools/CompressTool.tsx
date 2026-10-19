import { useState } from "react";
import { Select } from "@mantine/core";
import { ToolFlow } from "../components/ToolFlow";
import { postFiles } from "../api/client";

const LEVELS = [
  { value: "low", label: "轻度 — 仅清理冗余对象（最快）" },
  { value: "medium", label: "中度 — 清理 + 压缩流（推荐）" },
  { value: "high", label: "深度 — 中度 + 字体子集化（最慢）" },
];

export default function CompressTool() {
  const [level, setLevel] = useState("medium");

  return (
    <ToolFlow
      minFiles={1}
      maxFiles={1}
      accepts=".pdf,application/pdf"
      resultKind="pdf"
      settings={
        <Select
          label="压缩级别"
          data={LEVELS}
          value={level}
          onChange={(v) => v && setLevel(v)}
        />
      }
      execute={async (managed) => {
        const form = new FormData();
        form.append("fileInput", managed[0]!.file, managed[0]!.name);
        form.append("level", level);
        return postFiles("/misc/compress", form);
      }}
    />
  );
}
