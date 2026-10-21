import { useState } from "react";
import { NumberInput, Select, TextInput } from "@mantine/core";
import { ToolFlow } from "../components/ToolFlow";
import { postFiles } from "../api/client";

type Direction = "pdf-to-image" | "pdf-to-text" | "pdf-to-word" | "image-to-pdf" | "office-to-pdf";

const DIRECTIONS: { value: Direction; label: string; accepts: string; multi: boolean }[] = [
  { value: "pdf-to-image", label: "PDF → 图片（PNG/JPEG）", accepts: ".pdf,application/pdf", multi: false },
  { value: "pdf-to-text", label: "PDF → 文本（TXT）", accepts: ".pdf,application/pdf", multi: false },
  { value: "pdf-to-word", label: "PDF → Word（DOCX，格式可能丢失）", accepts: ".pdf,application/pdf", multi: false },
  { value: "image-to-pdf", label: "图片 → PDF", accepts: "image/*", multi: true },
  { value: "office-to-pdf", label: "Office → PDF（LibreOffice）", accepts: ".doc,.docx,.xls,.xlsx,.ppt,.pptx,.odt,.ods,.odp,.rtf,.txt,.csv,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document", multi: false },
];

/**
 * Conversion tool: one page dispatching to the five /convert endpoints,
 * selected by direction. Per-direction parameters are shown inline.
 */
export default function ConvertTool() {
  const [direction, setDirection] = useState<Direction>("pdf-to-image");
  const [imageFormat, setImageFormat] = useState("png");
  const [dpi, setDpi] = useState<number | string>(150);
  const [pages, setPages] = useState("all");
  const [pageSize, setPageSize] = useState("a4");

  const meta = DIRECTIONS.find((d) => d.value === direction)!;

  return (
    <ToolFlow
      minFiles={1}
      maxFiles={meta.multi ? -1 : 1}
      accepts={meta.accepts}
      resultKind="auto"
      notices={
        direction === "pdf-to-word"
          ? ["PDF 转 Word 为尽力而为的版式重建：复杂排版、扫描件可能丢失格式，请转换后校对。"]
          : direction === "office-to-pdf"
            ? ["Office 转 PDF 依赖本机 LibreOffice；首次转换需初始化其配置（约 10-30 秒）。"]
            : undefined
      }
      settings={
        <div style={{ display: "grid", gap: 12 }}>
          <Select
            label="转换方向"
            data={DIRECTIONS.map((d) => ({ value: d.value, label: d.label }))}
            value={direction}
            onChange={(v) => v && setDirection(v as Direction)}
          />
          {direction === "pdf-to-image" && (
            <>
              <Select
                label="图片格式"
                data={[
                  { value: "png", label: "PNG（无损）" },
                  { value: "jpeg", label: "JPEG（体积小）" },
                ]}
                value={imageFormat}
                onChange={(v) => v && setImageFormat(v)}
              />
              <NumberInput
                label="DPI（清晰度）"
                value={dpi}
                onChange={setDpi}
                min={36}
                max={600}
                description="72=屏幕查看，150=默认，300=打印"
              />
              <TextInput
                label="页码范围"
                value={pages}
                onChange={(e) => setPages(e.currentTarget.value)}
                description="例如 1,3-5 或 all"
              />
            </>
          )}
          {direction === "image-to-pdf" && (
            <Select
              label="页面尺寸"
              data={[
                { value: "a4", label: "A4" },
                { value: "letter", label: "Letter" },
                { value: "source", label: "按图片原尺寸" },
              ]}
              value={pageSize}
              onChange={(v) => v && setPageSize(v)}
            />
          )}
        </div>
      }
      execute={async (managed, jobOptions) => {
        const form = new FormData();
        if (meta.multi) {
          managed.forEach((f) => form.append("fileInput", f.file, f.name));
        } else {
          form.append("fileInput", managed[0]!.file, managed[0]!.name);
        }
        switch (direction) {
          case "pdf-to-image":
            form.append("format", imageFormat);
            form.append("dpi", String(dpi));
            form.append("pages", pages);
            return postFiles("/convert/pdf-to-image", form, jobOptions);
          case "pdf-to-text":
            return postFiles("/convert/pdf-to-text", form, jobOptions);
          case "pdf-to-word":
            return postFiles("/convert/pdf-to-word", form, jobOptions);
          case "image-to-pdf":
            form.append("pageSize", pageSize);
            return postFiles("/convert/image-to-pdf", form, jobOptions);
          case "office-to-pdf":
            return postFiles("/convert/office-to-pdf", form, jobOptions);
        }
      }}
    />
  );
}
