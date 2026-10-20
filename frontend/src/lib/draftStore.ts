export interface DraftAnchor {
  lineId: string;
  page: number;
  originalHash: string;
  bbox: number[];
  original: string;
  text: string;
}

export interface CachedDraftSummary {
  id: string;
  name: string;
  pageCount: number;
  sizeBytes: number;
  createdAt: number;
  updatedAt: number;
  expiresAt: number;
  editCount: number;
}

export interface CachedDraft extends CachedDraftSummary {
  blob: Blob;
  edits: DraftAnchor[];
}

const DB_NAME = "pdf-editor-drafts";
const DB_VERSION = 1;
const STORE_NAME = "documents";
export const DRAFT_TTL_MS = 7 * 24 * 60 * 60 * 1000;

function openDatabase(): Promise<IDBDatabase> {
  if (typeof indexedDB === "undefined") {
    return Promise.reject(new Error("当前浏览器不支持 IndexedDB"));
  }
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        const store = db.createObjectStore(STORE_NAME, { keyPath: "id" });
        store.createIndex("expiresAt", "expiresAt", { unique: false });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("无法打开本地草稿缓存"));
    request.onblocked = () => reject(new Error("本地草稿数据库正被其他页面占用"));
  });
}

function runTransaction<T>(
  mode: IDBTransactionMode,
  run: (store: IDBObjectStore, resolve: (value: T) => void, reject: (reason: unknown) => void) => void,
): Promise<T> {
  return openDatabase().then((db) => new Promise<T>((resolve, reject) => {
    const transaction = db.transaction(STORE_NAME, mode);
    const store = transaction.objectStore(STORE_NAME);
    let result: T;
    let settled = false;
    const finish = (value: T) => {
      result = value;
      settled = true;
    };
    run(store, finish, reject);
    transaction.oncomplete = () => {
      db.close();
      resolve(settled ? result! : (undefined as T));
    };
    transaction.onerror = () => {
      db.close();
      reject(transaction.error ?? new Error("本地草稿缓存操作失败"));
    };
    transaction.onabort = () => {
      db.close();
      reject(transaction.error ?? new Error("本地草稿缓存操作已中止"));
    };
  }));
}

function toSummary(draft: CachedDraft): CachedDraftSummary {
  return {
    id: draft.id,
    name: draft.name,
    pageCount: draft.pageCount,
    sizeBytes: draft.sizeBytes,
    createdAt: draft.createdAt,
    updatedAt: draft.updatedAt,
    expiresAt: draft.expiresAt,
    editCount: draft.edits.length,
  };
}

export function createDraftId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `draft-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export async function saveDraft(draft: CachedDraft): Promise<void> {
  await runTransaction<void>("readwrite", (store, resolve, reject) => {
    const request = store.put(draft);
    request.onsuccess = () => resolve(undefined);
    request.onerror = () => reject(request.error ?? new Error("本地草稿缓存写入失败"));
  });
}

export async function getDraft(id: string): Promise<CachedDraft | undefined> {
  return runTransaction<CachedDraft | undefined>("readonly", (store, resolve, reject) => {
    const request = store.get(id);
    request.onsuccess = () => resolve(request.result as CachedDraft | undefined);
    request.onerror = () => reject(request.error ?? new Error("无法读取本地草稿"));
  });
}

export async function deleteDraft(id: string): Promise<void> {
  await runTransaction<void>("readwrite", (store, resolve, reject) => {
    const request = store.delete(id);
    request.onsuccess = () => resolve(undefined);
    request.onerror = () => reject(request.error ?? new Error("无法清除本地草稿"));
  });
}

export async function listDrafts(now = Date.now()): Promise<CachedDraftSummary[]> {
  const records = await runTransaction<CachedDraft[]>("readonly", (store, resolve, reject) => {
    const request = store.getAll();
    request.onsuccess = () => resolve(request.result as CachedDraft[]);
    request.onerror = () => reject(request.error ?? new Error("无法列出本地草稿"));
  });
  const expired = records.filter((record) => record.expiresAt <= now);
  if (expired.length) {
    await runTransaction<void>("readwrite", (store, resolve) => {
      for (const record of expired) store.delete(record.id);
      resolve(undefined);
    });
  }
  return records.filter((record) => record.expiresAt > now).map(toSummary);
}

export async function clearAllDrafts(): Promise<void> {
  await runTransaction<void>("readwrite", (store, resolve, reject) => {
    const request = store.clear();
    request.onsuccess = () => resolve(undefined);
    request.onerror = () => reject(request.error ?? new Error("无法清除本地草稿"));
  });
}
