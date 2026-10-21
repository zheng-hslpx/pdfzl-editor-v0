import { beforeEach, describe, expect, it, vi } from "vitest";

const axiosFake = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  isAxiosError: vi.fn((value: unknown) => Boolean(value && typeof value === "object" && "isAxiosError" in value)),
}));

vi.mock("axios", () => ({
  default: {
    create: () => ({ get: axiosFake.get, post: axiosFake.post }),
    isAxiosError: axiosFake.isAxiosError,
  },
}));

import { extractError, JobCancelledError, postJobBody, type JobProgress } from "../api/client";

describe("client long-running jobs", () => {
  beforeEach(() => {
    axiosFake.get.mockReset();
    axiosFake.post.mockReset();
    axiosFake.isAxiosError.mockClear();
    axiosFake.post.mockResolvedValue({ data: { jobId: "job-1", state: "queued" } });
  });

  it("reports progress and returns the successful JSON result", async () => {
    axiosFake.get
      .mockResolvedValueOnce({ data: {
        jobId: "job-1", state: "running",
        progress: { phase: "render", completed: 2, total: 5, unit: "pages" },
      } })
      .mockResolvedValueOnce({ data: {
        jobId: "job-1", state: "succeeded", result: { revision: 4 },
      } });
    const progress: JobProgress[] = [];
    const result = await postJobBody<{ revision: number }>("/edit/test", {}, {
      onProgress: (item) => progress.push(item),
    });

    expect(result).toEqual({ revision: 4 });
    expect(progress.map((item) => item.state)).toEqual(["running", "succeeded"]);
    expect(progress[0]?.progress).toEqual({ phase: "render", completed: 2, total: 5, unit: "pages" });
    expect(axiosFake.post.mock.calls[0]?.[2].headers).toEqual({ "Idempotency-Key": expect.any(String) });
  });

  it("sends cancellation for an aborted queued task and waits for its cancelled terminal state", async () => {
    axiosFake.get.mockResolvedValueOnce({ data: { jobId: "job-1", state: "cancelled" } });
    const controller = new AbortController();
    controller.abort();

    await expect(postJobBody("/edit/test", {}, { signal: controller.signal })).rejects.toBeInstanceOf(JobCancelledError);
    expect(axiosFake.post).toHaveBeenCalledTimes(2);
    expect(axiosFake.post.mock.calls[1]?.[0]).toBe("/jobs/job-1/cancel");
  });

  it("marks cancellation as too late after the server enters committing", async () => {
    const conflict = { isAxiosError: true, response: { status: 409, data: { code: "JOB_COMMITTING" } } };
    axiosFake.post
      .mockResolvedValueOnce({ data: { jobId: "job-1", state: "running" } })
      .mockRejectedValueOnce(conflict);
    axiosFake.get
      .mockResolvedValueOnce({ data: { jobId: "job-1", state: "committing" } })
      .mockResolvedValueOnce({ data: { jobId: "job-1", state: "succeeded", result: { ok: true } } });
    const progress: JobProgress[] = [];
    const controller = new AbortController();
    const promise = postJobBody("/edit/test", {}, {
      signal: controller.signal,
      onProgress: (item) => progress.push(item),
    });
    await vi.waitFor(() => expect(axiosFake.get).toHaveBeenCalledTimes(1));
    controller.abort();

    await expect(promise).resolves.toEqual({ ok: true });
    expect(progress.some((item) => item.cancelTooLate)).toBe(true);
  });
});

describe("extractError", () => {
  it("shows a structured business error returned by the API", async () => {
    const error = Object.assign(new Error("Request failed with status code 422"), {
      isAxiosError: true,
      response: { status: 422, data: { error: "页码范围无效", code: "INVALID_INPUT" } },
    });

    await expect(extractError(error)).resolves.toBe("页码范围无效");
  });

  it("shows structured business errors returned as a blob", async () => {
    const error = Object.assign(new Error("Request failed with status code 400"), {
      isAxiosError: true,
      response: {
        status: 400,
        data: new Blob([JSON.stringify({ error: "水印文字不能为空", code: "INVALID_INPUT" })], {
          type: "application/json",
        }),
      },
    });

    await expect(extractError(error)).resolves.toBe("水印文字不能为空");
  });

  it("reports a network failure separately from API errors", async () => {
    const error = Object.assign(new Error("Network Error"), {
      isAxiosError: true,
      code: "ERR_NETWORK",
    });

    await expect(extractError(error)).resolves.toBe(
      "无法连接到本地服务，请确认 PDF Editor 服务正在运行。",
    );
  });

  it("reports request cancellation separately from a network fault", async () => {
    const error = Object.assign(new Error("canceled"), {
      isAxiosError: true,
      code: "ERR_CANCELED",
    });

    await expect(extractError(error)).resolves.toBe("请求已取消。");
  });

  it("preserves a known structured server-side operation error", async () => {
    const error = Object.assign(new Error("Request failed with status code 500"), {
      isAxiosError: true,
      response: {
        status: 500,
        data: { error: "PDF 转 Word 失败", code: "CONVERSION_FAILED" },
      },
    });

    await expect(extractError(error)).resolves.toBe("PDF 转 Word 失败");
  });

  it("hides unknown server exception details behind a stable message", async () => {
    const error = Object.assign(new Error("Request failed with status code 500"), {
      isAxiosError: true,
      response: {
        status: 500,
        data: { error: "服务器内部错误", code: "INTERNAL_ERROR" },
      },
    });

    await expect(extractError(error)).resolves.toBe("服务器发生未知错误（HTTP 500），请稍后重试。");
  });

  it("preserves ordinary non-Axios error messages", async () => {
    await expect(extractError(new Error("local validation failed"))).resolves.toBe(
      "local validation failed",
    );
  });
});
