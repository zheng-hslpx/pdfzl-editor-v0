# PDF Editor v0.2.0

**简体中文** | [English](README.md)

本机运行的单用户 PDF 工具。文档在本机处理，后端和 Vite 默认只监听 `127.0.0.1`。服务没有登录鉴权，不要绑到局域网或公网地址。项目按本机处理来设计，但运行时到底往外发了哪些请求，还没做过全量审计。

截至 2026-10-27，F01-F13 的缺陷整改、四项最终验收补充整改和两项显示修复都已完成，本机要求的 pytest、Vitest、TypeScript、production build 以及开发和生产环境的 E2E 全部通过。**建议 PDF Editor v0.2.0 通过个人离线自用的正式验收**。源代码已公开在 GitHub，但还没有发布安装包或 Release。还有两处没有验证到底：一是 LibreOffice 被强制终止后外部临时目录的清理，二是极端 PDF 和资源压力下的表现。PyMuPDF 走 AGPL 还是 Artifex 商业许可，是将来公开分发前由项目所有者决定的事，不构成个人离线自用验收的阻断项。产品范围不含 OCR、表单、批注，也没有真正的安全 redaction。工具注册表现在有 **11 个入口**，其中“格式转换”一个入口里包含 5 种转换。

## 版本更新

当前版本 0.2.0。后端 `FastAPI(version="0.2.0")` 和前端 `package.json` 里的版本号是一致的。标签 `v0.2.0` 打在 `40a9736` 上，之后又追加了两项显示修复。仓库没有 CHANGELOG，版本沿革就看下面这张表和 `tasks/` 下的任务清单。

| 版本 | 时间 | 主题 | 状态 |
|---|---|---|---|
| v0 | 2026-10-19 | 初版：后端 16 个端点，前端 12 个工具加一个可视化编辑器 | 已被 v0.1 取代 |
| v0.1 | 2026-10-20 至 10-21 | 按审查意见做的 A-D 四批工程化整改 | 已被 v0.2.0 取代 |
| v0.2.0 | 2026-10-21 | F01-F13 缺陷整改、四项补充整改、本机离线自用验收 | 已打标签 `v0.2.0` |
| v0.2.0（当前） | 2026-10-27 | 两项显示修复：扫描件文字蒙版、公文页文字偏移 | 当前主线 |

### v0.2.0 缺陷整改（2026-10-21）

**编辑与草稿**

- F01（P0）保存过程中继续输入，草稿会跑到别的行上。修法是把草稿锚点绑到页面身份、文档版本、原内容及其哈希、几何信息上；保存之后，只有已提交文本、原位置和当前 revision 三者能唯一对上，才重新定位。对不上的草稿留在单独提示区，不允许再次写入，文本提取模型也不再覆盖已有的语义锚点。
- F02（P1）同一页改多行再批量保存，会跟自己触发锚点冲突。worker 现在在不变的文档快照上按页缓存文本模型，先把越界和重叠校验做完，再按原始坐标批量加遮挡、逐页应用，最后才写入替换文本。重叠区域返回 `OVERLAPPING_EDITS`，整个任务不提交。
- F03（P1）浏览器后退能绕过未保存草稿的保护。改用 React Router data router 的 `useBlocker` 之后，POP、前进、应用内链接和程序导航都在拦截范围内。取消会重置阻断并保留草稿和会话；选保存则先提交、另存 PDF 再放行，保存还没结束时，保存和放弃按钮都禁用。

**渲染与预览**

- F06（P2）开发模式下 PDF 结果预览打不开，原因是缓冲区已经转移。`PdfViewer` 复用共享的 PDF.js loader，结果直接用 Blob URL 加载，等 `PDFDocumentLoadingTask.destroy()` 之后再释放；组件清理时会取消任务，稳定的回调也不会重复触发加载。
- F08（P2）旋转过的页面还在显示没跟着转的文字覆盖层。`EditorCanvas` 只在 `rotation=0` 时渲染覆盖层；90°、180°、270° 页面继续显示按 PDF.js viewport 旋转的画布，同时提示覆盖层已隐藏、坐标编辑不可用。
- F09（P2）没配本地 CMap，中文在画布上漏字。共享 loader 给 URL、ArrayBuffer、Uint8Array 三种输入都配了同源的 `cMapUrl`、`cMapPacked` 和 `standardFontDataUrl`；仓库带 169 个 CMap 和 16 个 standard-font 资源，与锁定的 `pdfjs-dist@6.4.299` 对齐，许可证文件一并保留。

**转换与输出**

- F07（P2）转换结果的类型判断错了，DOCX 和多页图片会进错预览器。现在按输出 Blob 的实际 MIME 和文件扩展名判断，两者冲突就归为未知，不按转换方向去猜。只有 PDF 显示 PDF.js 预览和编辑入口；DOCX、ZIP 这类不支持预览的结果显示文件名、格式、MIME 和下载入口。
- F12（P2）给 90°、270° 页面加页码，方向是反的。锚点仍然从显示坐标反变换回页面坐标，绘制基线的旋转改成跟着 `page.rotation` 走。0°、90°、180°、270° 四个方向在用户看来都是从左到右，页面旋转元数据没有动。
- F13（P2）水印太长被截到页面外，接口还报成功。现在按文字实测宽度、字号、换行后的高度和旋转角算出外接框，在可见页面内自适应缩放，最小不低于 8 pt（用户自己设的字号本来就低于 8 pt 的，按用户的来）。实在放不进可读字号的，返回 `WATERMARK_OVERFLOW`，不产出假成功文件。

**服务与启动**

- F04（P1）一键启动器显示的地址和后端 Origin 白名单对不上。现在以 `UI_URL=http://127.0.0.1:5173` 作为唯一页面地址，readiness 探测也从它派生；`curl.exe --fail` 把 4xx、5xx 都算未就绪，后端 Origin allowlist 仍然保持最小。
- F05（P2）幂等写请求在查重之前先做了 revision 和 busy 校验，成功过的请求重试反而返回 409。`JobManager` 现在先解析幂等记录、再走运行请求预检：同一个请求返回已有的任务，同一个键配不同请求体或 `History-Policy` 返回 `IDEMPOTENCY_KEY_REUSED`。新请求的 revision、busy、锚点和 session 锁校验照旧。
- F10（P2）单页文本读取和锚点预检跑在 API 事件循环里，会阻塞。文本提取、上传校验和写入预检都改到 spawn worker 执行，共用单活动 worker 队列、2 GiB 内存预算、15 分钟超时和进程取消。worker 槽位满了，文本读取最多等 10 秒，上传校验最多等 5 秒。
- F11（P2）内容校验放过了伪装成 PDF 的图片。上传解析后用 PyMuPDF 的 `document.is_pdf` 判断，PDF 引擎的公共入口 `open_pdf` 再拒一次，错误统一成不含本地路径的 `INVALID_PDF`。

### 四项补充整改（2026-10-21）

- 长文字替换被静默裁切。textbox 缩到最小 6 pt 还放不下，就返回 `TEXT_OVERFLOW`，不再拿无约束基线硬画。worker 失败不产生会话变更，前端的草稿还在。
- 草稿恢复的 E2E 时好时坏。根因是恢复提示的断言排在长文本等待之后，那时提示早就自动关了。现在提示和完整文本两个断言都保留，只把提示断言挪到点击“恢复”之后，没有延长提示时间，没有删断言，也没有加固定 sleep。
- 预览失败没有恢复路径。打开 PDF 现在有可取消的 60 秒截止；打开失败或超时显示重试，页面 render 被拒显示页级错误和重试。每个预览 effect 自己持有 loading task 和取消信号，失败、重试、卸载时销毁任务并回收 Blob URL。
- PDF.js 静态资源加了版本校验。`npm run check:pdfjs-resources` 先核对锁定版本和安装版本，再逐文件比对 CMap、standard-font 资源和 SHA-256，许可证文件必须齐全，改过的资源会被拒。

### 两项显示修复（2026-10-27）

- 扫描件的文字蒙版丢了（`b416b3d`）。这类扫描件是淡色整页底图加一层 CCITTFaxDecode ImageMask 文字合成出来的，PDF.js 拿不到 `wasmUrl`，本地也没捆绑解码器，蒙版就画不出来。修法是给共享参数补上同源 `wasmUrl`，把锁定版本的 13 项 WASM、JavaScript fallback 和许可证资源一起打进仓库，wasm 集合也纳入逐文件 SHA-256 校验。
- 公文页上没编辑过的文字位置偏了（`33f297d`）。原文一个字没动，却被白底 textarea 和替代字体重新排了一遍。现在闲置行的文字和背景都是透明的，原 PDF canvas 的字形、基线、宽度照旧显示，只有聚焦或者确实改过才出现编辑框。失焦后草稿仍然可见；撤销回原文或保存刷新，canvas 外观就回来了；查找改成半透明高亮。

### 各版本测试数量

| 阶段 | pytest | Vitest | E2E（开发 / production preview） |
|---|---|---|---|
| v0 | 51 | 4 | Playwright 冒烟场景与截图核对 |
| v0.1 | 90（0 skipped） | 22/22 | 3/3 |
| v0.2.0 | 115（0 skipped） | 54/54 | 16/16 / 16/16 |
| v0.2.0（当前） | 115 | 56/56 | 18/18 / 18/18 |

表里的数字是各版本验收当时的记录，不沿用到后续版本。某一次跑下来多少通过、多少跳过、为什么跳过，以当次的 CI 或本机报告为准，跳过不算通过。

## 功能

| 分类 | 能力 |
|---|---|
| 文件组织 | 合并 PDF、拆分为 ZIP、旋转、删除和重排页面 |
| 格式转换 | PDF→图片 / 文本 / Word、图片→PDF、Office→PDF |
| 其他工具 | PDF 对象优化、文字水印（自适应缩放或明确报错）、页码（含旋转页）、文档信息 |
| 可视化编辑器 | 修改文本行、添加文字 / 图片、视觉遮挡、旋转 / 删除 / 移动页面、查找、高亮、保存 / 另存为、撤销 / 重做 |
| 大任务 | 后台任务进度、取消、幂等提交；PDF 渲染按页或批次处理 |
| 浏览器恢复 | 用户逐个文档主动开启的 IndexedDB 缓存，默认关闭 |

文本替换会重排文本行，原字体和字距不保证保留。旋转页目前不能在编辑器里改字、加字、加图或遮挡，得先用旋转工具把页面归一化。

编辑器的白色覆盖只是视觉遮挡，不能当安全脱敏用，也不保证清掉底层图像或其他 PDF 对象。真要不可恢复地删掉敏感内容，请换用经过验证的安全 redaction 工具，并检查输出。

## 运行和资源边界

- 单个上传文件上限 **600 MiB**（`600 × 1024 × 1024` 字节）。`multipart/form-data` 请求另按完整请求体计 **600 MiB**，包括所有文件、字段和边界，超了返回 `413 UPLOAD_TOO_LARGE`。这个中间件限制只覆盖 multipart 请求体，不代表其他 Content-Type 的 HTTP 请求体上限。
- 不设固定 PDF 页数上限。能不能做完，取决于单页尺寸、实际渲染资源、可用磁盘、时间和系统预算。
- 一个重任务运行，最多一个排队，队列满了返回 `429 JOB_QUEUE_FULL`。长任务返回 `202` 和 `jobId`，用 `GET /api/v1/jobs/{jobId}` 查进度，用 `POST /api/v1/jobs/{jobId}/cancel` 请求取消。长任务用 `Idempotency-Key` 防止重复提交。
- PyMuPDF 重任务、单页文本提取、锚点预检和 PDF 内容校验都在独立子进程里跑，共用 worker 内存预算 2 GiB、任务墙钟预算 15 分钟和有界队列。超限会终止 worker 并清理任务产物，原会话文件保持不变。LibreOffice 子目录在强制终止后的清理边界还没完整验证，见“已知限制”。
- **24 MP** 是单页预览预算，超了就降预览分辨率并提示。**160 MP** 是每个渲染批次的在途像素预算，不是整个导出的累计上限，每批处理完就释放像素缓冲。
- PDF.js 预览打开有可取消的 **60 秒**总截止；模块或文档加载失败会给出重试入口，页面渲染异常显示页级错误和重试。预览组件持有 loading task，清理时先 `destroy()`，再释放它的 Blob URL。
- `/api/health` 是不依赖 LibreOffice 的轻量存活检查。`/api/health/ready` 返回缓存的 LibreOffice readiness；冷启动探测默认最多等 20 秒，health 请求不会同步等待。探测超时时状态留在 `unknown`，5 秒后可以再试，只有探测明确失败才报 `not_ready`。本机真实冷启动探测耗时约 13 秒。
- 编辑会话存在内存里，服务重启不会恢复。活动编辑器定期 heartbeat，2 小时没动静就过期，关闭文件则立即清理会话。

## 快速开始

### Windows 一键启动

双击项目根目录的 `start-editor.bat`。服务只绑 `127.0.0.1`，打开 **http://127.0.0.1:5173** 就能用。Office→PDF 需要本机装 LibreOffice；自动检测不到的话，可以把 `PDF_EDITOR_SOFFICE` 指向 `soffice.exe`。

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

浏览器打开 **http://127.0.0.1:5173**。后端只接受本机 Host 和这一个前端 Origin；应用没有鉴权，不支持局域网或公网部署。

## 编辑器保存、撤销和恢复

- 打开文件后，前端按视口懒加载页面和文本，保存请求携带当前 `revision`。版本过期或文本锚点对不上时返回 `409 REVISION_CONFLICT`，未确认的草稿会保留。
- 删除、移动页面、插入文字或图片这类会影响页面与文本模型的操作，会先要求保存、放弃或取消。另存为会先提交文字草稿。关闭、换文件和重载都不会静默丢草稿。
- 可逆操作用轻量逆操作记录，需要时用服务端快照。历史最多 10 步、快照总量最多 1 GiB，大文件可能留不满 10 步，容量不够时会先问怎么继续。页面刷新或从浏览器缓存恢复后，撤销和重做从空状态开始。
- IndexedDB 默认关闭。开启前要逐个文档确认不敏感；缓存不加密，记录 7 天后过期，可以按文档或全部清除。过期记录在下次列出草稿时删掉，不承诺浏览器关闭期间定时物理擦除。浏览器配额不足时会提示缓存未保存，不影响服务端会话。

## 测试和构建

```powershell
# Python 后端测试及审计 / SBOM 工具
cd backend
python -m pip install -r requirements-dev.lock
python -m pytest -ra

# 前端检查
cd ../frontend
npm ci --legacy-peer-deps
npx tsc --noEmit
npm run check:pdfjs-resources
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

`npm run check:pdfjs-resources` 先比对已安装的 `pdfjs-dist` 版本和 `package-lock.json` 里锁定的版本，再逐文件核对本地 CMap、standard-font 与 WASM 解码器资源集合及其 SHA-256，包含 JavaScript fallback 和必需的许可证文件。脚本只报告差异，不改资源加载配置，也不复制文件。

Playwright 配置会启动本地后端和 Vite。可以用 `PYTHON` 指定启动后端的解释器，这个解释器要装好后端锁定依赖（含 `uvicorn`）。开发机默认用已安装的 Chrome，CI 用 Playwright Chromium。GitHub Actions 上跑两个任务：Windows + LibreOffice 的后端测试，以及前端检查（TypeScript、Vitest、production build、Chromium E2E、依赖审计与 SBOM），并归档测试结果和审计报告。项目按 Windows 本机使用交付，CI 不覆盖 Linux。每次 pytest 的通过数、跳过数和跳过原因以 CI 报告为准，跳过不记作通过。

前端要求 Node.js 22.22.2 或更新的兼容版本。安装命令和 CI 一致，用 `--legacy-peer-deps` 绕开 npm 10 解析 Vitest 4 可选 peer 依赖树时的崩溃；测试需要的 `@testing-library/dom` 已经列为直接开发依赖。

### 回归记录：两项显示修复（2026-10-27）

- PDF.js 的 URL 加载和字节预览都配了本地 `wasmUrl`，新增与锁定版本一致的 13 项解码器、JavaScript fallback 和许可证资源，扫描 PDF 里 CCITT、JBIG2 这类图像蒙版恢复了显示。合成 CCITT ImageMask 的浏览器回归验证了解码器请求和实际画布像素，资源被篡改的夹具会被 SHA-256 校验拒掉。
- 未编辑文字的 textarea 文字和背景保持透明，露出原 PDF canvas 的字体和位置；点击才进入编辑，草稿在失焦和切换工具后仍然可见。未改动行失焦、草稿撤销回原文或保存刷新后恢复 canvas 外观，查找用半透明高亮。懒加载淘汰行模型时清掉焦点状态，重载后不会留下旧编辑框。
- 本机验证：pytest **115 passed**，Vitest **56/56**，TypeScript 和 production build 通过；Chrome 开发模式全量 **18/18**，production preview 全量 **18/18**。搜索请求的等待条件最后调过一次，调完之后开发模式的同一显示回归另行通过 **1/1**。既有的 Starlette / httpx 弃用提示、动态 / 静态导入和大 chunk 提示还在。
- 两份用户原件用来做真实浏览器回归：扫描件第 1、2 页看解码器阻断和恢复的对照，公文 3 页的闲置完整 DOM 与隐藏文字覆盖层的画布截图逐像素一致。原件 SHA-256 前后一致，原件和截图都没提交 Git，本地证据放在仓库外的 `E:\worksapce\PDF demo1\render-evidence-20261027`。
- 这一轮只修预览显示。正在编辑的文字还是用现有的浏览器编辑控件，保存还是走后端原有的文字替换机制。搜索提取没结束时可能碰上新任务队列繁忙的边界，回归里明确等到提取完成才验证保存，队列限制没有改。

### 回归记录：v0.2 整改（2026-10-21）

- 后端锁定环境：pytest **115 passed，0 skipped**，包含 F01、F02 相关的服务端回归，长文字溢出的 API 和真实 PDF 输出，F10-F13 回归以及 Office→PDF 成功路径，另有一条既有的 Starlette / httpx 弃用提示。
- 前端：TypeScript 检查通过，Vitest **54/54**，Vite production build 成功。
- Playwright Chromium：开发模式 **16/16**，production preview 模式 **16/16**，覆盖 F01 双 save-race、草稿恢复、新增长文字溢出的真实浏览器流程、transient 503 后预览重试，以及 StrictMode 下的文件切换和卸载。preview 全套访问的是 `4173` 上的 production preview。
- PDF.js 静态资源检查通过：锁定版本和安装版本都是 `6.4.299`，169 个 CMap 目录文件和 16 个 standard-font 目录文件（含各自许可文件）逐文件 SHA-256 一致，篡改资源的临时故障夹具被脚本拒绝。
- 预览打开有可取消的 60 秒截止。Vitest 故障注入验证了打开超时、render rejection、重试、卸载，以及 `loadingTask.destroy()` 之后释放 Blob URL；真实浏览器在 transient 503 后重试，读取并渲染真实 PDF 成功。
- Windows 一键启动器修好 CRLF 行尾后实跑通过，显示 `Ready!`；后端和 Vite 代理的 health 都返回 `200`。`.gitattributes` 把批处理文件固定在 CRLF。
- Vite build 还有 `EditorPage.tsx` 的动态 / 静态导入提示和超过 500 kB 的 chunk 提示，不影响 build 成功。
- 这一轮没跑 `pip-audit`、`npm audit`、CycloneDX 和托管 CI，也没 push，这些结果不能记作本次通过。

## 已知限制

1. PDF→Word 是尽力而为的版式重建，复杂排版和扫描件需要人工校对；项目不含 OCR。
2. Office→PDF 依赖本机 LibreOffice。readiness 探测有 20 秒预算，超时说明探测没在预算内做完，会返回 `unknown`，冷却几秒后重试；真实冷启动路径已在本机验证过。转换操作另算 180 秒超时。
3. 带密码的 PDF 暂时处理不了；文档信息工具会把加密状态显示成“是 / 否 / 无法确认”。
4. PDF 对象优化不做图像降采样，文件能小多少取决于具体文档。
5. 浏览器本地恢复缓存不加密，敏感文件不要开。超过 7 天的记录在下次列出草稿时删除，不保证浏览器关闭期间定时物理擦除。
6. LibreOffice 进程树由 Windows Job Object 管理。强制终止发生在外部转换的不同阶段时，profile 和临时目录的清理路径还没逐条验证。
7. 极端字体、非标准页面 box / UserUnit、签名和表单保真、600 MiB 文件、15 分钟任务、长期磁盘压力都没做穷尽验证，不声称任意 PDF 都能保真。
8. PDF 打开过程有 60 秒截止并支持重试，每页 render 抛错可以单页重试。单页 render promise 永久 pending 的情况没有独立的页级截止时间。
9. 运行时所有网络外呼没做过全流量审计，托管 GitHub Actions 也没跑过，本机检查替代不了这两项证据。
10. 装依赖要访问 Python / npm 软件源；装完之后，PDF 处理不依赖任何云服务。

## 项目定位

本项目是作者自用的本机离线 PDF 工具，代码公开供学习参考。作者不提供商业支持，也不承诺在生产环境可用。这段话说明的是项目定位与支持范围，不构成对许可授予权利的额外限制，具体授权以 [LICENSE](LICENSE) 为准。

## 许可证

Copyright © 2026 Hongsen Zheng。

本仓库按目录分别授权：

- 根目录 `LICENSE` 是 **AGPL-3.0**，默认覆盖整个仓库，`backend/` 也在其中。后端通过 PyMuPDF 处理 PDF，而 PyMuPDF 采用 AGPL-3.0 或 Artifex 商业双许可，所以后端与整个作品按 AGPL-3.0 发布，本项目未购买 Artifex 商业许可。
- `frontend/` 是独立程序，通过 HTTP API 调用后端，不链接 PyMuPDF，单独采用 **MIT**，见 [frontend/LICENSE](frontend/LICENSE)。

这个划分自 2026-10-27 起生效；此前提交按 MIT 发布，已经发出的 MIT 授权继续有效。第三方依赖、捆绑资源与 PyMuPDF 的许可说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
