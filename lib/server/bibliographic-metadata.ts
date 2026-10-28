import {
  parseDocumentExtraction,
  type DocumentExtraction,
} from "../document-extraction/index.ts";

const DOI_RE = /^10\.\d{4,9}\/[!-~]+$/u;
const REDIRECT_STATUSES = new Set([301, 302, 303, 307, 308]);
const RETRYABLE_STATUSES = new Set([408, 429]);
const DOI_AUTHORITY_HOSTS = new Set([
  "api.crossref.org",
  "api.datacite.org",
  "data.crosscite.org",
  "data.crossref.org",
  "data.datacite.org",
  "doi.org",
  "dx.doi.org",
]);
const REGISTRY_FIELDS = [
  "author",
  "journal",
  "issn",
  "volume",
  "issue",
  "page",
  "article_number",
  "doi",
  "link",
  "publisher",
  "year",
] as const;
const MAX_RESPONSE_BYTES = 512 * 1024;
const MAX_REDIRECTS = 3;
const MAX_ATTEMPTS = 2;
const REQUEST_TIMEOUT_MS = 4_000;
const MAX_RETRY_DELAY_MS = 1_500;
const CROSSREF_ROWS = "3";
const CROSSREF_SELECT = [
  "DOI",
  "title",
  "author",
  "container-title",
  "publisher",
  "volume",
  "issue",
  "page",
  "article-number",
  "ISSN",
  "issued",
].join(",");

export type BibliographicMetadataStatus = "matched" | "not-found" | "not-applicable" | "deferred";

export type BibliographicMetadataResult = {
  extraction: DocumentExtraction;
  status: BibliographicMetadataStatus;
};

export type BibliographicMetadataOptions = {
  fetch?: typeof fetch;
  sleep?: (milliseconds: number) => Promise<void>;
  timeoutMs?: number;
};

type FetchResult =
  | { kind: "record"; value: unknown }
  | { kind: "not-found" }
  | { kind: "deferred" };

type RegistryRecord = Record<(typeof REGISTRY_FIELDS)[number], string> & {
  registeredTitle: string;
  pageIsArticleNumber: boolean;
};

function cleanDoi(value: string) {
  let candidate = value.normalize("NFKC").trim();
  candidate = candidate.replace(/^doi\s*:\s*/iu, "");
  candidate = candidate.replace(/^https?:\/\/(?:dx\.)?doi\.org\//iu, "");
  try {
    candidate = decodeURIComponent(candidate);
  } catch {
    return "";
  }
  candidate = candidate.replace(/[.,;:]+$/u, "").trim().toLowerCase();
  if (candidate.length > 300 || !DOI_RE.test(candidate) || /[\s<>"'`\\]/u.test(candidate)) return "";
  return candidate;
}

function cleanText(value: unknown, maximum = 10_000) {
  if (typeof value !== "string") return "";
  const normalized = value
    .normalize("NFKC")
    .replace(/<[^>]*>/gu, " ")
    .replace(/&nbsp;/giu, " ")
    .replace(/&amp;/giu, "&")
    .replace(/&lt;/giu, "<")
    .replace(/&gt;/giu, ">")
    .replace(/\s+/gu, " ")
    .trim();
  return Array.from(normalized).length <= maximum ? normalized : "";
}

function firstText(value: unknown, maximum = 10_000) {
  if (Array.isArray(value)) {
    for (const item of value.slice(0, 32)) {
      const text = cleanText(item, maximum);
      if (text) return text;
    }
    return "";
  }
  return cleanText(value, maximum);
}

function normalizeTitle(value: string) {
  return value
    .normalize("NFKC")
    .toLocaleLowerCase("en")
    .replace(/<[^>]*>/gu, " ")
    .replace(/&(?:amp|lt|gt|nbsp);/giu, " ")
    .replace(/[\p{P}\p{S}]+/gu, " ")
    .replace(/\s+/gu, " ")
    .trim();
}

function ngrams(value: string, size: number) {
  const compact = value.replace(/\s+/gu, " ");
  const result = new Set<string>();
  if (compact.length < size) {
    if (compact) result.add(compact);
    return result;
  }
  for (let index = 0; index <= compact.length - size; index += 1) {
    result.add(compact.slice(index, index + size));
  }
  return result;
}

function diceSimilarity(left: string, right: string) {
  const leftParts = ngrams(left, 3);
  const rightParts = ngrams(right, 3);
  if (!leftParts.size || !rightParts.size) return 0;
  let overlap = 0;
  for (const part of leftParts) if (rightParts.has(part)) overlap += 1;
  return (2 * overlap) / (leftParts.size + rightParts.size);
}

function titleSimilarity(localTitle: string, registeredTitle: string) {
  const local = normalizeTitle(localTitle);
  const registered = normalizeTitle(registeredTitle);
  if (local.length < 12 || registered.length < 12) return 0;
  if (local === registered) return 1;
  const shorter = local.length <= registered.length ? local : registered;
  const longer = local.length > registered.length ? local : registered;
  if (shorter.length >= 40 && longer.includes(shorter)) return 0.95;
  return diceSimilarity(local, registered);
}

function titlesMatch(localTitle: string, registeredTitle: string) {
  return titleSimilarity(localTitle, registeredTitle) >= 0.8;
}

function isAllowedAuthorityUrl(value: URL) {
  return value.protocol === "https:"
    && value.port === ""
    && !value.username
    && !value.password
    && DOI_AUTHORITY_HOSTS.has(value.hostname.toLowerCase());
}

async function cancelBody(response: Response) {
  try {
    await response.body?.cancel();
  } catch {
    // The body may already be closed by the runtime.
  }
}

async function boundedJson(response: Response) {
  const declared = response.headers.get("content-length");
  if (declared && /^\d+$/u.test(declared) && Number(declared) > MAX_RESPONSE_BYTES) {
    await cancelBody(response);
    throw new TypeError("DOI registry response is too large");
  }
  if (!response.body) throw new TypeError("DOI registry response has no body");

  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > MAX_RESPONSE_BYTES) {
        await reader.cancel();
        throw new TypeError("DOI registry response is too large");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }

  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) as unknown;
}

function retryDelay(response: Response | undefined, attempt: number) {
  const value = response?.headers.get("retry-after")?.trim();
  if (value && /^\d+(?:\.\d+)?$/u.test(value)) {
    return Math.min(MAX_RETRY_DELAY_MS, Math.max(0, Number(value) * 1_000));
  }
  if (value) {
    const timestamp = Date.parse(value);
    if (Number.isFinite(timestamp)) return Math.min(MAX_RETRY_DELAY_MS, Math.max(0, timestamp - Date.now()));
  }
  return Math.min(MAX_RETRY_DELAY_MS, 150 * (2 ** attempt));
}

async function requestOnce(
  fetcher: typeof fetch,
  url: URL,
  timeoutMs: number,
  accept: string,
): Promise<{ response?: Response; transient: boolean }> {
  try {
    return {
      response: await fetcher(url, {
        method: "GET",
        redirect: "manual",
        referrerPolicy: "no-referrer",
        headers: {
          accept,
          "user-agent": "DeepShovel/1.0.0 (https://deepshovel.iiot-shxt.xyz/)",
        },
        signal: AbortSignal.timeout(timeoutMs),
      }),
      transient: false,
    };
  } catch {
    return { transient: true };
  }
}

async function fetchAuthorityJson(
  initialUrl: URL,
  options: BibliographicMetadataOptions,
  accept = "application/vnd.citationstyles.csl+json",
): Promise<FetchResult> {
  const fetcher = options.fetch ?? fetch;
  const sleep = options.sleep ?? ((milliseconds: number) => new Promise((resolve) => setTimeout(resolve, milliseconds)));
  const timeoutMs = Number.isFinite(options.timeoutMs) && Number(options.timeoutMs) > 0
    ? Math.min(30_000, Number(options.timeoutMs))
    : REQUEST_TIMEOUT_MS;

  for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt += 1) {
    let url = new URL(initialUrl);
    let lastResponse: Response | undefined;
    let retry = false;

    for (let redirect = 0; redirect <= MAX_REDIRECTS; redirect += 1) {
      if (!isAllowedAuthorityUrl(url)) return { kind: "deferred" };
      const requested = await requestOnce(fetcher, url, timeoutMs, accept);
      if (requested.transient || !requested.response) {
        retry = true;
        break;
      }
      const response = requested.response;
      lastResponse = response;

      if (REDIRECT_STATUSES.has(response.status)) {
        const location = response.headers.get("location");
        await cancelBody(response);
        if (!location || redirect === MAX_REDIRECTS) return { kind: "deferred" };
        let next: URL;
        try {
          next = new URL(location, url);
        } catch {
          return { kind: "deferred" };
        }
        if (!isAllowedAuthorityUrl(next)) return { kind: "deferred" };
        url = next;
        continue;
      }

      if (RETRYABLE_STATUSES.has(response.status) || response.status >= 500) {
        retry = true;
        await cancelBody(response);
        break;
      }
      if (response.status === 401 || response.status === 403) {
        await cancelBody(response);
        return { kind: "deferred" };
      }
      if (response.status === 204 || (response.status >= 400 && response.status < 500)) {
        await cancelBody(response);
        return { kind: "not-found" };
      }
      if (response.status !== 200) {
        await cancelBody(response);
        return { kind: "deferred" };
      }

      const contentType = response.headers.get("content-type")?.split(";", 1)[0]?.trim().toLowerCase() ?? "";
      if (contentType !== "application/vnd.citationstyles.csl+json" && contentType !== "application/json") {
        await cancelBody(response);
        return { kind: "deferred" };
      }
      try {
        return { kind: "record", value: await boundedJson(response) };
      } catch {
        return { kind: "deferred" };
      }
    }

    if (!retry || attempt + 1 === MAX_ATTEMPTS) return { kind: "deferred" };
    await sleep(retryDelay(lastResponse, attempt));
  }
  return { kind: "deferred" };
}

async function fetchRegistryRecord(doi: string, options: BibliographicMetadataOptions) {
  return fetchAuthorityJson(new URL(`https://doi.org/${encodeURIComponent(doi)}`), options);
}

async function fetchCrossrefCandidates(title: string, options: BibliographicMetadataOptions) {
  const url = new URL("https://api.crossref.org/works");
  url.searchParams.set("query.title", Array.from(title).slice(0, 512).join(""));
  url.searchParams.set("rows", CROSSREF_ROWS);
  url.searchParams.set("select", CROSSREF_SELECT);
  return fetchAuthorityJson(url, options, "application/json");
}

function recordValue(value: unknown) {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function authorsValue(value: unknown) {
  if (!Array.isArray(value) || value.length > 512) return "";
  const authors: string[] = [];
  for (const raw of value.slice(0, 256)) {
    const author = recordValue(raw);
    if (!author) continue;
    const literal = cleanText(author.literal, 500);
    const given = cleanText(author.given, 250);
    const family = cleanText(author.family, 250);
    const name = literal || [given, family].filter(Boolean).join(" ");
    if (name) authors.push(name);
  }
  const joined = authors.join("; ");
  return Array.from(joined).length <= 10_000 ? joined : "";
}

function issnValue(value: unknown) {
  const candidates = (Array.isArray(value) ? value : [value]).slice(0, 16);
  const result: string[] = [];
  for (const raw of candidates) {
    const candidate = cleanText(raw, 32).toUpperCase();
    if (/^\d{4}-[\dX]{4}$/u.test(candidate) && !result.includes(candidate)) result.push(candidate);
  }
  return result.join("; ");
}

function issuedYear(value: unknown) {
  const object = recordValue(value);
  const dateParts = object?.["date-parts"];
  if (!Array.isArray(dateParts) || !Array.isArray(dateParts[0])) return "";
  const year = dateParts[0][0];
  return Number.isSafeInteger(year) && Number(year) >= 1000 && Number(year) <= 3000 ? String(year) : "";
}

function parseRegistryRecord(value: unknown, expectedDoi: string): RegistryRecord | null {
  const item = recordValue(value);
  if (!item || Object.keys(item).length > 512) return null;
  const returnedDoi = cleanDoi(firstText(item.DOI ?? item.doi, 300));
  const registeredTitle = firstText(item.title, 10_000);
  if (returnedDoi !== expectedDoi || !registeredTitle) return null;

  const articleNumber = firstText(item["article-number"], 1_000);
  let page = firstText(item.page, 1_000);
  const pageIsArticleNumber = Boolean(page && articleNumber && page === articleNumber);
  if (pageIsArticleNumber) page = "";
  const year = ["issued", "published", "published-print", "published-online", "created"]
    .map((key) => issuedYear(item[key]))
    .find(Boolean) ?? "";

  return {
    registeredTitle,
    pageIsArticleNumber,
    author: authorsValue(item.author),
    journal: firstText(item["container-title"], 10_000),
    issn: issnValue(item.ISSN ?? item.issn),
    volume: firstText(item.volume, 1_000),
    issue: firstText(item.issue, 1_000),
    page,
    article_number: articleNumber,
    doi: expectedDoi,
    link: `https://doi.org/${expectedDoi}`,
    publisher: firstText(item.publisher, 10_000),
    year,
  };
}

function metadataYear(value: string) {
  const normalized = cleanText(value, 32);
  if (!/^\d{4}$/u.test(normalized)) return null;
  const year = Number(normalized);
  return year >= 1000 && year <= 3000 ? year : null;
}

function normalizedSurname(value: string) {
  const normalized = value
    .normalize("NFKD")
    .replace(/\p{M}+/gu, "")
    .toLocaleLowerCase("en")
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();
  return normalized.split(/\s+/u).filter(Boolean).at(-1) ?? "";
}

function localFirstAuthorSurname(value: string) {
  const author = cleanText(value, 10_000);
  if (!author) return "";
  const first = author.split(/[;；]|\s+(?:and|&)\s+/iu, 1)[0]?.trim() ?? "";
  if (!first) return "";
  const familyFirst = first.includes(",") ? first.split(",", 1)[0] ?? "" : first;
  return normalizedSurname(familyFirst);
}

function candidateFirstAuthorSurname(value: unknown) {
  if (!Array.isArray(value) || !value.length) return "";
  const author = recordValue(value[0]);
  if (!author) return "";
  return normalizedSurname(
    cleanText(author.family, 250)
      || cleanText(author.literal, 500)
      || cleanText(author.name, 500),
  );
}

function crossrefItems(value: unknown) {
  const envelope = recordValue(value);
  if (!envelope || envelope.status !== "ok" || envelope["message-type"] !== "work-list") return null;
  const message = recordValue(envelope.message);
  if (!message || !Array.isArray(message.items) || message.items.length > Number(CROSSREF_ROWS)) return null;
  return message.items as unknown[];
}

function applyRegistryRecord(extraction: DocumentExtraction, registered: RegistryRecord): DocumentExtraction {
  const metadata: Record<string, string> = { ...extraction.metadata };
  const sources: Record<string, string> = { ...extraction.field_sources };
  for (const field of REGISTRY_FIELDS) {
    const value = registered[field];
    if (value) metadata[field] = value;
    if (value || !metadata[field]) sources[field] = "doi_registry";
  }
  if (registered.pageIsArticleNumber) {
    metadata.page = "";
    sources.page = "doi_registry";
  }
  return parseDocumentExtraction({
    ...extraction,
    metadata,
    field_sources: sources,
  });
}

async function enrichFromCrossrefTitle(
  extraction: DocumentExtraction,
  options: BibliographicMetadataOptions,
): Promise<BibliographicMetadataResult> {
  const title = cleanText(extraction.metadata.title, 10_000);
  if (normalizeTitle(title).length < 12) return { extraction, status: "not-applicable" };

  const localYear = metadataYear(extraction.metadata.year);
  if (localYear === null) return { extraction, status: "not-found" };
  const hasLocalAuthor = Boolean(cleanText(extraction.metadata.author, 10_000));
  const localSurname = localFirstAuthorSurname(extraction.metadata.author);
  if (hasLocalAuthor && !localSurname) return { extraction, status: "not-found" };

  const fetched = await fetchCrossrefCandidates(title, options);
  if (fetched.kind !== "record") return { extraction, status: fetched.kind };
  const items = crossrefItems(fetched.value);
  if (!items) return { extraction, status: "deferred" };

  const matches = new Map<string, RegistryRecord>();
  for (const raw of items) {
    const candidate = recordValue(raw);
    if (!candidate) continue;
    const candidateDoi = cleanDoi(firstText(candidate.DOI ?? candidate.doi, 300));
    if (!candidateDoi) continue;
    const registered = parseRegistryRecord(candidate, candidateDoi);
    if (!registered || titleSimilarity(title, registered.registeredTitle) < 0.9) continue;
    const candidateYear = metadataYear(registered.year);
    if (candidateYear === null || Math.abs(candidateYear - localYear) > 1) continue;
    if (localSurname && candidateFirstAuthorSurname(candidate.author) !== localSurname) continue;
    matches.set(candidateDoi, registered);
  }

  if (matches.size !== 1) return { extraction, status: "not-found" };
  const registered = matches.values().next().value;
  if (!registered) return { extraction, status: "not-found" };
  return { extraction: applyRegistryRecord(extraction, registered), status: "matched" };
}

export async function enrichBibliographicMetadata(
  extraction: DocumentExtraction,
  options: BibliographicMetadataOptions = {},
): Promise<BibliographicMetadataResult> {
  const doi = cleanDoi(extraction.metadata.doi);
  if (doi) {
    const fetched = await fetchRegistryRecord(doi, options);
    if (fetched.kind === "deferred") return { extraction, status: "deferred" };
    if (fetched.kind === "record") {
      const registered = parseRegistryRecord(fetched.value, doi);
      if (registered && titlesMatch(extraction.metadata.title, registered.registeredTitle)) {
        return { extraction: applyRegistryRecord(extraction, registered), status: "matched" };
      }
    }
  }
  return enrichFromCrossrefTitle(extraction, options);
}
