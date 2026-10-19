import { Table, Text } from "@mantine/core";
import { ToolFlow } from "../components/ToolFlow";
import { postJson } from "../api/client";

interface PdfInfo {
  pageCount: number;
  pageSize: { width: number; height: number; rotation: number };
  pdfVersion: string;
  metadata: Record<string, string>;
  isEncrypted: boolean;
  fonts: string[];
  tocEntries: number;
  fileSize: number;
}

/** Read-only inspector: shows document metadata as a table. */
export default function InfoTool() {
  return (
    <ToolFlow
      minFiles={1}
      maxFiles={1}
      accepts=".pdf,application/pdf"
      resultKind="json"
      settings={<Text size="sm" c="dimmed">查看 PDF 的页数、尺寸、版本、元数据与字体列表。</Text>}
      executeJson={async (managed) => {
        const form = new FormData();
        form.append("fileInput", managed[0]!.file, managed[0]!.name);
        return postJson<PdfInfo>("/misc/pdf-info", form);
      }}
      renderJson={(raw) => {
        const info = raw as PdfInfo;
        const rows: [string, string][] = [
          ["页数", String(info.pageCount)],
          [
            "页面尺寸",
            `${info.pageSize.width} × ${info.pageSize.height} pt（旋转 ${info.pageSize.rotation}°）`,
          ],
          ["PDF 版本", info.pdfVersion || "未知"],
          ["文件大小", `${(info.fileSize / 1024).toFixed(1)} KB`],
          ["书签条目", String(info.tocEntries)],
          ["已加密", info.isEncrypted ? "是" : "否"],
          ["字体", info.fonts.length > 0 ? info.fonts.join("、") : "（无嵌入字体）"],
          ["标题", info.metadata?.title || "—"],
          ["作者", info.metadata?.author || "—"],
          ["主题", info.metadata?.subject || "—"],
          ["创建工具", info.metadata?.creator || "—"],
          ["创建日期", info.metadata?.creationDate || "—"],
        ];
        return (
          <Table striped highlightOnHover withTableBorder>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>属性</Table.Th>
                <Table.Th>值</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map(([k, v]) => (
                <Table.Tr key={k}>
                  <Table.Td fw={600}>{k}</Table.Td>
                  <Table.Td style={{ wordBreak: "break-all" }}>{v}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        );
      }}
    />
  );
}
