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

function parseFilename(disposition: string | undefined, fallback: string): string {
  if (!disposition) return fallback;
  const star = /filename\*=utf-8''([^;]+)/i.exec(disposition);
  if (star) return decodeURIComponent(star[1]!);
  const plain = /filename="?([^";]+)"?/i.exec(disposition);
  return plain ? plain[1]! : fallback;
}

/** POST a multipart form and return the file the server streamed back. */
export async function postFiles(endpoint: string, form: FormData): Promise<FileResult> {
  const resp = await client.post(endpoint, form, { responseType: "blob" });
  const filename = parseFilename(resp.headers["content-disposition"], "download.pdf");
  return { blob: resp.data as Blob, filename };
}

/** POST a multipart form and return parsed JSON (e.g. pdf-info). */
export async function postJson<T>(endpoint: string, form: FormData): Promise<T> {
  const resp = await client.post<T>(endpoint, form);
  return resp.data;
}

/** POST a JSON body and return parsed JSON. */
export async function postBody<T>(endpoint: string, body: unknown): Promise<T> {
  const resp = await client.post<T>(endpoint, body);
  return resp.data;
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
    const data = err.response?.data;
    if (data instanceof Blob) {
      try {
        const json = JSON.parse(await data.text()) as { error?: string };
        return json.error ?? `请求失败 (${err.response?.status ?? "?"})`;
      } catch {
        return `请求失败 (${err.response?.status ?? "?"})`;
      }
    }
    if (typeof data === "object" && data !== null && "error" in data) {
      return String((data as { error: unknown }).error);
    }
    return err.message;
  }
  return String(err);
}

export default client;
