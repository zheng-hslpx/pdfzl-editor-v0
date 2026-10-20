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

**一键启动（Windows）**：双击 `start-editor.bat`——自动启动后端（8000）与前端（5173）两个服务窗口，就绪后自动打开浏览器。关闭这两个窗口即停止全部服务。脚本会先检查依赖，缺失时给出安装提示。

**手动启动**：

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

打开 **http://localhost:5173**（用 localhost，不要用 127.0.0.1）。右上角徽章显示「后端已连接」即正常。

### 测试

```bash
cd backend
python -m pytest            # 51 用例（pytest）

cd ../frontend
npm test                    # 4 用例（vitest 冒烟）
```

## 使用指南

### 四步工具流

所有工具统一流程：**选择文件 → 设置 → 执行 → 下载/预览**。

1. 在工具页点「添加文件」（合并可多选，其余单选）
2. 合并时用 ↑↓ 调整顺序；其他工具按需填参数
3. 点「开始处理」，等待完成
4. 结果页可「下载文件」；PDF 类结果还可「在编辑器中打开」继续编辑

### 该用什么工具

| 需求 | 工具 | 说明 |
|---|---|---|
| 几个 PDF 合成一个 | 合并 PDF | 顺序可调；保留各文件书签目录 |
| 按页拆分 | 拆分 PDF | 页码范围如 `1,3-5` 或 `all`，每段一个 PDF，打包 ZIP |
| 页面旋转 / 删除 / 重排 | 页面组织分类 | 旋转支持指定页；重排输入新顺序如 `3,1,2` |
| PDF → 图片 | 格式转换 → PDF→图片 | PNG/JPEG，DPI 36-600（72 屏幕、150 默认、300 打印） |
| PDF → 文本 | 格式转换 → PDF→文本 | 提取全文为 TXT |
| PDF → Word | 格式转换 → PDF→Word | 尽力而为的版式重建，转换后需校对 |
| Office → PDF | 格式转换 → Office→PDF | docx/xlsx/pptx/odt 等；首次约 10-30 秒初始化 |
| 图片 → PDF | 格式转换 → 图片→PDF | 多张图片，A4/Letter/原尺寸 |
| 压缩 / 水印 / 页码 | 更多工具 | 三档压缩；水印可调透明度角度字号颜色；页码可选位置与起始值 |
| 查看文档信息 | 文档信息 | 页数、尺寸、版本、字体、元数据 |

### 可视化编辑器

侧栏「可视化编辑器」进入，打开 PDF 后：

- **改文字**：默认「选择/改字」模式，点击任意文本行直接输入，`Ctrl+Z` 可撤销，完成后点「保存」（有未保存标记与关闭提醒）
- **加文字/图片**：切换到对应模式，在页面上点击目标位置，图片默认 120×80pt
- **涂抹**：在页面上拖拽划选区域，白色覆盖（同时清除底层文本，不影响图片/线条）
- **旋转/删页/移动页**：切换到对应模式后点击页面（删页有确认；移动页先点源页再点目标页）
- **查找**：工具栏输入文字，所有匹配行黄色高亮并显示匹配数
- **缩放**：工具栏百分比两侧按钮，或缩放过小过大时注意性能
- **另存为**：导出为新文件，不覆盖会话

注意：修改文本行会重排该行文字，原字体/字距不保留（自动选 Helvetica/中文字体 + 字号自适应缩小）；已旋转的页面为只读，需先用「旋转页面」工具归一化。

### 页码范围语法

`1`（第 1 页）、`3-5`（第 3 到 5 页）、`1,3,5-8`（混合，逗号分隔）、`all`（全部）。页码从 1 开始，越界会报错提示。

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
