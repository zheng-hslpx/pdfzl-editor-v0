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

import { JobCancelledError, postJobBody, type JobProgress } from "../api/client";

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
