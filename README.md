# PDF Editor v0.2

本机运行的单用户 PDF 工具。文档在本机处理；后端与 Vite 默认只监听 `127.0.0.1`。服务没有登录鉴权；请勿绑定到局域网或公网地址。项目按本机处理设计，但尚未完成全量运行时网络外呼审计。

v0.2 已完成审查缺陷 F01–F13 的修复和本机回归。**正式产品/发布验收仍待决策**：LibreOffice readiness 冷启动误判、部分异常提示准确性和强制终止后的外部临时目录清理仍有边界未验证；公开分发前还需项目所有者决定 PyMuPDF 许可路径。产品范围不含 OCR、表单、批注或真正的安全 redaction。当前工具注册表有 **11 个入口**；“格式转换”入口内含 5 种转换。

## 功能

| 分类 | 能力 |
|---|---|
| 文件组织 | 合并 PDF、拆分为 ZIP、旋转、删除和重排页面 |
| 格式转换 | PDF→图片/文本/Word、图片→PDF、Office→PDF |
| 其他工具 | PDF 对象优化、文字水印（自适应缩放或明确报错）、页码（含旋转页）、文档信息 |
| 可视化编辑器 | 修改文本行、添加文字/图片、视觉遮挡、旋转/删除/移动页面、查找、高亮、保存/另存为、撤销/重做 |
| 大任务 | 后台任务进度、取消、幂等提交；PDF 渲染按页或批次处理 |
| 浏览器恢复 | 用户逐文档主动开启的 IndexedDB 缓存；默认关闭 |

文本替换会重排文本行，原字体和字距不保证保留。旋转页目前不能在编辑器中改字、加字、加图或遮挡；先用旋转工具归一化页面。

编辑器的白色覆盖是视觉遮挡，不能作为安全脱敏使用，也不保证清除底层图像或其他 PDF 对象。需要不可恢复地删除敏感内容时，请使用经过验证的安全 redaction 工具并检查输出。

## 运行和资源边界

- 单个上传文件上限为 **600 MiB**（`600 × 1024 × 1024` 字节）。`multipart/form-data` 请求另按完整请求体计 **600 MiB**，包括所有文件、字段和边界；超限返回 `413 UPLOAD_TOO_LARGE`。此中间件限制只覆盖 multipart 请求体，不代表其他 Content-Type 的 HTTP 请求体上限。
- 不设固定 PDF 页数上限。能否完成取决于单页尺寸、实际渲染资源、可用磁盘、时间和系统预算。
- 一个重任务运行、最多一个排队；队列满时返回 `429 JOB_QUEUE_FULL`。长任务返回 `202` 和 `jobId`，用 `GET /api/v1/jobs/{jobId}` 查询进度，用 `POST /api/v1/jobs/{jobId}/cancel` 请求取消。长任务使用 `Idempotency-Key` 防止重复提交。
- PyMuPDF 重任务、单页文本提取、锚点预检和 PDF 内容校验在独立子进程执行，共用 worker 内存预算 2 GiB、任务墙钟预算 15 分钟和有界队列。超限会终止 worker 并清理任务产物；原会话文件保持不变。LibreOffice 子目录在强制终止后的所有清理边界尚未完整验证，见“已知限制”。
- **24 MP** 是单页预览预算；超出时降低预览分辨率并提示。**160 MP** 是每个渲染批次的在途像素预算，不是整个导出的累计上限；处理后会释放每批像素缓冲。
- `/api/health` 是不依赖 LibreOffice 的轻量存活检查。`/api/health/ready` 返回缓存的 LibreOffice readiness；冷启动探测默认最多等待 10 秒，若探测较慢可能暂时报 `not_ready`，而后续 Office 转换仍可能成功。探测有缓存刷新间隔，不由 health 请求同步等待。
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
- IndexedDB 默认关闭。开启前须逐文档确认文档不敏感；缓存未加密，记录 7 天后过期，可按文档或全部清除。过期记录在下次列出草稿时删除，不承诺浏览器关闭期间定时物理擦除。浏览器配额不足时会显示缓存未保存，不影响服务端会话。

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

### 本机 v0.2 整改回归记录（2026-10-21）

- 后端锁定环境：pytest **109 passed，0 skipped**；包含 F10–F13 回归和本机 LibreOffice Office→PDF 成功路径。收到一条既有 Starlette/httpx 弃用提示。
- 前端：TypeScript 检查通过，Vitest **40/40**，Vite production build 成功。
- Playwright Chromium 开发模式 **14/14**。Production preview 模式下，结果预览、实际转换类型、旋转页画布、中文 CMap 四项相关 E2E **4/4**；完整 14 项套件 **10 passed、4 failed**，失败项硬编码访问 `127.0.0.1:5173`，而 preview 配置监听 `4173`，因此这些用例没有覆盖到应用页面。
- Vite build 仍有 `EditorPage.tsx` 动态/静态导入提示和大于 500 kB 的 chunk 提示；不影响 build 成功。
- 本次未重跑 `pip-audit`、`npm audit`、CycloneDX 或托管 CI。GitHub Actions 尚未因本地任务不执行 push 而触发；这些结果不能记作本次通过。

## 已知限制

1. PDF→Word 是尽力而为的版式重建，复杂排版和扫描件需校对；本项目没有 OCR。
2. Office→PDF 依赖本机 LibreOffice。readiness 冷启动探测默认 10 秒；本机曾观察到探测先返回 `not_ready`，但实际 Office 转换可在更长时间内成功。转换操作另有 180 秒超时；不要把一次 `not_ready` 等同于 LibreOffice 未安装。
3. 密码保护的 PDF 暂不支持处理；文档信息工具会将加密状态显示为“是 / 否 / 无法确认”。
4. PDF 对象优化不做图像降采样，文件变小的幅度取决于具体文档。
5. 浏览器本地恢复缓存未加密；不要对敏感文件开启。超过 7 天的记录在下次列出草稿时删除，不提供浏览器关闭期间的定时物理擦除保证。
6. LibreOffice 进程树有 Windows Job Object 管理；强制终止发生在外部转换的不同阶段时，所有 profile/临时目录清理路径尚未逐一验证。
7. 某些 Axios 请求错误在界面中可能显示通用 HTTP 错误文本，而不是后端返回的具体业务消息；API 仍返回结构化错误码和消息。
8. 本次未对运行时所有网络外呼做全流量审计，也未运行托管 GitHub Actions；本机检查不能替代这些证据。
9. 安装依赖时需要访问 Python/npm 软件源；安装完成后，PDF 处理不依赖云服务。

## 许可证

仓库根目录 `LICENSE` 保持原样。第三方依赖许可和 PyMuPDF 双许可说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。PyMuPDF 可按 AGPL-3.0 或 Artifex 商业许可使用；项目所有者尚需选择适用路径并记录决定。公开分发前应完成该决定及对应许可检查。
