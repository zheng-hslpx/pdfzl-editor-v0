import { lazy, type ComponentType } from "react";
import {
  IconArrowsSort,
  IconCopy,
  IconCut,
  IconFileTypePdf,
  IconFileZip,
  IconHash,
  IconInfoCircle,
  IconPencil,
  IconPhoto,
  IconRotate,
  IconSticker,
  IconTransform,
} from "@tabler/icons-react";

/**
 * Tool registry — mirrors Stirling-PDF's useTranslatedToolCatalog pattern:
 * every tool is a lazy component plus metadata (name, category, limits).
 */
export type ToolCategory = "recommended" | "organize" | "convert" | "extra" | "edit";

export const CATEGORY_LABELS: Record<ToolCategory, string> = {
  recommended: "常用工具",
  organize: "页面组织",
  convert: "格式转换",
  edit: "编辑",
  extra: "更多工具",
};

export interface ToolDef {
  id: string;
  name: string;
  desc: string;
  category: ToolCategory;
  icon: ComponentType<{ size?: string | number }>;
  /** -1 = unlimited */
  maxFiles: number;
  accepts?: string;
  available: boolean;
  component?: React.LazyExoticComponent<ComponentType>;
}

export const TOOLS: ToolDef[] = [
  {
    id: "merge",
    name: "合并 PDF",
    desc: "将多个 PDF 按顺序合并为一个文件，保留书签目录",
    category: "recommended",
    icon: IconCopy,
    maxFiles: -1,
    accepts: ".pdf,application/pdf",
    available: true,
    component: lazy(() => import("../tools/MergeTool")),
  },
  {
    id: "split",
    name: "拆分 PDF",
    desc: "按页码范围拆分为多个 PDF，打包 ZIP 下载",
    category: "recommended",
    icon: IconCut,
    maxFiles: 1,
    accepts: ".pdf,application/pdf",
    available: true,
    component: lazy(() => import("../tools/SplitTool")),
  },
  {
    id: "rotate",
    name: "旋转页面",
    desc: "按 90/180/270 度旋转整篇或指定页",
    category: "organize",
    icon: IconRotate,
    maxFiles: 1,
    accepts: ".pdf,application/pdf",
    available: false,
  },
  {
    id: "remove-pages",
    name: "删除页面",
    desc: "删除指定页码",
    category: "organize",
    icon: IconFileZip,
    maxFiles: 1,
    accepts: ".pdf,application/pdf",
    available: false,
  },
  {
    id: "reorder-pages",
    name: "重排页面",
    desc: "按给定顺序重排页面",
    category: "organize",
    icon: IconArrowsSort,
    maxFiles: 1,
    accepts: ".pdf,application/pdf",
    available: false,
  },
  {
    id: "convert",
    name: "格式转换",
    desc: "PDF ↔ 图片 / Word / 文本，Office → PDF",
    category: "convert",
    icon: IconTransform,
    maxFiles: 1,
    available: false,
  },
  {
    id: "compress",
    name: "压缩 PDF",
    desc: "三档压缩，清理冗余对象",
    category: "extra",
    icon: IconFileZip,
    maxFiles: 1,
    accepts: ".pdf,application/pdf",
    available: false,
  },
  {
    id: "watermark",
    name: "添加水印",
    desc: "文字水印，可调透明度/角度/字号",
    category: "extra",
    icon: IconSticker,
    maxFiles: 1,
    accepts: ".pdf,application/pdf",
    available: false,
  },
  {
    id: "page-numbers",
    name: "添加页码",
    desc: "在页面指定位置添加页码",
    category: "extra",
    icon: IconHash,
    maxFiles: 1,
    accepts: ".pdf,application/pdf",
    available: false,
  },
  {
    id: "info",
    name: "文档信息",
    desc: "查看页数、尺寸、元数据、字体等",
    category: "extra",
    icon: IconInfoCircle,
    maxFiles: 1,
    accepts: ".pdf,application/pdf",
    available: false,
  },
  {
    id: "editor",
    name: "可视化编辑器",
    desc: "直接修改 PDF 文本内容，保存 / 另存为",
    category: "edit",
    icon: IconPencil,
    maxFiles: 1,
    accepts: ".pdf,application/pdf",
    available: false,
  },
  {
    id: "image-to-pdf",
    name: "图片转 PDF",
    desc: "多张图片合并为一个 PDF",
    category: "convert",
    icon: IconPhoto,
    maxFiles: -1,
    accepts: "image/*",
    available: false,
  },
  {
    id: "pdf-to-image",
    name: "PDF 转图片",
    desc: "渲染为 PNG / JPEG，可选 DPI",
    category: "convert",
    icon: IconFileTypePdf,
    maxFiles: 1,
    accepts: ".pdf,application/pdf",
    available: false,
  },
];

export function getTool(id: string | undefined): ToolDef | undefined {
  return TOOLS.find((t) => t.id === id);
}
