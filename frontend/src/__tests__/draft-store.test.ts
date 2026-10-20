import { afterEach, describe, expect, it, vi } from "vitest";
import {
  clearAllDrafts,
  deleteDraft,
  getDraft,
  listDrafts,
  saveDraft,
  type CachedDraft,
} from "../lib/draftStore";

function makeIndexedDb() {
  const records = new Map<string, CachedDraft>();
  let upgraded = false;
  let failQuota = false;
  const db: Record<string, unknown> = {
    objectStoreNames: { contains: (name: string) => name === "documents" && upgraded },
    createObjectStore: () => {
      upgraded = true;
      return { createIndex: () => undefined };
    },
    close: () => undefined,
    transaction: () => {
      const transaction: Record<string, unknown> = { oncomplete: null, onerror: null, onabort: null };
      let pending = 0;
      const request = <T,>(operation: () => T) => {
        const value: Record<string, unknown> = { result: undefined, error: null, onsuccess: null, onerror: null };
        pending += 1;
        queueMicrotask(() => {
          try {
            if (failQuota) throw new DOMException("quota", "QuotaExceededError");
            value.result = operation();
            (value.onsuccess as (() => void) | null)?.();
          } catch (error) {
            value.error = error;
            (value.onerror as (() => void) | null)?.();
            (transaction.onerror as (() => void) | null)?.();
          } finally {
            pending -= 1;
            if (pending === 0) setTimeout(() => (transaction.oncomplete as (() => void) | null)?.(), 0);
          }
        });
        return value;
      };
      const store: Record<string, unknown> = {
        transaction,
        put: (draft: CachedDraft) => request(() => {
          records.set(draft.id, draft);
          return draft.id;
        }),
        get: (id: string) => request(() => records.get(id)),
        getAll: () => request(() => [...records.values()]),
        delete: (id: string) => request(() => records.delete(id)),
        clear: () => request(() => records.clear()),
      };
      transaction.objectStore = () => store;
      return transaction;
    },
  };
  const factory = {
    open: () => {
      const openRequest: Record<string, unknown> = { result: undefined, onupgradeneeded: null, onsuccess: null, onerror: null, onblocked: null };
      setTimeout(() => {
        openRequest.result = db;
        if (!upgraded) (openRequest.onupgradeneeded as (() => void) | null)?.();
        (openRequest.onsuccess as (() => void) | null)?.();
      }, 0);
      return openRequest;
    },
  };
  return {
    factory: factory as unknown as IDBFactory,
    records,
    setQuotaFailure: (value: boolean) => { failQuota = value; },
  };
}

function sampleDraft(id: string, expiresAt: number): CachedDraft {
  const blob = new Blob(["pdf bytes"], { type: "application/pdf" });
  return {
    id,
    name: `${id}.pdf`,
    pageCount: 3,
    sizeBytes: blob.size,
    createdAt: 1,
    updatedAt: 2,
    expiresAt,
    editCount: 1,
    blob,
    edits: [{ lineId: "page:l0", page: 0, originalHash: "abc", bbox: [1, 2, 3, 4], original: "before", text: "after" }],
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("IndexedDB draft storage", () => {
  it("stores and lists opted-in documents, expires old records, and supports per-document and global clearing", async () => {
    const indexedDb = makeIndexedDb();
    vi.stubGlobal("indexedDB", indexedDb.factory);
    const now = 10_000_000;
    await saveDraft(sampleDraft("keep", now + 1000));
    await saveDraft(sampleDraft("expire", now - 1));

    expect(await listDrafts(now)).toEqual([expect.objectContaining({ id: "keep", editCount: 1 })]);
    expect(await getDraft("expire")).toBeUndefined();
    expect(await getDraft("keep")).toEqual(expect.objectContaining({ name: "keep.pdf" }));

    await deleteDraft("keep");
    expect(await listDrafts(now)).toEqual([]);
    await saveDraft(sampleDraft("all", now + 1000));
    await clearAllDrafts();
    expect(indexedDb.records.size).toBe(0);
  });

  it("surfaces storage quota errors so callers cannot report a successful cache", async () => {
    const indexedDb = makeIndexedDb();
    vi.stubGlobal("indexedDB", indexedDb.factory);
    indexedDb.setQuotaFailure(true);
    await expect(saveDraft(sampleDraft("quota", Date.now() + 1000))).rejects.toThrow("quota");
  });
});
