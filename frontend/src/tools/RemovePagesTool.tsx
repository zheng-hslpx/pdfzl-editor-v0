import { useState } from "react";
import { Text, TextInput } from "@mantine/core";
import { ToolFlow } from "../components/ToolFlow";
import { postFiles } from "../api/client";

export default function RemovePagesTool() {
  const [pages, setPages] = useState("");

  return (
    <ToolFlow
      minFiles={1}
      maxFiles={1}
      accepts=".pdf,application/pdf"
      resultKind="pdf"
      settings={
        <div style={{ display: "grid", gap: 12 }}>
          <TextInput
            label="要删除的页码"
            value={pages}
            onChange={(e) => setPages(e.currentTarget.value)}
            placeholder="例如 2,5,8-10"
            required
          />
          <Text size="xs" c="dimmed">
            不能删除全部页面。页码从 1 开始计数。
          </Text>
        </div>
      }
      execute={async (managed, jobOptions) => {
        const form = new FormData();
        form.append("fileInput", managed[0]!.file, managed[0]!.name);
        form.append("pages", pages);
        return postFiles("/general/remove-pages", form, jobOptions);
      }}
    />
  );
}
