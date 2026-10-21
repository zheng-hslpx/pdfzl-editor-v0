import client, {
  ApiJobError,
  JobCancelledError,
  postBody,
  postJobBody,
  postJson,
  type HistoryCapacityPlan,
  type HistoryPolicy,
  type JobOptions,
} from "./client";
import { downloadBlob } from "./client";

export interface PageMeta {
  page: number;
  pageId: string;
  width: number;
  height: number;
  rotation: number;
}

export interface TextLine {
  id: string;
  pageId: string;
  originalHash: string;
  bbox: number[];
  fontSize: number;
  font: string;
  color: string;
  text: string;
}

export interface PageModel extends PageMeta {
  lines: TextLine[];
  /** 此页面文本模型的提取版本，仅供前端缓存校验。 */
  revision?: number;
}

export interface OpenResult {
  docId: string;
  originalName: string;
  pageCount: number;
  revision: number;
  pages: PageMeta[];
}

export interface EditorResult {
  pageCount: number;
  revision: number;
  pages: PageMeta[];
  pageIds?: string[];
  changedPageIds?: string[];
  removedPageIds?: string[];
}

export interface PageTextResult {
  docId: string;
  revision: number;
  page: PageModel;
}

export interface HistoryState {
  docId: string;
  revision: number;
  canUndo: boolean;
  canRedo: boolean;
  undoSteps: number;
  redoSteps: number;
  historyBytes: number;
  maxSteps: number;
  maxBytes: number;
}

export interface EditPayload {
  kind?: "text" | "cover";
  page: number;
  pageId?: string;
  lineId?: string;
  originalHash?: string;
  bbox: number[];
  original: string;
  text: string;
  fontSize?: number;
}

export async function openEditor(file: File, options: JobOptions = {}): Promise<OpenResult> {
  const form = new FormData();
  form.append("fileInput", file, file.name);
  return postJson<OpenResult>("/edit/open", form, options);
}

export async function fetchPages(
  docId: string,
  offset = 0,
  limit = 50,
): Promise<{ docId: string; revision: number; pageCount: number; pages: PageMeta[] }> {
  const { data } = await client.get(`/edit/${docId}/pages`, { params: { offset, limit } });
  return data;
}

export async function fetchPageText(docId: string, page: number): Promise<PageTextResult> {
  const { data } = await client.get<PageTextResult>(`/edit/${docId}/pages/${page}/text`);
  return data;
}

export async function fetchHistory(docId: string): Promise<HistoryState> {
  const { data } = await client.get<HistoryState>(`/edit/${docId}/history`);
  return data;
}

export async function fetchHistoryPlan(
  docId: string,
  kind: "snapshot" | "inverse" | "undo" | "redo",
): Promise<HistoryCapacityPlan> {
  const { data } = await client.get<HistoryCapacityPlan>(`/edit/${docId}/history/plan`, { params: { kind } });
  return data;
}

async function historyPolicyFor(
  docId: string,
  kind: "snapshot" | "inverse",
  options: JobOptions,
): Promise<HistoryPolicy> {
  const plan = await fetchHistoryPlan(docId, kind);
  if (!plan.needsConfirmation) return "snapshot";
  if (!options.onHistoryConfirm) {
    throw new ApiJobError(
      "HISTORY_CAPACITY_CONFIRMATION_REQUIRED",
      "撤销历史容量不足，需要先选择如何处理历史记录",
    );
  }
  const choice = await options.onHistoryConfirm(plan);
  if (choice === "cancel") throw new JobCancelledError();
  return choice;
}

async function submitSessionMutation<T>(
  docId: string,
  endpoint: string,
  payload: unknown,
  historyKind: "snapshot" | "inverse",
  options: JobOptions,
): Promise<T> {
  const historyPolicy = await historyPolicyFor(docId, historyKind, options);
  return postJobBody<T>(endpoint, payload, { ...options, historyPolicy });
}

export async function applyEdits(
  docId: string,
  expectedRevision: number,
  edits: EditPayload[],
  options: JobOptions = {},
): Promise<EditorResult> {
  return submitSessionMutation(
    docId,
    `/edit/${docId}/apply`,
    { expectedRevision, edits },
    "snapshot",
    options,
  );
}

export async function addText(
  docId: string,
  expectedRevision: number,
  params: { page: number; pageId: string; x: number; y: number; text: string; fontSize: number; color: string },
  options: JobOptions = {},
): Promise<EditorResult> {
  return submitSessionMutation(
    docId,
    `/edit/${docId}/text/add`,
    { ...params, expectedRevision },
    "snapshot",
    options,
  );
}

export async function addImage(
  docId: string,
  expectedRevision: number,
  params: { image: File; page: number; pageId: string; x: number; y: number; w: number; h: number },
  options: JobOptions = {},
): Promise<EditorResult> {
  const form = new FormData();
  form.append("image", params.image, params.image.name);
  form.append("page", String(params.page));
  form.append("x", String(params.x));
  form.append("y", String(params.y));
  form.append("w", String(params.w));
  form.append("h", String(params.h));
  form.append("expectedRevision", String(expectedRevision));
  form.append("pageId", params.pageId);
  const historyPolicy = await historyPolicyFor(docId, "snapshot", options);
  return postJson<EditorResult>(`/edit/${docId}/image/add`, form, { ...options, historyPolicy });
}

export async function rotatePage(
  docId: string,
  expectedRevision: number,
  page: number,
  angle = 90,
  options: JobOptions = {},
): Promise<EditorResult> {
  return submitSessionMutation(
    docId,
    `/edit/${docId}/pages/rotate`,
    { expectedRevision, page, angle },
    "inverse",
    options,
  );
}

export async function deletePages(
  docId: string,
  expectedRevision: number,
  pages: number[],
  options: JobOptions = {},
): Promise<EditorResult> {
  return submitSessionMutation(
    docId,
    `/edit/${docId}/pages/delete`,
    { expectedRevision, pages },
    "snapshot",
    options,
  );
}

export async function movePage(
  docId: string,
  expectedRevision: number,
  from: number,
  to: number,
  options: JobOptions = {},
): Promise<EditorResult> {
  return submitSessionMutation(
    docId,
    `/edit/${docId}/pages/move`,
    { expectedRevision, from, to },
    "inverse",
    options,
  );
}

async function runHistoryAction(
  docId: string,
  expectedRevision: number,
  action: "undo" | "redo",
  options: JobOptions = {},
): Promise<EditorResult> {
  const endpoint = `/edit/${docId}/history/${action}`;
  const plan = await fetchHistoryPlan(docId, action);
  let historyPolicy: HistoryPolicy | undefined;
  if (plan.needsConfirmation) {
    if (!options.onHistoryConfirm) {
      throw new ApiJobError(
        "HISTORY_CAPACITY_CONFIRMATION_REQUIRED",
        "撤销/重做将超过历史容量，请先选择如何处理历史记录",
      );
    }
    const choice = await options.onHistoryConfirm(plan);
    if (choice === "cancel") throw new JobCancelledError();
    historyPolicy = choice;
  }
  return postJobBody<EditorResult>(endpoint, { expectedRevision }, { ...options, historyPolicy });
}

export async function undo(docId: string, expectedRevision: number, options: JobOptions = {}): Promise<EditorResult> {
  return runHistoryAction(docId, expectedRevision, "undo", options);
}

export async function redo(docId: string, expectedRevision: number, options: JobOptions = {}): Promise<EditorResult> {
  return runHistoryAction(docId, expectedRevision, "redo", options);
}

export async function closeSession(docId: string): Promise<void> {
  await postBody(`/edit/${docId}/close`, {});
}

export async function touchSession(docId: string): Promise<void> {
  await postBody(`/edit/${docId}/touch`, {});
}

export async function fetchSessionPdf(docId: string): Promise<Blob> {
  const { data } = await client.get<Blob>(`/edit/${docId}/download`, {
    responseType: "blob",
    timeout: 0,
  });
  return data;
}

export async function saveEditedAs(docId: string, filename: string): Promise<void> {
  const resp = await client.get(`/edit/${docId}/download`, {
    params: { filename },
    responseType: "blob",
  });
  downloadBlob(resp.data as Blob, filename);
}
