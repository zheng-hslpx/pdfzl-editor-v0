import { useState } from "react";
import { Select, TextInput } from "@mantine/core";
import { ToolFlow } from "../components/ToolFlow";
import { postFiles } from "../api/client";

export default function RotateTool() {
  const [angle, setAngle] = useState<string | null>("90");
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
            label="旋转角度"
            data={[
              { value: "90", label: "顺时针 90°" },
              { value: "180", label: "180°" },
              { value: "270", label: "顺时针 270°" },
            ]}
            value={angle}
            onChange={setAngle}
          />
          <TextInput
            label="页码范围"
            value={pages}
            onChange={(e) => setPages(e.currentTarget.value)}
            description="例如 1,3-5 或 all"
          />
        </div>
      }
      execute={async (managed) => {
        const form = new FormData();
        form.append("fileInput", managed[0]!.file, managed[0]!.name);
        form.append("angle", angle ?? "90");
        form.append("pages", pages);
        return postFiles("/general/rotate", form);
      }}
    />
  );
}
