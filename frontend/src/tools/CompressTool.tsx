import { Select } from "@mantine/core";
import { ToolFlow } from "../components/ToolFlow";
import { postFiles } from "../api/client";

export default function CompressTool() {
  return (
    <ToolFlow
      minFiles={1}
      maxFiles={1}
      accepts=".pdf,application/pdf"
      resultKind="pdf"
      notices={["执行 PDF 对象优化；不会对图片降采样，也不提供图像质量档位。"]}
      settings={
        <Select
          label="优化方式"
          data={[{ value: "medium", label: "对象优化" }]}
          value="medium"
          readOnly
        />
      }
      execute={async (managed, jobOptions) => {
        const form = new FormData();
        form.append("fileInput", managed[0]!.file, managed[0]!.name);
        form.append("level", "medium");
        return postFiles("/misc/compress", form, jobOptions);
      }}
    />
  );
}
