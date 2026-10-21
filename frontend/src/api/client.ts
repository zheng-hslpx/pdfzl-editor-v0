import axios from "axios";

/** API client for /api/v1 tool endpoints. */
const client = axios.create({
  baseURL: "/api/v1",
  timeout: 120_000,
});

export interface HealthResponse {
  status: string;
  libreoffice?: boolean;
}

/** Health endpoint lives at /api/health (outside /api/v1). */
export async function checkHealth(): Promise<HealthResponse> {
  const { data } = await axios.get<HealthResponse>("/api/health", { timeout: 10_000 });
  return data;
}

export interface FileResult {
  blob: Blob;
  filename: string;
}

export type JobState = "queued" | "running" | "committing" | "succeeded" | "failed" | "cancelled";

export interface JobProgress {
  jobId: string;
  state: JobState;
  progress: { phase: string; completed: number | null; total: number | null; unit: string | null } | null;
  cancelTooLate?: boolean;
}

export interface JobOptions {
  signal?: AbortSignal;
  onProgress?: (value: JobProgress) => void;
  historyPolicy?: HistoryPolicy;
  onHistoryConfirm?: (plan: HistoryCapacityPlan) => Promise<HistoryPolicy | "cancel">;
}

export type HistoryPolicy = "snapshot" | "inverse" | "evict_oldest" | "no_undo";

export interface HistoryCapacityPlan {
  mode: "snapshot" | "inverse";
  needsConfirmation: boolean;
  currentSteps: number;
  maxSteps: number;
  currentBytes: number;
  maxBytes: number;
  estimateBytes: number;
  canEvictOldest: boolean;
  canSkipUndo: boolean;
}

interface AcceptedJob {
  jobId: string;
  state: JobState;
}

interface JobStatus extends AcceptedJob {
  progress?: JobProgress["progress"];
  error?: { code?: string; message?: string } | null;
  resultUrl?: string;
  filename?: string;
  result?: unknown;
}

export class ApiJobError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "ApiJobError";
    this.code = code;
  }
}

export class JobCancelledError extends Error {
  constructor() {
    super("任务已取消");
    this.name = "JobCancelledError";
  }
}

function makeIdempotencyKey(): string {
  return globalThis.crypto?.randomUUID?.() ?? `job-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function pollJob<T>(accepted: AcceptedJob, options: JobOptions = {}): Promise<JobStatus & { result?: T; cancelTooLate: boolean }> {
  const { jobId } = accepted;
  let cancelSent = false;
  let cancelTooLate = false;
  let lastState: JobState = accepted.state;
  const notify = (status: JobStatus) => {
    lastState = status.state;
    options.onProgress?.({
      jobId,
      state: status.state,
      progress: status.progress ?? null,
      cancelTooLate,
    });
  };

  while (true) {
    if (options.signal?.aborted && !cancelSent && !["succeeded", "failed", "cancelled"].includes(lastState)) {
      cancelSent = true;
      try {
        await client.post(`/jobs/${jobId}/cancel`);
      } catch (err) {
        if (axios.isAxiosError(err) && err.response?.status === 409) {
          const data = err.response.data as { code?: string } | undefined;
          if (data?.code === "JOB_COMMITTING") {
            cancelTooLate = true;
          } else {
            throw err;
          }
        } else {
          throw err;
        }
      }
    }

    const { data } = await client.get<JobStatus>(`/jobs/${jobId}`);
    notify(data);
    if (data.state === "succeeded") return { ...data, cancelTooLate } as JobStatus & { result?: T; cancelTooLate: boolean };
    if (data.state === "failed") {
      throw new ApiJobError(data.error?.code ?? "JOB_FAILED", data.error?.message ?? "任务失败");
    }
    if (data.state === "cancelled") throw new JobCancelledError();
    await sleep(250);
  }
}

async function submitMultipartJob<T>(
  endpoint: string,
  form: FormData,
  responseType: "json" | "blob",
  options: JobOptions = {},
): Promise<T> {
  const { data: accepted } = await client.post<AcceptedJob>(endpoint, form, {
    timeout: 0,
    headers: {
      "Idempotency-Key": makeIdempotencyKey(),
      ...(options.historyPolicy ? { "History-Policy": options.historyPolicy } : {}),
    },
  });
  const status = await pollJob<T>(accepted, options);
  if (responseType === "json") return status.result as T;
  if (!status.resultUrl) throw new ApiJobError("JOB_RESULT_MISSING", "任务未返回文件结果");
  const response = await axios.get<Blob>(status.resultUrl, { responseType: "blob", timeout: 0 });
  const filename = parseFilename(response.headers["content-disposition"], status.filename ?? "download.pdf");
  return { blob: response.data as Blob, filename } as T;
}

function parseFilename(disposition: string | undefined, fallback: string): string {
  if (!disposition) return fallback;
  const star = /filename\*=utf-8''([^;]+)/i.exec(disposition);
  if (star) return decodeURIComponent(star[1]!);
  const plain = /filename="?([^";]+)"?/i.exec(disposition);
  return plain ? plain[1]! : fallback;
}

/** Submit a multipart task, poll its progress, then download its result. */
export async function postFiles(
  endpoint: string,
  form: FormData,
  options: JobOptions = {},
): Promise<FileResult> {
  return submitMultipartJob<FileResult>(endpoint, form, "blob", options);
}

/** Submit a multipart task and return its JSON result (e.g. editor open). */
export async function postJson<T>(endpoint: string, form: FormData, options: JobOptions = {}): Promise<T> {
  return submitMultipartJob<T>(endpoint, form, "json", options);
}

/** POST a JSON body and return parsed JSON. */
export async function postBody<T>(endpoint: string, body: unknown): Promise<T> {
  const resp = await client.post<T>(endpoint, body);
  return resp.data;
}

/** Submit a JSON-backed editor mutation and return its completed job result. */
export async function postJobBody<T>(endpoint: string, body: unknown, options: JobOptions = {}): Promise<T> {
  const { data: accepted } = await client.post<AcceptedJob>(endpoint, body, {
    headers: {
      "Idempotency-Key": makeIdempotencyKey(),
      ...(options.historyPolicy ? { "History-Policy": options.historyPolicy } : {}),
    },
  });
  const result = await pollJob<T>(accepted, options);
  return result.result as T;
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/** Extract a human-readable message from an axios error (JSON or blob body). */
export async function extractError(err: unknown): Promise<string> {
  if (axios.isAxiosError(err)) {
    const status = err.response?.status;
    if (!err.response) {
      if (err.code === "ERR_CANCELED") return "请求已取消。";
      if (err.code === "ECONNABORTED" || err.code === "ETIMEDOUT") {
        return "本地服务响应超时，请稍后重试。";
      }
      return "无法连接到本地服务，请确认 PDF Editor 服务正在运行。";
    }

    const data = err.response?.data;
    let message: string | undefined;
    let code: string | undefined;
    if (data instanceof Blob) {
      try {
        const json = JSON.parse(await data.text()) as { error?: unknown; code?: unknown };
        if (typeof json.error === "string") message = json.error;
        if (typeof json.code === "string") code = json.code;
      } catch {
        // Fall through to the status-specific message below.
      }
    } else if (typeof data === "object" && data !== null) {
      const body = data as { error?: unknown; code?: unknown };
      if (typeof body.error === "string") message = body.error;
      if (typeof body.code === "string") code = body.code;
    }

    if (status !== undefined && status >= 500 && (!message || !code || code === "INTERNAL_ERROR")) {
      return `服务器发生未知错误（HTTP ${status}），请稍后重试。`;
    }
    if (message) return message;
    if (status !== undefined && status >= 500) {
      return `服务器发生未知错误（HTTP ${status}），请稍后重试。`;
    }
    return `请求失败 (${status ?? "?"})`;
  }
  if (err instanceof Error) return err.message;
  return String(err);
}

export function getErrorCode(err: unknown): string | undefined {
  if (err instanceof ApiJobError) return err.code;
  if (axios.isAxiosError(err)) {
    const data = err.response?.data as { code?: string } | undefined;
    return data?.code;
  }
  return undefined;
}

export default client;
