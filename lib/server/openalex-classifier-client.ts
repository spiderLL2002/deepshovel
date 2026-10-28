import { ApiError } from "./evidence-store.ts";
import { containsCredential, readBoundedJson } from "./service-response.ts";

const MAX_RESPONSE_BYTES = 256 * 1024;
const SHARED_SECRET_RE = /^[\x21-\x7e]{32,512}$/;

export class OpenAlexClassifierClient {
  private readonly baseUrl: string;
  private readonly sharedSecret: string;
  private readonly fetcher: typeof fetch;

  constructor(config: { baseUrl: string; sharedSecret: string; fetch?: typeof fetch }) {
    this.baseUrl = config.baseUrl.replace(/\/+$/u, "");
    if (!SHARED_SECRET_RE.test(config.sharedSecret)) {
      throw new ApiError(503, "COMPILER_NOT_CONFIGURED", "Compiler shared secret is invalid");
    }
    this.sharedSecret = config.sharedSecret;
    this.fetcher = config.fetch ?? fetch;
  }

  async classify(
    input: { title: string; abstract?: string; doi?: string },
    signal?: AbortSignal,
  ): Promise<unknown> {
    const timeout = AbortSignal.timeout(10 * 60 * 1000);
    const combined = signal ? AbortSignal.any([signal, timeout]) : timeout;
    let response: Response;
    try {
      response = await this.fetcher(`${this.baseUrl}/v1/openalex/classify`, {
        method: "POST",
        redirect: "manual",
        headers: {
          authorization: `Bearer ${this.sharedSecret}`,
          "content-type": "application/json",
        },
        body: JSON.stringify(input),
        signal: combined,
      });
    } catch (error) {
      throw new ApiError(503, "OPENALEX_CLASSIFIER_UNAVAILABLE", "OpenAlex 官方分类器当前不可用", { cause: error });
    }
    if (response.status >= 300 && response.status < 400) {
      throw new ApiError(502, "OPENALEX_CLASSIFIER_REDIRECT", "OpenAlex 分类器返回了不允许的重定向");
    }
    const payload = await readBoundedJson(response, MAX_RESPONSE_BYTES, (kind, message) => new ApiError(
      502, kind === "too_large" ? "OPENALEX_CLASSIFIER_RESPONSE_TOO_LARGE" : "OPENALEX_CLASSIFIER_INVALID_RESPONSE", message,
    ));
    if (containsCredential(payload, this.sharedSecret)) {
      throw new ApiError(502, "OPENALEX_CLASSIFIER_INVALID_RESPONSE", "Classifier response contained forbidden credential material");
    }
    if (!response.ok) {
      throw new ApiError(response.status >= 500 ? 503 : 502, "OPENALEX_CLASSIFIER_FAILED", "OpenAlex 官方分类失败");
    }
    return payload;
  }
}
