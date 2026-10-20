# PDF Editor v0.1

本机运行的单用户 PDF 工具。文档处理在本机完成，应用不会把文件上传到云服务。后端与 Vite 默认只监听 `127.0.0.1`，服务没有登录鉴权；请勿绑定到局域网或公网地址。

v0.1 完成阶段 0、阶段 1 及对应 P0/P1/P2 工程整改。产品范围不含 OCR、表单、批注或真正的安全 redaction。当前工具注册表有 **11 个入口**；“格式转换”入口内含 5 种转换。

## 功能

| 分类 | 能力 |
|---|---|
| 文件组织 | 合并 PDF、拆分为 ZIP、旋转、删除和重排页面 |
| 格式转换 | PDF→图片/文本/Word、图片→PDF、Office→PDF |
| 其他工具 | PDF 对象优化、水印、页码、文档信息 |
| 可视化编辑器 | 修改文本行、添加文字/图片、视觉遮挡、旋转/删除/移动页面、查找、高亮、保存/另存为、撤销/重做 |
| 大任务 | 后台任务进度、取消、幂等提交；PDF 渲染按页或批次处理 |
| 浏览器恢复 | 用户逐文档主动开启的 IndexedDB 缓存；默认关闭 |

文本替换会重排文本行，原字体和字距不保证保留。旋转页目前不能在编辑器中改字、加字、加图或遮挡；先用旋转工具归一化页面。

编辑器的白色覆盖是视觉遮挡，不能作为安全脱敏使用，也不保证清除底层图像或其他 PDF 对象。需要不可恢复地删除敏感内容时，请使用经过验证的安全 redaction 工具并检查输出。

## 运行和资源边界

- 每次 HTTP 请求体上限为 **600 MiB**（`600 × 1024 × 1024` 字节），统计完整 multipart 请求，包括多个文件、字段和边界。超限返回 `413 UPLOAD_TOO_LARGE`。该限制按请求计算，不是单文件限制。
- 不设固定 PDF 页数上限。能否完成取决于单页尺寸、实际渲染资源、可用磁盘、时间和系统预算。
- 一个重任务运行、最多一个排队；队列满时返回 `429 JOB_QUEUE_FULL`。长任务返回 `202` 和 `jobId`，用 `GET /api/v1/jobs/{jobId}` 查询进度，用 `POST /api/v1/jobs/{jobId}/cancel` 请求取消。长任务使用 `Idempotency-Key` 防止重复提交。
- PyMuPDF 重任务在独立子进程执行。worker 内存预算为 2 GiB，任务墙钟预算为 15 分钟；超限会终止 worker、清理临时产物并保留原会话文件。
- **24 MP** 是单页预览预算；超出时降低预览分辨率并提示。**160 MP** 是每个渲染批次的在途像素预算，不是整个导出的累计上限；处理后会释放每批像素缓冲。
- `/api/health` 是不依赖 LibreOffice 的轻量存活检查。`/api/health/ready` 返回缓存的 LibreOffice readiness；转换探测有超时和刷新间隔，不由 health 请求同步等待。
- 编辑会话在内存中，服务重启后不会恢复；活动编辑器定期 heartbeat，2 小时无活动后过期。关闭文件会立即清理会话。

## 快速开始

### Windows 一键启动

双击项目根目录的 `start-editor.bat`。服务仅绑定 `127.0.0.1`；打开 **http://127.0.0.1:5173**。Office→PDF 需要安装本机 LibreOffice。若自动检测不到，可设置 `PDF_EDITOR_SOFFICE` 指向 `soffice.exe`。

### 手动启动

```powershell
# 后端
cd backend
python -m pip install -r requirements.lock
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 前端（另开一个终端）
cd frontend
npm ci --legacy-peer-deps
npm run dev -- --host 127.0.0.1
```

浏览器打开 **http://127.0.0.1:5173**。后端只允许本机 Host，并只接受该前端 Origin；应用没有鉴权，不支持局域网或公网部署。

## 编辑器保存、撤销和恢复

- 打开文件后，前端按视口懒加载页面与文本；保存请求携带当前 `revision`。版本过期或文本锚点不匹配时返回 `409 REVISION_CONFLICT`，未确认草稿会保留。
- 删除、移动页面、插入文字/图片等会影响页面或文本模型的操作，会先要求保存、放弃或取消。另存为会先提交文字草稿。关闭、换文件和重载不会静默丢弃草稿。
- 可逆操作使用轻量逆操作记录；需要时使用服务端快照。历史最多 10 步、快照总量最多 1 GiB；大文件可能无法保留 10 步。容量不足时会先询问如何继续。页面刷新或从浏览器缓存恢复后，撤销/重做历史从空状态开始。
- IndexedDB 默认关闭。开启前须逐文档确认文档不敏感；缓存未加密，7 天过期，并可按文档或全部清除。浏览器配额不足时会显示缓存未保存，不影响服务端会话。

## 测试和构建

```powershell
# Python 后端测试及审计/SBOM 工具
cd backend
python -m pip install -r requirements-dev.lock
python -m pytest -ra

# 前端检查
cd ../frontend
npm ci --legacy-peer-deps
npx tsc --noEmit
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

Playwright 配置会启动本地后端和 Vite。开发机默认使用已安装的 Chrome；CI 使用 Playwright Chromium。GitHub Actions 分别定义 Linux 后端、Windows + LibreOffice 后端、TypeScript/Vitest/Vite build、Chromium E2E、SBOM 与依赖审计任务，并归档测试结果和审计报告。每次 pytest 通过数、跳过数及跳过原因以 CI 报告为准，不把跳过记作通过。

前端要求 Node.js 22.22.2 或更新的兼容版本。安装命令与 CI 一致使用 `--legacy-peer-deps`，以避开 npm 10 对 Vitest 4 可选 peer 依赖树的解析崩溃；测试所需的 `@testing-library/dom` 已列为直接开发依赖。

### 本机验收记录（2026-10-21）

- Python 3.13.12 锁定环境：pytest **90 passed，0 skipped**；包含本机 LibreOffice Office→PDF 成功路径。收到一条 Starlette/httpx 弃用提示。
- 本机 Node.js 22.19.0 / npm 10.9.3：TypeScript 通过，Vitest **22/22**，Playwright Chromium E2E **3/3**，Vite production build 通过。CI 固定使用 Node.js 22.22.2，以符合 jsdom 的 Node 引擎要求。
- 依赖审计：`pip-audit` 与 `npm audit --audit-level=moderate` 均为 **0 项已知漏洞**。本地 CycloneDX 结果含 Python 88 个组件、前端 265 个组件；CI 每次运行重新生成 SBOM。
- Vite build 仍提示 `EditorPage.tsx` 同时被动态和静态导入，以及 bundle 存在大于 500 kB 的 chunk；不影响 build 成功。
- 本机验收已完成；托管 GitHub Actions 尚未因未推送而触发。

## 已知限制

1. PDF→Word 是尽力而为的版式重建，复杂排版和扫描件需校对；本项目没有 OCR。
2. Office→PDF 依赖本机 LibreOffice；未安装或 readiness 失败时该转换不可用。启动检查 `/api/health/ready`。
3. 密码保护的 PDF 暂不支持处理；文档信息工具会将加密状态显示为“是 / 否 / 无法确认”。
4. PDF 对象优化不做图像降采样，文件变小的幅度取决于具体文档。
5. 浏览器本地恢复缓存未加密；不要对敏感文件开启。
6. 安装依赖时需要访问 Python/npm 软件源；安装完成后，PDF 处理不依赖云服务。

## 许可证

仓库根目录 `LICENSE` 保持原样。第三方依赖许可和 PyMuPDF 双许可说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。PyMuPDF 可按 AGPL-3.0 或 Artifex 商业许可使用；项目所有者尚需选择适用路径并记录决定。公开分发前应完成该决定及对应许可检查。
