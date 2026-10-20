import { useState } from "react";
import { NumberInput, Select, TextInput } from "@mantine/core";
import { ToolFlow } from "../components/ToolFlow";
import { postFiles } from "../api/client";

const POSITIONS = [
  { value: "bottom-center", label: "底部居中" },
  { value: "bottom-left", label: "底部左侧" },
  { value: "bottom-right", label: "底部右侧" },
  { value: "top-center", label: "顶部居中" },
  { value: "top-left", label: "顶部左侧" },
  { value: "top-right", label: "顶部右侧" },
];

export default function PageNumbersTool() {
  const [position, setPosition] = useState("bottom-center");
  const [startNumber, setStartNumber] = useState<number | string>(1);
  const [fontSize, setFontSize] = useState<number | string>(10);
  const [margin, setMargin] = useState<number | string>(36);
  const [pages, setPages] = useState("all");

  return (
    <ToolFlow
      minFiles={1}
      maxFiles={1}
      accepts=".pdf,application/pdf"
      resultKind="pdf"
      settings={
        <div style={{ display: "grid", gap: 12 }}>
          <Select
            label="位置"
            data={POSITIONS}
            value={position}
            onChange={(v) => v && setPosition(v)}
          />
          <NumberInput
            label="起始页码"
            value={startNumber}
            onChange={setStartNumber}
            min={0}
          />
          <NumberInput
            label="字号"
            value={fontSize}
            onChange={setFontSize}
            min={6}
            max={48}
          />
          <NumberInput
            label="边距（pt）"
            value={margin}
            onChange={setMargin}
            min={0}
            max={200}
          />
          <TextInput
            label="页码范围"
            value={pages}
            onChange={(e) => setPages(e.currentTarget.value)}
            description="例如 1,3-5 或 all"
          />
        </div>
      }
      execute={async (managed, jobOptions) => {
        const form = new FormData();
        form.append("fileInput", managed[0]!.file, managed[0]!.name);
        form.append("position", position);
        form.append("startNumber", String(startNumber));
        form.append("fontSize", String(fontSize));
        form.append("margin", String(margin));
        form.append("pages", pages);
        return postFiles("/misc/add-page-numbers", form, jobOptions);
      }}
    />
  );
}
