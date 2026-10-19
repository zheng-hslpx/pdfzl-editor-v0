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

export default client;
