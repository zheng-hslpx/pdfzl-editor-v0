# PDF Editor v0.2.0

**English** | [简体中文](README_CN.md)

A single-user PDF toolkit that runs on your own machine. Documents stay local, and the backend and the Vite dev server bind to `127.0.0.1` by default. There is no authentication, so do not expose the service to a LAN or the internet. The design assumes local use, but nobody has audited every request the app makes at runtime.

As of 2026-10-27, the F01-F13 defects, the four follow-up acceptance items, and the two display fixes are all done. The pytest, Vitest, TypeScript, production build, and E2E runs this machine requires all pass, in development and against the production preview. **PDF Editor v0.2.0 is recommended to pass formal acceptance for personal offline use.** The source is public on GitHub, but there is no installer or Release yet. Two areas are still unverified: cleanup of external temp directories after LibreOffice is force-killed, and behavior under extreme PDFs or resource pressure. Whether PyMuPDF ships under AGPL or an Artifex commercial license is an owner decision for any future public distribution; it does not block personal offline use. The product does not do OCR, forms, annotations, or real secure redaction. The tool registry has **11 entries**, and the "Format conversion" entry covers 5 conversions.

## Version history

The current version is 0.2.0. The backend (`FastAPI(version="0.2.0")`) and the frontend `package.json` agree on that number. The tag `v0.2.0` points at `40a9736`, and two display fixes landed afterwards. There is no CHANGELOG, so the table below and the task lists under `tasks/` are the record.

| Version | Date | Theme | Status |
|---|---|---|---|
| v0 | 2026-10-19 | First build: 16 backend endpoints, 12 frontend tools plus a visual editor | Replaced by v0.1 |
| v0.1 | 2026-10-20 to 10-21 | Four engineering batches (A-D) driven by review findings | Replaced by v0.2.0 |
| v0.2.0 | 2026-10-21 | F01-F13 fixes, four follow-up items, local offline acceptance | Tagged `v0.2.0` |
| v0.2.0 (current) | 2026-10-27 | Two display fixes: scanned-page text masks, shifted text on official documents | Current main |

### v0.2.0 defect fixes (2026-10-21)

**Editing and drafts**

- F01 (P0) Typing during a save moved drafts onto other lines. Draft anchors now carry page identity, document version, original text and its hash, and geometry. After a save, a draft is relocated only when the committed text, the original position, and the current revision identify one line unambiguously. Drafts that fail that check stay in a separate notice area and cannot be written again, and text extraction no longer overwrites an existing semantic anchor.
- F02 (P1) Saving several lines on one page in a batch tripped over its own anchors. The worker caches the text model per page against an unchanged snapshot and runs the out-of-bounds and overlap checks first. It then applies covers in original coordinates page by page and writes the replacement text last. Overlapping regions return `OVERLAPPING_EDITS` and the job is not committed.
- F03 (P1) Browser Back got around the unsaved-draft guard. Navigation now goes through React Router's data-router `useBlocker`, which covers POP, forward, in-app links, and programmatic navigation. Cancelling resets the block and keeps the draft and session. Saving commits and exports a copy before leaving, and the save and discard buttons are disabled while a save is running.

**Rendering and preview**

- F06 (P2) PDF result previews failed in development mode because the buffer had already been transferred. `PdfViewer` reuses the shared PDF.js loader and loads results straight from a Blob URL, releasing it after `PDFDocumentLoadingTask.destroy()`. Component cleanup cancels the task, and stable callbacks no longer start a second load.
- F08 (P2) Rotated pages still showed a text overlay that had not been rotated. `EditorCanvas` renders the overlay only at `rotation=0`. Pages at 90°, 180°, and 270° keep the canvas rotated by the PDF.js viewport, and the UI says the overlay is hidden and coordinate editing is off.
- F09 (P2) With no local CMap, Chinese glyphs went missing on the canvas. The shared loader now sets same-origin `cMapUrl`, `cMapPacked`, and `standardFontDataUrl` for URL, ArrayBuffer, and Uint8Array inputs. The repo carries 169 CMap files and 16 standard-font files that match the pinned `pdfjs-dist@6.4.299`, along with their license files.

**Conversion and output**

- F07 (P2) Result types were inferred wrongly, so DOCX and multi-page image results opened the wrong previewer. Detection now uses the output Blob's actual MIME type and file extension; when the two disagree, the result is treated as unknown rather than guessed from the conversion direction. Only PDF gets the PDF.js preview and an edit entry. DOCX, ZIP, and anything else that cannot be previewed shows the filename, format, MIME type, and a download link.
- F12 (P2) Page numbers on 90° and 270° pages came out reversed. Anchors are still transformed from display coordinates back to page coordinates, but the drawing baseline now rotates with `page.rotation`. All four orientations read left to right on screen, and the page rotation metadata is untouched.
- F13 (P2) Overlong watermarks were clipped past the page edge and still reported success. The bounding box now comes from measured text width, font size, wrapped height, and rotation angle, and the text scales down to fit the visible page, never below 8 pt. A user size that is already below 8 pt is kept. When nothing readable fits, the call returns `WATERMARK_OVERFLOW` and writes no file.

**Service and launcher**

- F04 (P1) The one-click launcher advertised one URL while the backend Origin list expected another. `UI_URL=http://127.0.0.1:5173` is now the only page address, and the readiness probe is derived from it. `curl.exe --fail` treats 4xx and 5xx as not ready, and the backend Origin allowlist stays as small as it was.
- F05 (P2) Idempotent writes checked revision and busy state before looking up the idempotency record, so retrying a request that had already succeeded returned 409. `JobManager` resolves the record first: an identical request returns the existing job, while the same key with a different body or `History-Policy` returns `IDEMPOTENCY_KEY_REUSED`. New requests still get the revision, busy, anchor, and session lock checks.
- F10 (P2) Single-page text reads and anchor preflights ran on the API event loop. Text extraction, upload validation, and write preflights now run in spawn workers behind a single-active-worker queue with a 2 GiB memory budget, a 15-minute timeout, and process cancellation. A text read waits up to 10 seconds for a free slot, and upload validation waits up to 5 seconds.
- F11 (P2) Content validation accepted images disguised as PDFs. Parsed uploads are checked with PyMuPDF's `document.is_pdf`, and the engine's public `open_pdf` rejects non-PDF documents a second time. Both paths report `INVALID_PDF` with no local paths in the message.

### Four follow-up items (2026-10-21)

- Long replacement text was silently truncated. When a textbox cannot fit the text even at the 6 pt minimum, the call returns `TEXT_OVERFLOW` instead of drawing on an unconstrained baseline. A worker failure leaves the session untouched, and the frontend keeps the draft.
- The draft-recovery E2E test was flaky. Its assertion for the recovery notice ran after a long text wait, by which point the notice had closed itself. Both assertions stay. The notice assertion now runs right after the "Recover" click, with no change to the notice timing, the assertion count, or the absence of fixed sleeps.
- Preview failures had no recovery path. Opening a PDF now has a cancellable 60-second deadline. Load failures and timeouts show a retry, and a rejected page render shows a page-level error with retry. Each preview effect owns its loading task and cancellation signal, and destroys the task and revokes the Blob URL on failure, retry, or unmount.
- PDF.js resources now have a version check. `npm run check:pdfjs-resources` compares the pinned and installed versions, then verifies the CMap, standard-font, and SHA-256 file sets. License files must be present, and tampered resources are rejected.

### Two display fixes (2026-10-27)

- Text masks went missing in scanned PDFs (`b416b3d`). These scans are built from a pale full-page background plus a CCITTFaxDecode ImageMask text layer. PDF.js was given no `wasmUrl` and the decoders were not bundled, so the mask never drew. The fix adds a same-origin `wasmUrl` to the shared parameters, bundles the 13 WASM, JavaScript fallback, and license files for the pinned version, and brings the wasm set under the per-file SHA-256 check.
- Unedited text on official-document pages shifted (`33f297d`). Text nobody had touched was being redrawn by an opaque textarea in a substitute font. Idle lines now keep transparent text and background, so the original canvas glyphs, baselines, and widths show through, and the editor appears only on focus or when a line has changed. A blurred draft stays visible. Undoing back to the original text, or saving and reloading, brings the canvas look back. Find uses a translucent highlight.

### Test counts by stage

| Stage | pytest | Vitest | E2E (development / production preview) |
|---|---|---|---|
| v0 | 51 | 4 | Playwright smoke scenarios and screenshot review |
| v0.1 | 90 (0 skipped) | 22/22 | 3/3 |
| v0.2.0 | 115 (0 skipped) | 54/54 | 16/16 / 16/16 |
| v0.2.0 (current) | 115 | 56/56 | 18/18 / 18/18 |

Those numbers are what each stage recorded at acceptance and do not carry forward. Pass, skip, and skip-reason counts for a given run come from that run's CI or local report. A skip is not a pass.

## Features

| Category | Capability |
|---|---|
| File organization | Merge PDFs, split to ZIP, rotate, delete, and reorder pages |
| Format conversion | PDF→image / text / Word, image→PDF, Office→PDF |
| Other tools | PDF object optimization, text watermark (adaptive scaling or an explicit error), page numbers (rotated pages included), document info |
| Visual editor | Edit text lines, add text or images, visual covers, rotate / delete / move pages, find, highlight, save / save as, undo / redo |
| Large jobs | Background job progress, cancellation, idempotent submission; PDF rendering per page or batch |
| Browser recovery | Per-document IndexedDB cache the user turns on, off by default |

Text replacement reflows the text lines, so the original font and letter spacing are not guaranteed. Rotated pages cannot be edited in the editor yet, and you cannot add text, images, or covers to them. Normalize them with the rotate tool first.

The white cover in the editor is a visual overlay. It is not secure redaction, and it does not guarantee removal of the underlying image or other PDF objects. To delete sensitive content beyond recovery, use a validated secure redaction tool and check the output.

## Runtime and resource boundaries

- A single upload is capped at **600 MiB** (`600 × 1024 × 1024` bytes). A `multipart/form-data` request is separately capped at **600 MiB** for the full request body, including every file, field, and boundary; going over returns `413 UPLOAD_TOO_LARGE`. That middleware limit covers multipart bodies only and says nothing about the request body limit for other Content-Types.
- There is no fixed page limit. Whether a job finishes depends on page size, the rendering resources actually available, disk space, time, and the system budget.
- One heavy job runs at a time and at most one waits. A full queue returns `429 JOB_QUEUE_FULL`. Long jobs return `202` with a `jobId`, report progress through `GET /api/v1/jobs/{jobId}`, and accept cancellation through `POST /api/v1/jobs/{jobId}/cancel`. They use an `Idempotency-Key` to block duplicate submissions.
- PyMuPDF heavy jobs, single-page text extraction, anchor preflights, and PDF content validation run in separate subprocesses that share a 2 GiB worker memory budget, a 15-minute wall-clock budget per job, and a bounded queue. Going over either budget kills the worker and cleans up job artifacts; the original session file stays as it was. Cleanup of LibreOffice subdirectories after a forced kill is not fully verified; see "Known limitations".
- **24 MP** is the per-page preview budget. Above it, the preview drops resolution and says so. **160 MP** is the in-flight pixel budget per rendering batch, not a running total for an export; each batch frees its pixel buffers when it finishes.
- Opening a PDF.js preview has a cancellable **60-second** overall deadline. Module or document load failures show a retry entry, and a page that fails to render shows a page-level error with retry. Preview components hold the loading task, call `destroy()` during cleanup, then release the Blob URL.
- `/api/health` is a lightweight liveness check that does not touch LibreOffice. `/api/health/ready` returns the cached LibreOffice readiness state. The cold-start probe waits up to 20 seconds by default, and health requests never wait for it. On timeout the state stays `unknown` and can be retried after 5 seconds; only an explicit probe failure reports `not_ready`. A real cold start on this machine took about 13 seconds.
- Editing sessions live in memory and do not survive a restart. Open editors send heartbeats, a session expires after 2 hours of inactivity, and closing a file cleans up its session right away.

## Quick start

### One-click start on Windows

Double-click `start-editor.bat` in the project root. The service binds only to `127.0.0.1`; open **http://127.0.0.1:5173** to use it. Office→PDF needs a local LibreOffice install. If detection fails, point `PDF_EDITOR_SOFFICE` at `soffice.exe`.

### Manual start

```powershell
# Backend
cd backend
python -m pip install -r requirements.lock
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# Frontend (second terminal)
cd frontend
npm ci --legacy-peer-deps
npm run dev -- --host 127.0.0.1
```

Open **http://127.0.0.1:5173** in a browser. The backend accepts only a local Host and this one frontend Origin. There is no authentication, and LAN or public deployment is not supported.

## Editor save, undo, and recovery

- After a file opens, the frontend lazy-loads pages and text by viewport, and save requests carry the current `revision`. A stale revision or a mismatched text anchor returns `409 REVISION_CONFLICT`, and unconfirmed drafts are kept.
- Operations that touch the page or text model, such as deleting or moving pages and inserting text or images, ask for a save, discard, or cancel first. Save-as commits text drafts before writing. Closing, switching files, and reloading never drop a draft silently.
- Reversible operations use lightweight inverse records, falling back to server snapshots when needed. History holds at most 10 steps and 1 GiB of snapshots, so a large file may keep fewer than 10; the UI asks how to proceed when capacity runs short. After a page refresh or a recovery from the browser cache, undo and redo start empty.
- IndexedDB is off by default. Confirm per document that the file is not sensitive before turning it on. The cache is unencrypted, records expire after 7 days, and you can clear them per document or all at once. Expired records are deleted the next time drafts are listed; there is no guarantee of timed physical erasure while the browser is closed. If the browser quota runs out, the UI reports that the cache was not saved and the server session carries on.

## Testing and building

```powershell
# Python backend tests plus audit / SBOM tooling
cd backend
python -m pip install -r requirements-dev.lock
python -m pytest -ra

# Frontend checks
cd ../frontend
npm ci --legacy-peer-deps
npx tsc --noEmit
npm run check:pdfjs-resources
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

`npm run check:pdfjs-resources` compares the installed `pdfjs-dist` version with the one pinned in `package-lock.json`, then checks the exact local CMap, standard-font, and WASM decoder resource file sets and SHA-256 hashes, including JavaScript fallbacks and the required license files. It reports differences without changing the resource-loading setup or copying files.

The Playwright config starts the local backend and Vite. `PYTHON` can point at the interpreter used for the backend, which needs the locked backend dependencies installed, `uvicorn` included. Development machines use the installed Chrome by default and CI uses Playwright Chromium. GitHub Actions runs two jobs: the backend suite on Windows with LibreOffice, and the frontend checks (TypeScript, Vitest, production build, Chromium E2E, dependency audit, SBOM). Both archive their test results and audit reports. The project ships for Windows only, so CI does not cover Linux. For any given pytest run, the pass count, skip count, and skip reasons come from the CI report; skips do not count as passes.

The frontend needs Node.js 22.22.2 or a newer compatible release. Install commands match CI and use `--legacy-peer-deps`, which avoids npm 10 crashing on the optional peer dependency tree of Vitest 4. `@testing-library/dom`, which the tests need, is a direct development dependency.

### Regression log: two display fixes (2026-10-27)

- PDF.js URL loading and byte-based previews both set a local `wasmUrl`. The 13 decoders, JavaScript fallbacks, and license files that match the pinned version were added, which restored image masks such as CCITT and JBIG2 in scanned PDFs. A synthetic CCITT ImageMask browser regression checks the decoder requests and the actual canvas pixels, and a tampered-resource fixture is rejected by the SHA-256 check.
- The textarea for unedited text keeps text and background transparent, showing the original canvas font and position. A click enters editing, and drafts stay visible after blur and across tool switches. The canvas look comes back when you blur a line you did not change, undo a draft back to the original, or save and reload. Find uses a translucent highlight. Evicting a lazily loaded line model clears focus state, so no stale editor box reappears after a reload.
- Local verification: pytest **115 passed**, Vitest **56/56**, TypeScript and production build pass, Chrome full suite **18/18** in development mode and **18/18** against the production preview. After the last adjustment to the search wait condition, the same display regression passed separately **1/1** in development mode. The existing Starlette / httpx deprecation notices and the dynamic / static import and large-chunk warnings are still there.
- Two source documents from the user went through the browser regression: pages 1 and 2 of the scan, compared with the decoder blocked and then restored, and all three pages of the official document, where screenshots of the idle full DOM and of the canvas with the text overlay hidden matched pixel for pixel. Source SHA-256 values match before and after. The source files and screenshots were not committed, and the local evidence sits outside the repo at `E:\worksapce\PDF demo1\render-evidence-20261027`.
- This round fixed preview display only. Text under active editing still uses the existing browser editing control, and saving still goes through the existing backend text replacement. A search extraction that has not finished can still hit the existing busy job queue; the regression waits for extraction to complete before checking the save and leaves the queue limits alone.

### Regression log: v0.2 remediation (2026-10-21)

- Backend, locked environment: pytest **115 passed, 0 skipped**, covering the F01 and F02 server-side regressions, the long-text overflow API with real PDF output, the F10-F13 regressions, and the Office→PDF success path, with one pre-existing Starlette / httpx deprecation notice.
- Frontend: TypeScript passes, Vitest **54/54**, Vite production build succeeds.
- Playwright Chromium: **16/16** in development mode and **16/16** against the production preview, covering the F01 double save-race, draft recovery, the new long-text overflow flow in a real browser, preview retry after a transient 503, and StrictMode file switching and unmount. The preview suite runs against the production preview on port `4173`.
- PDF.js static resource check passes. The pinned and installed versions are both `6.4.299`, and the 169 CMap directory files and 16 standard-font directory files, each with its license file, match per-file SHA-256. A temporary fault fixture with a tampered resource was rejected by the script.
- Preview opening has a cancellable 60-second deadline. Vitest fault injection covers the open timeout, render rejection, retry, unmount, and Blob URL release after `loadingTask.destroy()`. A real browser retried after a transient 503 and then read and rendered a real PDF.
- After the CRLF fix, the Windows launcher ran through to `Ready!`, and both the backend and the Vite-proxied health checks returned `200`. `.gitattributes` pins batch files to CRLF on checkout.
- The Vite build still reports the `EditorPage.tsx` dynamic / static import notice and a chunk over 500 kB. Neither affects build success.
- This run did not execute `pip-audit`, `npm audit`, CycloneDX, or hosted CI, and did not push. None of those results count as passing for this run.

## Known limitations

1. PDF→Word is a best-effort layout reconstruction. Complex layouts and scans need a human to check them; the project has no OCR.
2. Office→PDF needs a local LibreOffice. The readiness probe has a 20-second budget; a timeout means the probe did not finish in time, which returns `unknown` and retries after a short cooldown. The real cold-start path has been verified on this machine. Conversion has its own 180-second timeout.
3. Password-protected PDFs are not supported yet. The document info tool reports encryption as "yes / no / undetermined".
4. PDF object optimization does not downsample images, so how much smaller a file gets depends on the document.
5. The browser recovery cache is unencrypted, so do not turn it on for sensitive files. Records older than 7 days are deleted the next time drafts are listed, with no guarantee of timed erasure while the browser is closed.
6. A Windows Job Object manages the LibreOffice process tree. When a forced kill lands at a different stage of an external conversion, not every profile and temp directory cleanup path has been verified.
7. Extreme fonts, non-standard page boxes and UserUnit, signature and form fidelity, 600 MiB files, 15-minute jobs, and long-term disk pressure have not been exhaustively tested. No claim is made about format fidelity for arbitrary PDFs.
8. PDF opening has a 60-second deadline and supports retry, and a page whose render throws can be retried on its own. A render promise that stays pending forever has no separate page-level deadline.
9. Not every outbound request has been through a full traffic audit, and hosted GitHub Actions has not run. Local checks do not replace that evidence.
10. Installing dependencies needs access to Python and npm package sources. Once installed, PDF processing uses no cloud service.

## Scope

This is a personal, offline PDF tool that the author publishes for learning and reference. There is no commercial support, and no promise that it is fit for production. This paragraph describes the project's scope and support level. It does not add restrictions to the rights the licenses grant; the licenses govern.

## License

Copyright (c) 2026 Hongsen Zheng.

The repository is licensed per directory:

- The root `LICENSE` is **AGPL-3.0** and covers the repository by default, `backend/` included. The backend processes PDFs through PyMuPDF, which is dual licensed under AGPL-3.0 or an Artifex commercial license, so the backend and the combined work ship under AGPL-3.0. No Artifex commercial license is held.
- `frontend/` is a separate program that calls the backend over an HTTP API and does not link PyMuPDF. It is licensed **MIT**; see [frontend/LICENSE](frontend/LICENSE).

The split took effect on 2026-10-27. Earlier commits were published under MIT, and those grants remain valid. Third-party dependencies, bundled resources, and the PyMuPDF terms are documented in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
