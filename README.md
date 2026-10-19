# PDF Editor v0

轻量级自托管 PDF 编辑器，架构模式参考 [Stirling-PDF](https://github.com/Stirling-Tools/Stirling-PDF)（MIT）。
数据全程本地处理，不上传外部服务。

## 功能（v0）

| 分类 | 工具 |
|---|---|
| 常用 | 合并 PDF（顺序可调 + 书签合并）、拆分 PDF（页码范围 → ZIP） |
| 页面组织 | 旋转、删除页面、重排页面 |
| 格式转换 | PDF → 图片（PNG/JPEG，36-600 DPI）、PDF → 文本、PDF → Word（best-effort）、图片 → PDF、Office → PDF（LibreOffice） |
| 更多 | 压缩（三档）、文字水印（任意角度/透明度）、添加页码、文档信息 |
| 编辑 | **可视化编辑器**：点击文本行直接修改、添加文字/图片、涂抹、旋转/删除/移动页面、查找高亮、保存 / 另存为 |

规划中（v0.1+）：OCR、PDF/A、电子书转换、表单填写、i18n、IndexedDB 文件持久化。

## 技术栈

- **后端**：Python 3.13 + FastAPI + PyMuPDF（fitz，PDFium 血统）+ pdf2docx
- **前端**：React 19 + Vite 7 + TypeScript + Mantine 8 + PDF.js（pdfjs-dist 5）
- **Office 转换**：LibreOffice headless（隔离 profile + 启动预热 + 串行锁）
- **测试**：pytest（50 用例）+ Playwright（Chromium e2e）

## 从 Stirling-PDF 复用的架构模式

- REST 端点分组 `/api/v1/{general,convert,misc,edit}`（对应原作 `@GeneralApi` 等注解分组）
- multipart 上传 → 临时文件 → 处理 → 流式响应（`Content-Disposition` + RFC 5987 中文文件名）→ 响应发完即删
- 临时文件生命周期管理（TempFileManager / TempFile / 周期清理，对应原作同名组件）
- 前端工具注册表 + 四步工具流（Files → Settings → Execute → Result，对应原作 `createToolFlow`）
- 页码范围语法 `"1,3,5-8,all"`；输出命名 `<原名>_merged.pdf` / `<原名>_split.zip`

简化点：无异步任务队列（同步流式响应）、编辑为服务端会话式（非前端 WASM 重编辑）、行级文本替换为视觉级（不保留原字体字形）。

## 快速开始

```bash
# 后端（端口 8000）
cd backend
pip install -r requirements.txt
uvicorn app.main:app --port 8000

# 前端（端口 5173，/api 代理到 8000）
cd frontend
npm install
npm run dev
```

打开 **http://localhost:5173**（用 localhost，不要用 127.0.0.1）。

### 测试

```bash
cd backend
python -m pytest            # 50 用例
```

## 项目结构

```
pdf-editor/
├── backend/
│   ├── app/
│   │   ├── main.py               # FastAPI 入口、CORS、lifespan（清理 + LO 预热）
│   │   ├── config.py             # 环境变量配置
│   │   ├── core/                 # tempfile / responses / errors / pdf_engine
│   │   ├── routers/              # general / convert / misc / edit
│   │   └── services/             # libreoffice / pdf2docx / sessions
│   └── tests/                    # pytest（fixtures 生成样例 PDF/docx/png）
└── frontend/
    └── src/
        ├── api/                  # client + editor API
        ├── components/           # ToolFlow / PdfViewer
        ├── contexts/             # FileContext
        ├── lib/                  # pdfjs loader / pending-file bridge
        ├── pages/                # Home / ToolPage / EditorPage
        └── tools/                # registry + 12 个工具组件
```

## 已知限制

1. **PDF → Word（pdf2docx）为尽力而为的版式重建**：复杂排版、扫描件、艺术字会丢失格式，转换后需校对。
2. **Office → PDF 依赖本机 LibreOffice**：首次转换需初始化其配置（约 10-30 秒）。若 LibreOffice 损坏，`/api/health` 的 `libreoffice` 字段为 false，相关测试自动跳过。
3. **可视化编辑器为行级视觉替换**：修改文本行时原字体/字距不保留（自动选择 Helvetica/中文内置字体 + 字号自适应缩小）；扫描件无文本层，只能添加文字/涂抹。
4. **旋转页（rotation ≠ 0）暂不支持编辑**：请先用「旋转页面」工具归一化。
5. **文件列表存于内存**：刷新页面需重新选择文件（IndexedDB 持久化列 v0.1）。
6. **PyMuPDF 为 AGPL-3.0**：本项目仅供学习研究使用，商用需评估 Artifex 商业许可。

## 环境说明（Windows 开发机）

- Python 依赖装在 managed venv（`~/.workbuddy/binaries/python/envs/default`），前端 `node_modules` 为项目本地安装。
- LibreOffice 位于 `C:\Program Files\LibreOffice`，服务使用 `-env:UserInstallation` 隔离 profile，不与桌面版冲突。

## 许可

本项目代码：MIT。依赖许可见各自项目（PyMuPDF AGPL-3.0 需注意）。
