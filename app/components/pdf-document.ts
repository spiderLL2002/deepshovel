import type { PDFDocumentLoadingTask, PDFDocumentProxy } from "pdfjs-dist";

const MAX_CACHED_DOCUMENTS = 4;

type CachedDocument = {
  promise: Promise<PDFDocumentProxy>;
  activeLeases: number;
  destroy: () => Promise<void>;
};

type DocumentLease = {
  promise: Promise<PDFDocumentProxy>;
  release: () => void;
};

const documents = new Map<string, CachedDocument>();
let pdfModule: Promise<typeof import("pdfjs-dist/webpack.mjs")> | null = null;

export function loadPdfModule() {
  pdfModule ??= import("pdfjs-dist/webpack.mjs").catch((error) => {
    pdfModule = null;
    throw error;
  });
  return pdfModule;
}

function touchDocument(url: string, entry: CachedDocument) {
  if (documents.get(url) !== entry) return;
  documents.delete(url);
  documents.set(url, entry);
}

function destroyDocument(entry: CachedDocument) {
  void entry.destroy().catch(() => undefined);
}

function evictIdleDocuments() {
  while (documents.size > MAX_CACHED_DOCUMENTS) {
    let oldestIdle: [string, CachedDocument] | null = null;
    for (const candidate of documents) {
      if (candidate[1].activeLeases === 0) {
        oldestIdle = candidate;
        break;
      }
    }
    if (!oldestIdle) return;
    const [url, entry] = oldestIdle;
    documents.delete(url);
    destroyDocument(entry);
  }
}

/** Share concurrent loads, but never destroy a document while a page or crop is rendering. */
export function acquireDocument(url: string): DocumentLease {
  let entry = documents.get(url);
  if (entry) {
    touchDocument(url, entry);
  } else {
    let loadingTask: PDFDocumentLoadingTask | null = null;
    const pending = loadPdfModule().then((pdfjs) => {
      loadingTask = pdfjs.getDocument({
        url,
        cMapPacked: true,
        cMapUrl: "/pdfjs/cmaps/",
        enableXfa: false,
        standardFontDataUrl: "/pdfjs/standard_fonts/",
        stopAtErrors: false,
        useSystemFonts: true,
        wasmUrl: "/pdfjs/wasm/",
      });
      return loadingTask.promise;
    });
    entry = {
      promise: pending,
      activeLeases: 0,
      destroy: () => loadingTask?.destroy() ?? Promise.resolve(),
    };
    documents.set(url, entry);
    const created = entry;
    void pending.catch(() => {
      if (documents.get(url) === created) documents.delete(url);
      void created.destroy().catch(() => undefined);
    });
  }

  entry.activeLeases += 1;
  evictIdleDocuments();
  let released = false;
  return {
    promise: entry.promise,
    release: () => {
      if (released) return;
      released = true;
      entry.activeLeases -= 1;
      evictIdleDocuments();
    },
  };
}

