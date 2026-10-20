import { defineConfig, devices } from "@playwright/test";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";

const python = process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3");
const pythonCommand = /[\s]/.test(python) ? `"${python}"` : python;
const tempRoot = join(tmpdir(), `pdfed-pw-${process.pid}`);
const useBundledChromium = Boolean(process.env.CI);

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [
    ["list"],
    ["junit", { outputFile: "test-results/e2e-junit.xml" }],
    ["html", { outputFolder: "playwright-report", open: "never" }],
  ],
  use: {
    baseURL: "http://127.0.0.1:5173",
    acceptDownloads: true,
    trace: "retain-on-failure",
    launchOptions: useBundledChromium
      ? undefined
      : { channel: process.env.PLAYWRIGHT_CHANNEL ?? "chrome" },
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: [
    {
      command: `${pythonCommand} -m uvicorn app.main:app --host 127.0.0.1 --port 8000`,
      cwd: resolve(import.meta.dirname, "../backend"),
      url: "http://127.0.0.1:8000/api/health",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        PDF_EDITOR_TEMP_DIR: join(tempRoot, "backend"),
        PDF_EDITOR_UI_ORIGIN: "http://127.0.0.1:5173",
      },
    },
    {
      command: "npm run dev -- --host 127.0.0.1",
      cwd: import.meta.dirname,
      url: "http://127.0.0.1:5173",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
