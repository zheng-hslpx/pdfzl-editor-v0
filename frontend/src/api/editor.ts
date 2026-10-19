import client, { postBody, postJson } from "./client";
import { downloadBlob } from "./client";

export interface PageMeta {
  page: number;
  width: number;
  height: number;
  rotation: number;
}

export interface TextLine {
  id: string;
  bbox: number[];
  fontSize: number;
  font: string;
  color: string;
  text: string;
}

export interface PageModel extends PageMeta {
  lines: TextLine[];
}

export interface OpenResult {
  docId: string;
  originalName: string;
  pageCount: number;
  pages: PageMeta[];
  textModel: PageModel[];
}

export interface EditorResult {
  ok: boolean;
  pageCount: number;
  pages: PageMeta[];
  textModel: PageModel[];
}

export interface EditPayload {
  page: number;
  bbox: number[];
  original: string;
  text: string;
  fontSize?: number;
}

export async function openEditor(file: File): Promise<OpenResult> {
  const form = new FormData();
  form.append("fileInput", file, file.name);
  return postJson<OpenResult>("/edit/open", form);
}

export async function applyEdits(
  docId: string,
  edits: EditPayload[],
): Promise<EditorResult> {
  return postBody<EditorResult>(`/edit/${docId}/apply`, { edits });
}

export async function addText(
  docId: string,
  params: { page: number; x: number; y: number; text: string; fontSize: number; color: string },
): Promise<EditorResult> {
  return postBody<EditorResult>(`/edit/${docId}/text/add`, params);
}

export async function addImage(
  docId: string,
  params: { image: File; page: number; x: number; y: number; w: number; h: number },
): Promise<EditorResult> {
  const form = new FormData();
  form.append("image", params.image, params.image.name);
  form.append("page", String(params.page));
  form.append("x", String(params.x));
  form.append("y", String(params.y));
  form.append("w", String(params.w));
  form.append("h", String(params.h));
  return postJson<EditorResult>(`/edit/${docId}/image/add`, form);
}

export async function rotatePage(
  docId: string,
  page: number,
  angle = 90,
): Promise<EditorResult> {
  return postBody<EditorResult>(`/edit/${docId}/pages/rotate`, { page, angle });
}

export async function deletePages(
  docId: string,
  pages: number[],
): Promise<EditorResult> {
  return postBody<EditorResult>(`/edit/${docId}/pages/delete`, { pages });
}

export async function movePage(
  docId: string,
  from: number,
  to: number,
): Promise<EditorResult> {
  return postBody<EditorResult>(`/edit/${docId}/pages/move`, { from, to });
}

export async function closeSession(docId: string): Promise<void> {
  await postBody(`/edit/${docId}/close`, {});
}

export async function saveEditedAs(docId: string, filename: string): Promise<void> {
  const resp = await client.get(`/edit/${docId}/download`, {
    params: { filename },
    responseType: "blob",
  });
  downloadBlob(resp.data as Blob, filename);
}

/** Fetch current session bytes (used to re-render canvases after edits). */
export async function fetchSessionBytes(docId: string): Promise<Uint8Array> {
  const resp = await client.get(`/edit/${docId}/download`, { responseType: "blob" });
  return new Uint8Array(await (resp.data as Blob).arrayBuffer());
}
