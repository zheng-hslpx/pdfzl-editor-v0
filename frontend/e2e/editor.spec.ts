import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { isAbsolute, join, relative, resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";

let fixtureDirectory = "";
let fixturePath = "";

test.beforeAll(() => {
  fixtureDirectory = mkdtempSync(join(tmpdir(), "pdfed-e2e-"));
  fixturePath = join(fixtureDirectory, "editor-smoke.pdf");
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
  ].join("; ");
  execFileSync(python, ["-c", source, fixturePath], { stdio: "pipe" });
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
  await expect(page.locator("textarea").first()).toHaveValue("DRAFT RESTORED AFTER RELOAD", { timeout: 30_000 });
  await expect(page.getByText(/撤销历史从空状态开始/)).toBeVisible();
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
