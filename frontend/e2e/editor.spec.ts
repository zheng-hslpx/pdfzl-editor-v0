import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { isAbsolute, join, relative, resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";

let fixtureDirectory = "";
let fixturePath = "";
let raceFixturePath = "";
let imageFixturePath = "";
let rotatedFixturePath = "";

test.beforeAll(() => {
  fixtureDirectory = mkdtempSync(join(tmpdir(), "pdfed-e2e-"));
  fixturePath = join(fixtureDirectory, "editor-smoke.pdf");
  raceFixturePath = join(fixtureDirectory, "editor-race.pdf");
  imageFixturePath = join(fixtureDirectory, "conversion-image.png");
  rotatedFixturePath = join(fixtureDirectory, "editor-rotations.pdf");
  const python = process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3");
  const source = [
    "import fitz, sys",
    "doc = fitz.open()",
    "page = doc.new_page(width=595, height=842)",
    "page.insert_text((72, 100), 'PAGE-1 EDITABLE', fontsize=14)",
    "page = doc.new_page(width=595, height=842)",
    "page.insert_text((72, 100), 'PAGE-2 ROTATED', fontsize=14)",
    "page.set_rotation(90)",
    "page = doc.new_page(width=595, height=842)",
    "page.insert_text((72, 100), 'PAGE-3 EDITABLE', fontsize=14)",
    "doc.save(sys.argv[1])",
    "doc.close()",
    "race = fitz.open()",
    "race_page = race.new_page(width=400, height=500)",
    "race_page.insert_text((40, 70), 'PAGE1 FIRST ORIGINAL', fontsize=14)",
    "race_page.insert_text((40, 110), 'PAGE1 SECOND ORIGINAL', fontsize=14)",
    "race.save(sys.argv[2])",
    "race.close()",
    "rotated = fitz.open()",
    "for angle in (0, 90, 180, 270):",
    "    page = rotated.new_page(width=420, height=600)",
    "    page.insert_text((48, 90), f'ROTATION-{angle}-CANVAS', fontsize=18)",
    "    page.draw_rect(fitz.Rect(48, 110, 180, 160), color=(1, 0, 0), fill=(1, 0, 0))",
    "    page.set_rotation(angle)",
    "rotated.save(sys.argv[4])",
    "rotated.close()",
    "from PIL import Image",
    "Image.new('RGB', (120, 80), (30, 90, 150)).save(sys.argv[3])",
  ].join("\n");
  execFileSync(python, ["-c", source, fixturePath, raceFixturePath, imageFixturePath, rotatedFixturePath], { stdio: "pipe" });
});

test.afterAll(() => {
  if (!fixtureDirectory) return;
  const root = resolve(tmpdir());
  const target = resolve(fixtureDirectory);
  const rel = relative(root, target);
  if (rel && !rel.startsWith(`..${process.platform === "win32" ? "\\" : "/"}`) && !isAbsolute(rel)) {
    rmSync(target, { recursive: true, force: true });
  }
});

async function openSample(page: Page): Promise<string> {
  const pdfRequest = page.waitForRequest(
    (request) => /\/api\/v1\/edit\/[^/]+\/download(?:\?|$)/.test(request.url()),
    { timeout: 30_000 },
  );
  await page.locator('input[type="file"][accept=".pdf,application/pdf"]').setInputFiles(fixturePath);
  const request = await pdfRequest;
  const match = /\/api\/v1\/edit\/([^/]+)\/download/.exec(new URL(request.url()).pathname);
  if (!match) throw new Error("Editor PDF range request did not include a docId");
  await expect(page.getByText("editor-smoke.pdf", { exact: true })).toBeVisible();
  await expect(page.locator("textarea").first()).toHaveValue("PAGE-1 EDITABLE");
  return match[1]!;
}

function appUrl(page: Page, pathname: string): string {
  return new URL(pathname, new URL(page.url()).origin).href;
}

async function openTwoLineSample(page: Page): Promise<string> {
  await page.goto("/editor");
  const pdfRequest = page.waitForRequest(
    (request) => /\/api\/v1\/edit\/[^/]+\/download(?:\?|$)/.test(request.url()),
    { timeout: 30_000 },
  );
  await page.locator('input[type="file"][accept=".pdf,application/pdf"]').setInputFiles(raceFixturePath);
  const request = await pdfRequest;
  const match = /\/api\/v1\/edit\/([^/]+)\/download/.exec(new URL(request.url()).pathname);
  if (!match) throw new Error("Editor PDF range request did not include a docId");
  await expect(page.getByText("editor-race.pdf", { exact: true })).toBeVisible();
  await expect(page.locator("textarea")).toHaveCount(2);
  await expect(page.locator("textarea").nth(0)).toHaveValue("PAGE1 FIRST ORIGINAL");
  await expect(page.locator("textarea").nth(1)).toHaveValue("PAGE1 SECOND ORIGINAL");
  return match[1]!;
}

async function pauseNextSuccessfulMutation(page: Page) {
  let release!: () => void;
  let resolveReached!: (job: Record<string, unknown>) => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  const reached = new Promise<Record<string, unknown>>((resolve) => { resolveReached = resolve; });
  let paused = false;
  const handler = async (route: import("@playwright/test").Route) => {
    const response = await route.fetch();
    const body = await response.text();
    const job = JSON.parse(body) as Record<string, unknown>;
    const result = job.result as Record<string, unknown> | undefined;
    if (!paused && job.state === "succeeded" && typeof result?.revision === "number") {
      paused = true;
      resolveReached(job);
      await gate;
    }
    await route.fulfill({ response, body });
  };
  await page.route((url) => /\/api\/v1\/jobs\/[^/]+$/.test(new URL(url).pathname), handler);
  return {
    reached,
    release,
    stop: () => page.unroute((url) => /\/api\/v1\/jobs\/[^/]+$/.test(new URL(url).pathname), handler),
  };
}

async function waitForJob(page: Page, jobId: string): Promise<Record<string, unknown>> {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    const response = await page.request.get(`http://127.0.0.1:8000/api/v1/jobs/${jobId}`);
    const job = await response.json() as Record<string, unknown>;
    if (["succeeded", "failed", "cancelled"].includes(String(job.state))) return job;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`job ${jobId} did not finish in time`);
}

async function fetchPageText(page: Page, docId: string) {
  const response = await page.request.get(`http://127.0.0.1:8000/api/v1/edit/${docId}/pages/0/text`);
  expect(response.status()).toBe(200);
  return await response.json() as {
    revision: number;
    page: {
      pageId: string;
      lines: Array<{
        id: string;
        pageId: string;
        originalHash: string;
        bbox: number[];
        fontSize: number;
        text: string;
      }>;
    };
  };
}

test("protects dirty navigation, supports undo/redo, detects stale revisions, saves as, and closes", async ({ page }) => {
  await page.goto("/editor");
  const docId = await openSample(page);

  const firstLine = page.locator("textarea").first();
  await firstLine.fill("PAGE-1 DRAFT");
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await expect(firstLine).toHaveValue("PAGE-1 EDITABLE");
  await page.getByRole("button", { name: "重做", exact: true }).click();
  await expect(firstLine).toHaveValue("PAGE-1 DRAFT");

  await page.getByRole("link", { name: "全部工具" }).click();
  await expect(page.getByRole("heading", { name: "有未保存的修改" })).toBeVisible();
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.getByRole("heading", { name: "有未保存的修改" })).toBeHidden();
  await expect(firstLine).toHaveValue("PAGE-1 DRAFT");

  const pagesResponse = await page.request.get(`http://127.0.0.1:8000/api/v1/edit/${docId}/pages`);
  const pageData = await pagesResponse.json() as { pages: Array<{ page: number; pageId: string }> };
  const rotated = pageData.pages.find((item) => item.page === 1)!;
  const rotatedWrite = await page.request.post(`http://127.0.0.1:8000/api/v1/edit/${docId}/text/add`, {
    data: {
      expectedRevision: 0,
      page: 1,
      pageId: rotated.pageId,
      x: 80,
      y: 120,
      text: "SHOULD BE REJECTED",
      fontSize: 12,
      color: "#000000",
    },
    headers: { "Idempotency-Key": `rotated-${Date.now()}` },
  });
  expect(rotatedWrite.status()).toBe(422);
  expect((await rotatedWrite.json()).code).toBe("ROTATED_PAGE_UNSUPPORTED");

  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByText("已保存 1 处修改")).toBeVisible();
  const staleWrite = await page.request.post(`http://127.0.0.1:8000/api/v1/edit/${docId}/pages/rotate`, {
    data: { expectedRevision: 0, page: 0, angle: 90 },
    headers: { "Idempotency-Key": `stale-${Date.now()}` },
  });
  expect(staleWrite.status()).toBe(409);
  expect((await staleWrite.json()).code).toBe("REVISION_CONFLICT");

  page.once("dialog", (dialog) => {
    void dialog.accept("saved-e2e.pdf");
  });
  const saveAsResponse = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return response.request().method() === "GET"
      && url.pathname.endsWith(`/edit/${docId}/download`)
      && url.searchParams.get("filename") === "saved-e2e.pdf";
  }, { timeout: 15_000 });
  await page.getByRole("button", { name: "另存为", exact: true }).click();
  expect((await saveAsResponse).status()).toBe(200);
  await expect(page.getByText("已导出 saved-e2e.pdf", { exact: true })).toBeVisible();
  await expect(page.getByText("未保存", { exact: true })).toHaveCount(0);
  await expect(page.getByText("待另存", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "有未保存的修改" })).toBeHidden();

  const closeResponse = page.waitForResponse((response) =>
    response.request().method() === "POST" && response.url().endsWith(`/edit/${docId}/close`),
  );
  await page.getByRole("button", { name: "关闭文件" }).click();
  expect((await closeResponse).status()).toBe(200);
  await expect(page.getByRole("heading", { name: "可视化编辑器" })).toBeVisible();
});

test("recovers an opted-in IndexedDB text draft after a browser reload", async ({ page }) => {
  await page.goto("/editor");
  await openSample(page);
  await page.locator("textarea").first().fill("DRAFT RESTORED AFTER RELOAD");
  await page.getByRole("button", { name: "开启草稿恢复", exact: true }).click();
  await expect(page.getByText(/缓存未加密/)).toBeVisible();
  await page.getByRole("button", { name: "我确认文档非敏感，开启缓存" }).click();
  await expect(page.getByText("已缓存", { exact: true })).toBeVisible({ timeout: 30_000 });

  page.once("dialog", (dialog) => dialog.accept());
  await page.reload();
  await page.getByRole("button", { name: "恢复", exact: true }).waitFor({ state: "visible", timeout: 30_000 });
  await page.getByRole("button", { name: "恢复", exact: true }).click();
  await expect(page.getByText(/撤销历史从空状态开始/)).toBeVisible();
  await expect(page.locator("textarea").first()).toHaveValue("DRAFT RESTORED AFTER RELOAD", { timeout: 30_000 });
});

test("shows task progress, prevents a duplicate submit, and requests cancellation", async ({ page }) => {
  let cancelRequested = false;
  await page.route("**/api/v1/convert/pdf-to-image", (route) => route.fulfill({
    status: 202,
    contentType: "application/json",
    body: JSON.stringify({ jobId: "e2e-cancel-job", state: "queued" }),
  }));
  await page.route("**/api/v1/jobs/e2e-cancel-job/cancel", async (route) => {
    cancelRequested = true;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }) });
  });
  await page.route("**/api/v1/jobs/e2e-cancel-job", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(cancelRequested
      ? { jobId: "e2e-cancel-job", state: "cancelled" }
      : {
          jobId: "e2e-cancel-job",
          state: "running",
          progress: { phase: "rendering", completed: 1, total: 300, unit: "pages" },
        }),
  }));

  await page.goto("/tool/convert");
  await page.locator('input[type="file"][accept=".pdf,application/pdf"]').setInputFiles(fixturePath);
  await page.getByRole("button", { name: "下一步：设置" }).click();
  await page.getByRole("button", { name: "下一步：执行" }).click();
  await page.getByRole("button", { name: "开始处理", exact: true }).click();
  await expect(page.getByText(/rendering：1\/300 pages/)).toBeVisible();
  await expect(page.getByRole("button", { name: "开始处理", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "取消任务" }).click();
  await expect(page.getByText("任务已取消", { exact: true })).toBeVisible();
  expect(cancelRequested).toBe(true);
});

test("renders PDF tool results under StrictMode across file changes and unmount", async ({ page }) => {
  const pdfErrors: string[] = [];
  page.on("pageerror", (error) => pdfErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error" && /DataCloneError|detached|PDF worker/i.test(message.text())) {
      pdfErrors.push(message.text());
    }
  });

  const runPreview = async (path: string) => {
    await page.locator('input[type="file"]').setInputFiles(path);
    await page.getByRole("button", { name: "下一步：设置" }).click();
    await page.getByRole("button", { name: "下一步：执行" }).click();
    await page.getByRole("button", { name: "开始处理", exact: true }).click();
    await expect(page.getByText("处理完成", { exact: true })).toBeVisible({ timeout: 30_000 });
    const canvas = page.locator("canvas").first();
    await expect(canvas).toBeVisible();
    await expect.poll(() => canvas.evaluate((element) => (element as HTMLCanvasElement).width)).toBeGreaterThan(0);
    await expect.poll(() => canvas.evaluate((element) => (element as HTMLCanvasElement).height)).toBeGreaterThan(0);
    return canvas;
  };

  await page.goto("/tool/compress");
  let canvas = await runPreview(fixturePath);
  const firstPagePixels = await canvas.evaluate((element) => {
    const canvas = element as HTMLCanvasElement;
    const context = canvas.getContext("2d");
    if (!context) return 0;
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
    let nonWhite = 0;
    for (let index = 0; index < pixels.length; index += 4) {
      if (pixels[index] !== 255 || pixels[index + 1] !== 255 || pixels[index + 2] !== 255) nonWhite += 1;
    }
    return nonWhite;
  });
  expect(firstPagePixels).toBeGreaterThan(0);
  await page.screenshot({ path: "test-results/f06-viewer-result.png", fullPage: true });

  await page.getByRole("button", { name: "处理其他文件" }).click();
  await page.getByRole("button", { name: "移除" }).click();
  canvas = await runPreview(raceFixturePath);
  expect(await canvas.evaluate((element) => (element as HTMLCanvasElement).width)).toBeGreaterThan(0);
  expect(pdfErrors).toEqual([]);

  await page.getByRole("link", { name: "全部工具", exact: true }).click();
  await expect(page.getByRole("heading", { name: "PDF 工具箱" })).toBeVisible();
  expect(pdfErrors).toEqual([]);
});

test("shows a retryable editor preview error and recovers after a transient PDF load failure", async ({ page }) => {
  let pdfLoadAttempts = 0;
  await page.route(
    (url) => /\/api\/v1\/edit\/[^/]+\/download$/.test(url.pathname),
    async (route) => {
      pdfLoadAttempts += 1;
      if (pdfLoadAttempts === 1) {
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ code: "TEMPORARY_PREVIEW_FAILURE", error: "temporary PDF preview failure" }),
        });
        return;
      }
      await route.continue();
    },
  );

  await page.goto("/editor");
  await openSample(page);
  await expect(page.getByText("PDF 预览加载失败", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "重试预览", exact: true }).click();
  await expect(page.locator('[data-page-number="0"] canvas')).toBeVisible();
  expect(pdfLoadAttempts).toBe(2);
});

test("uses actual conversion output types for PDF, DOCX, ZIP, image, and text results", async ({ page }) => {
  test.setTimeout(180_000);
  page.setDefaultTimeout(10_000);

  const convert = async (direction: string, inputPath: string, pages?: string) => {
    await page.goto("/tool/convert");
    await page.locator('input[type="file"]').setInputFiles(inputPath);
    await page.getByRole("button", { name: "下一步：设置" }).click();
    await page.getByRole("textbox", { name: "转换方向" }).click();
    await page.getByRole("option", { name: direction }).click();
    if (pages) await page.getByRole("textbox", { name: "页码范围" }).fill(pages);
    await page.getByRole("button", { name: "下一步：执行" }).click();
    await page.getByRole("button", { name: "开始处理", exact: true }).click();
    await expect(page.getByText("处理完成", { exact: true })).toBeVisible({ timeout: 60_000 });
  };

  await convert("PDF → Word（DOCX，格式可能丢失）", fixturePath);
  await expect(page.getByText("输出格式：Word 文档", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "下载文件" })).toBeVisible();
  await expect(page.getByRole("button", { name: "在编辑器中打开" })).toHaveCount(0);
  await expect(page.locator("canvas, img")).toHaveCount(0);

  await convert("PDF → 图片（PNG/JPEG）", fixturePath);
  await expect(page.getByText("输出格式：ZIP 压缩包", { exact: true })).toBeVisible();
  await expect(page.getByText("editor-smoke_images.zip", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "在编辑器中打开" })).toHaveCount(0);
  await expect(page.locator("canvas, img")).toHaveCount(0);

  await convert("PDF → 图片（PNG/JPEG）", fixturePath, "1");
  await expect(page.getByRole("img", { name: "result" })).toBeVisible();
  await expect.poll(() => page.locator("img").first().evaluate((image) => (image as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
  await expect(page.getByRole("button", { name: "在编辑器中打开" })).toHaveCount(0);

  await convert("PDF → 文本（TXT）", fixturePath);
  await expect(page.getByText(/PAGE-1 EDITABLE/)).toBeVisible();
  await expect(page.locator("canvas, img")).toHaveCount(0);

  await convert("图片 → PDF", imageFixturePath);
  await expect(page.getByRole("button", { name: "在编辑器中打开" })).toBeVisible();
  await expect(page.locator("canvas").first()).toBeVisible();
});

test("hides untrusted text overlays on rotated pages while rendering each page rotation", async ({ page }) => {
  page.setDefaultTimeout(10_000);
  const loadedTextPages = new Set<number>();
  page.on("response", (response) => {
    const match = /\/pages\/(\d+)\/text$/.exec(new URL(response.url()).pathname);
    if (match) loadedTextPages.add(Number(match[1]));
  });

  await page.goto("/editor");
  const pdfRequest = page.waitForRequest(
    (request) => /\/api\/v1\/edit\/[^/]+\/download(?:\?|$)/.test(request.url()),
    { timeout: 30_000 },
  );
  await page.locator('input[type="file"][accept=".pdf,application/pdf"]').setInputFiles(rotatedFixturePath);
  const request = await pdfRequest;
  const match = /\/api\/v1\/edit\/([^/]+)\/download/.exec(new URL(request.url()).pathname);
  if (!match) throw new Error("Rotated PDF preview request did not include a docId");
  const docId = match[1]!;
  await expect(page.getByText("editor-rotations.pdf", { exact: true })).toBeVisible();

  const pagesResponse = await page.request.get(`http://127.0.0.1:8000/api/v1/edit/${docId}/pages?offset=0&limit=10`);
  expect(pagesResponse.status()).toBe(200);
  const metadata = await pagesResponse.json() as { revision: number; pages: Array<{ page: number; pageId: string; rotation: number }> };
  expect(metadata.pages.map((item) => item.rotation)).toEqual([0, 90, 180, 270]);

  for (const [pageNo, rotation] of [0, 90, 180, 270].entries()) {
    const textResponse = await page.request.get(`http://127.0.0.1:8000/api/v1/edit/${docId}/pages/${pageNo}/text`);
    expect(textResponse.status()).toBe(200);
    const model = await textResponse.json() as { page: { lines: Array<{ text: string; bbox: number[] }> } };
    const line = model.page.lines.find((item) => item.text === `ROTATION-${rotation}-CANVAS`);
    expect(line).toBeDefined();

    await page.evaluate((index) => {
      const maxScroll = document.documentElement.scrollHeight - window.innerHeight;
      window.scrollTo(0, Math.max(0, maxScroll * index / 3));
    }, pageNo);
    const pageFrame = page.locator(`[data-page-number="${pageNo}"]`);
    await pageFrame.scrollIntoViewIfNeeded();
    const canvas = pageFrame.locator("canvas");
    await expect(canvas).toBeVisible();
    await expect.poll(() => canvas.evaluate((element) => (element as HTMLCanvasElement).width)).toBeGreaterThan(0);
    await expect.poll(() => canvas.evaluate((element) => (element as HTMLCanvasElement).height)).toBeGreaterThan(0);
    await expect.poll(() => loadedTextPages.has(pageNo)).toBe(true);

    const image = await canvas.evaluate((element) => {
      const canvas = element as HTMLCanvasElement;
      const context = canvas.getContext("2d");
      if (!context) return { width: canvas.width, height: canvas.height, nonWhitePixels: 0 };
      const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
      let nonWhitePixels = 0;
      for (let index = 0; index < pixels.length; index += 4) {
        if (pixels[index] !== 255 || pixels[index + 1] !== 255 || pixels[index + 2] !== 255) nonWhitePixels += 1;
      }
      return { width: canvas.width, height: canvas.height, nonWhitePixels };
    });
    expect(image.nonWhitePixels).toBeGreaterThan(0);
    expect(image.width > image.height).toBe(rotation === 90 || rotation === 270);
    await pageFrame.screenshot({ path: `test-results/f08-page-${rotation}.png` });

    const overlays = pageFrame.locator("textarea");
    if (rotation === 0) {
      await expect(overlays).toHaveCount(1);
      await expect(overlays.first()).toHaveValue("ROTATION-0-CANVAS");
      const frameBox = await pageFrame.boundingBox();
      const overlayBox = await overlays.first().boundingBox();
      expect(frameBox).not.toBeNull();
      expect(overlayBox).not.toBeNull();
      expect(Math.abs(overlayBox!.x - (frameBox!.x + line!.bbox[0]! * 1.2))).toBeLessThan(2);
      expect(Math.abs(overlayBox!.y - (frameBox!.y + line!.bbox[1]! * 1.2))).toBeLessThan(2);
    } else {
      await expect(overlays).toHaveCount(0);
      await expect(page.getByTestId(`rotation-notice-${pageNo}`)).toContainText("文字覆盖层已隐藏");
    }
  }

  const rotatedPage = metadata.pages[1]!;
  const rejectedWrite = await page.request.post(`http://127.0.0.1:8000/api/v1/edit/${docId}/text/add`, {
    data: {
      expectedRevision: metadata.revision,
      page: rotatedPage.page,
      pageId: rotatedPage.pageId,
      x: 48,
      y: 180,
      text: "MUST NOT BE WRITTEN",
    },
    headers: { "Idempotency-Key": `rotated-overlay-${Date.now()}` },
  });
  expect(rejectedWrite.status()).toBe(422);
  expect((await rejectedWrite.json()).code).toBe("ROTATED_PAGE_UNSUPPORTED");
});

test("renders the review Chinese PDF with local CMaps and fonts", async ({ page }) => {
  page.setDefaultTimeout(15_000);
  const cmapResponses: Array<{ url: string; status: number }> = [];
  const fontWarnings: string[] = [];
  const externalRequests: string[] = [];
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (url.pathname.includes("/cmaps/")) cmapResponses.push({ url: url.href, status: response.status() });
  });
  page.on("console", (message) => {
    if (message.type() === "warning" || message.type() === "error") {
      if (/cMapUrl|standardFontDataUrl|font loading|loadFont/i.test(message.text())) fontWarnings.push(message.text());
    }
  });
  page.on("request", (request) => {
    const url = new URL(request.url());
    if ((url.protocol === "http:" || url.protocol === "https:")
      && !["127.0.0.1", "localhost"].includes(url.hostname)) externalRequests.push(url.href);
  });

  await page.goto("/editor");
  const pdfRequest = page.waitForRequest(
    (request) => /\/api\/v1\/edit\/[^/]+\/download(?:\?|$)/.test(request.url()),
    { timeout: 30_000 },
  );
  const sourcePath = join(import.meta.dirname, "fixtures", "mixed-cjk.pdf");
  await page.locator('input[type="file"][accept=".pdf,application/pdf"]').setInputFiles(sourcePath);
  const request = await pdfRequest;
  const match = /\/api\/v1\/edit\/([^/]+)\/download/.exec(new URL(request.url()).pathname);
  if (!match) throw new Error("Chinese PDF preview request did not include a docId");
  await expect(page.getByText("mixed-cjk.pdf", { exact: true })).toBeVisible();

  const pageFrame = page.locator('[data-page-number="0"]');
  await pageFrame.scrollIntoViewIfNeeded();
  const canvas = pageFrame.locator("canvas");
  await expect(canvas).toBeVisible();
  await expect.poll(() => canvas.evaluate((element) => (element as HTMLCanvasElement).width)).toBeGreaterThan(0);
  await expect.poll(() => cmapResponses.some((response) => response.status === 200)).toBe(true);

  const pixelCounts = await canvas.evaluate((element) => {
    const canvas = element as HTMLCanvasElement;
    const context = canvas.getContext("2d");
    if (!context) return { cjk: 0, english: 0 };
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
    const countDarkPixels = (x: number, y: number, width: number, height: number) => {
      let count = 0;
      for (let row = y; row < y + height; row += 1) {
        for (let column = x; column < x + width; column += 1) {
          const index = (row * canvas.width + column) * 4;
          if (pixels[index]! < 245 && pixels[index + 1]! < 245 && pixels[index + 2]! < 245) count += 1;
        }
      }
      return count;
    };
    return {
      cjk: countDarkPixels(
        Math.floor(canvas.width * 0.05), Math.floor(canvas.height * 0.24),
        Math.floor(canvas.width * 0.38), Math.floor(canvas.height * 0.12),
      ),
      english: countDarkPixels(
        Math.floor(canvas.width * 0.05), Math.floor(canvas.height * 0.08),
        Math.floor(canvas.width * 0.55), Math.floor(canvas.height * 0.12),
      ),
    };
  });
  expect(pixelCounts.english).toBeGreaterThan(20);
  expect(pixelCounts.cjk).toBeGreaterThan(20);
  expect(cmapResponses.length).toBeGreaterThan(0);
  expect(cmapResponses.every((response) => response.status === 200)).toBe(true);

  const fontResponse = await page.request.get(`${new URL(page.url()).origin}/standard_fonts/FoxitSerif.pfb`);
  expect(fontResponse.status()).toBe(200);
  expect(fontWarnings).toEqual([]);
  expect(externalRequests).toEqual([]);
  await canvas.screenshot({ path: "test-results/f09-cjk-canvas.png" });
});

test("launcher origin is configured and the active E2E origin accepts a PDF upload", async ({ page }) => {
  const launcher = readFileSync(join(import.meta.dirname, "../../start-editor.bat"), "utf8");
  expect(launcher).toContain('set "UI_URL=http://127.0.0.1:5173"');
  expect(launcher).toContain('curl.exe --fail --silent --show-error --max-time 2 "%HEALTH_URL%"');
  expect(launcher).not.toContain("http://localhost:5173");

  await page.goto("/editor");
  const docId = await openSample(page);
  await expect(page).toHaveURL(appUrl(page, "/editor"));
  const pagesResponse = await page.request.get(`http://127.0.0.1:8000/api/v1/edit/${docId}/pages`);
  expect(pagesResponse.status()).toBe(200);
  expect((await pagesResponse.json()).pageCount).toBe(3);
  await expect(page.getByText("editor-smoke.pdf", { exact: true })).toBeVisible();
});

test("protects the session and dirty draft during browser back when recovery cache is off", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "可视化编辑器", exact: true }).click();
  const docId = await openSample(page);
  await page.locator("textarea").first().fill("BROWSER BACK DRAFT");
  await expect(page.getByRole("button", { name: "开启草稿恢复" })).toBeVisible();

  await page.evaluate(() => window.history.back());
  await expect(page.getByRole("heading", { name: "有未保存的修改" })).toBeVisible();
  await expect(page).toHaveURL(/\/editor$/);
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.locator("textarea").first()).toHaveValue("BROWSER BACK DRAFT");
  await expect(page.getByText("editor-smoke.pdf", { exact: true })).toBeVisible();

  const closeResponse = page.waitForResponse((response) =>
    response.request().method() === "POST" && response.url().endsWith(`/edit/${docId}/close`),
  );
  await page.evaluate(() => window.history.back());
  await page.getByRole("button", { name: "放弃草稿并继续" }).click();
  await expect(page).toHaveURL(appUrl(page, "/"));
  expect((await closeResponse).status()).toBe(200);
});

test("protects dirty drafts during browser forward navigation", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "可视化编辑器", exact: true }).click();
  await page.getByRole("link", { name: "全部工具", exact: true }).click();
  await expect(page).toHaveURL(appUrl(page, "/"));
  await page.evaluate(() => window.history.back());
  await expect(page).toHaveURL(/\/editor$/);
  await openSample(page);
  await page.locator("textarea").first().fill("BROWSER FORWARD DRAFT");

  await page.evaluate(() => window.history.forward());
  await expect(page.getByRole("heading", { name: "有未保存的修改" })).toBeVisible();
  await expect(page).toHaveURL(/\/editor$/);
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.locator("textarea").first()).toHaveValue("BROWSER FORWARD DRAFT");

  await page.evaluate(() => window.history.forward());
  await page.getByRole("button", { name: "放弃草稿并继续" }).click();
  await expect(page).toHaveURL(appUrl(page, "/"));
});

test("keeps the editor session open when navigation is attempted during a save", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "可视化编辑器", exact: true }).click();
  await openSample(page);
  await page.locator("textarea").first().fill("SAVE IN PROGRESS");
  const paused = await pauseNextSuccessfulMutation(page);

  await page.getByRole("button", { name: "保存", exact: true }).click();
  await paused.reached;
  await page.evaluate(() => window.history.back());
  await expect(page.getByRole("heading", { name: "有未保存的修改" })).toBeVisible();
  await expect(page).toHaveURL(/\/editor$/);
  await expect(page.getByRole("button", { name: "放弃草稿并继续" })).toBeDisabled();
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.locator("textarea").first()).toHaveValue("SAVE IN PROGRESS");
  await expect(page).toHaveURL(/\/editor$/);

  paused.release();
  await paused.stop();
  await expect(page.getByText("已保存 1 处修改", { exact: true })).toBeVisible();
  await expect(page.getByText("editor-smoke.pdf", { exact: true })).toBeVisible();
});

test("saves and exports the edited PDF before completing browser back navigation", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "可视化编辑器", exact: true }).click();
  const docId = await openSample(page);
  await page.locator("textarea").first().fill("SAVED BEFORE NAVIGATION");

  page.once("dialog", (dialog) => { void dialog.accept("saved-before-navigation.pdf"); });
  const saveAsResponse = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return response.request().method() === "GET"
      && url.pathname.endsWith(`/edit/${docId}/download`)
      && url.searchParams.get("filename") === "saved-before-navigation.pdf";
  }, { timeout: 15_000 });
  await page.evaluate(() => window.history.back());
  await page.getByRole("button", { name: "另存为并继续" }).click();
  expect((await saveAsResponse).status()).toBe(200);
  await expect(page).toHaveURL(appUrl(page, "/"));
});

test("keeps a save-race draft on its original row across reordered extraction and a second save", async ({ page }) => {
  const docId = await openTwoLineSample(page);
  const paused = await pauseNextSuccessfulMutation(page);

  await page.locator("textarea").nth(0).fill("FIRST SAVED VALUE");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  const firstJob = await paused.reached;
  expect((firstJob.result as { revision: number }).revision).toBe(1);

  const afterFirstSave = await fetchPageText(page, docId);
  expect(afterFirstSave.revision).toBe(1);
  expect(afterFirstSave.page.lines.map((line) => line.text)).toEqual([
    "PAGE1 SECOND ORIGINAL",
    "FIRST SAVED VALUE",
  ]);

  await page.locator("textarea").nth(0).fill("FIRST NEW INPUT DURING SAVE");
  paused.release();
  await expect(page.locator("textarea").nth(0)).toHaveValue("PAGE1 SECOND ORIGINAL");
  await expect(page.locator("textarea").nth(1)).toHaveValue("FIRST NEW INPUT DURING SAVE");
  await expect(page.getByText(/保存期间的新修改仍保留/)).toBeVisible();
  await paused.stop();

  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByText("已保存 1 处修改", { exact: true })).toBeVisible();
  const afterSecondSave = await fetchPageText(page, docId);
  expect(afterSecondSave.revision).toBe(2);
  const firstRow = afterSecondSave.page.lines.find((line) => line.text === "FIRST NEW INPUT DURING SAVE");
  const secondRow = afterSecondSave.page.lines.find((line) => line.text === "PAGE1 SECOND ORIGINAL");
  expect(firstRow).toBeDefined();
  expect(secondRow).toBeDefined();
  expect(firstRow!.bbox[1]).toBeLessThan(80);
  expect(secondRow!.bbox[1]).toBeGreaterThan(80);
  expect(afterSecondSave.page.lines.some((line) => line.text === "FIRST SAVED VALUE")).toBe(false);
});

test("preserves a save-race draft as a visible conflict when its document version changes", async ({ page }) => {
  const docId = await openTwoLineSample(page);
  const paused = await pauseNextSuccessfulMutation(page);

  await page.locator("textarea").nth(0).fill("FIRST SAVED VALUE");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  const firstJob = await paused.reached;
  expect((firstJob.result as { revision: number }).revision).toBe(1);
  await page.locator("textarea").nth(0).fill("FIRST NEW INPUT DURING SAVE");

  const afterFirstSave = await fetchPageText(page, docId);
  const savedFirstRow = afterFirstSave.page.lines.find((line) => line.text === "FIRST SAVED VALUE");
  expect(savedFirstRow).toBeDefined();
  const externalResponse = await page.request.post(`http://127.0.0.1:8000/api/v1/edit/${docId}/apply`, {
    data: {
      expectedRevision: 1,
      edits: [{
        page: 0,
        pageId: savedFirstRow!.pageId,
        lineId: savedFirstRow!.id,
        originalHash: savedFirstRow!.originalHash,
        bbox: savedFirstRow!.bbox,
        original: savedFirstRow!.text,
        text: "EXTERNAL VERSION",
        fontSize: savedFirstRow!.fontSize,
      }],
    },
    headers: { "Idempotency-Key": `save-race-conflict-${Date.now()}` },
  });
  expect(externalResponse.status()).toBe(202);
  const externalJob = await waitForJob(page, (await externalResponse.json()).jobId as string);
  expect(externalJob.state).toBe("succeeded");

  paused.release();
  await expect(page.getByText("草稿定位冲突", { exact: true })).toBeVisible();
  await expect(page.getByTestId("anchor-conflict-text")).toContainText("FIRST NEW INPUT DURING SAVE");
  await paused.stop();

  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByText(/无法安全定位的冲突草稿/)).toBeVisible();
  const currentPage = await fetchPageText(page, docId);
  expect(currentPage.revision).toBe(2);
  expect(currentPage.page.lines.some((line) => line.text === "EXTERNAL VERSION")).toBe(true);
  expect(currentPage.page.lines.some((line) => line.text === "PAGE1 SECOND ORIGINAL")).toBe(true);
  expect(currentPage.page.lines.some((line) => line.text === "FIRST NEW INPUT DURING SAVE")).toBe(false);
});

test("keeps an overflowing replacement draft while the PDF revision stays unchanged", async ({ page }) => {
  await page.goto("/editor");
  const docId = await openSample(page);
  const replacement = "VISIBLE-".repeat(100);

  await page.locator("textarea").first().fill(replacement);
  await page.getByRole("button", { name: "保存", exact: true }).click();

  await expect(page.getByText(/替换文字在第 1 页无法完整放入编辑区域/)).toBeVisible();
  await expect(page.locator("textarea").first()).toHaveValue(replacement);
  await expect(page.getByText("未保存", { exact: true })).toBeVisible();
  const currentPage = await fetchPageText(page, docId);
  expect(currentPage.revision).toBe(0);
  expect(currentPage.page.lines.some((line) => line.text === "PAGE-1 EDITABLE")).toBe(true);
  expect(currentPage.page.lines.some((line) => line.text === replacement)).toBe(false);
});
