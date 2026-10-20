import { useState } from "react";
import { Text, TextInput } from "@mantine/core";
import { ToolFlow } from "../components/ToolFlow";
import { postFiles } from "../api/client";

export default function ReorderPagesTool() {
  const [pageOrder, setPageOrder] = useState("");

  return (
    <ToolFlow
      minFiles={1}
      maxFiles={1}
      accepts=".pdf,application/pdf"
      resultKind="pdf"
      settings={
        <div style={{ display: "grid", gap: 12 }}>
          <TextInput
            label="新的页面顺序"
            value={pageOrder}
            onChange={(e) => setPageOrder(e.currentTarget.value)}
            placeholder="例如 3,1,2"
            required
          />
          <Text size="xs" c="dimmed">
            输入 1 到总页数的一个排列，逗号分隔。例如 3 页文档输入「3,1,2」表示原第 3 页排到最前。
          </Text>
        </div>
      }
      execute={async (managed, jobOptions) => {
        const form = new FormData();
        form.append("fileInput", managed[0]!.file, managed[0]!.name);
        form.append("pageOrder", pageOrder);
        return postFiles("/general/reorder-pages", form, jobOptions);
      }}
    />
  );
}
