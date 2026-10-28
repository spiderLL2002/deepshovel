/**
 * POST to a route whose answer can take longer than the proxy's idle timeout.
 * The route streams heartbeat lines and then one result line (see
 * lib/server/streamed-response.ts); this returns that result as an ordinary
 * JSON Response, so callers keep their existing status and body handling.
 */
const STREAMED_RESPONSE_MEDIA_TYPE = "application/x-ndjson";

export const STREAM_INTERRUPTED_MESSAGE = "与服务器的连接中断，未收到结果，请重试。";
export const STREAM_INVALID_MESSAGE = "服务器返回的流式响应格式异常，请重试。";

function resultLine(line: string): Response | null {
  if (!line.trim()) return null;
  let message;
  try {
    message = JSON.parse(line);
  } catch {
    throw new Error(STREAM_INVALID_MESSAGE);
  }
  if (!message || typeof message !== "object" || Array.isArray(message)) throw new Error(STREAM_INVALID_MESSAGE);
  if (message.type === "started" || message.type === "heartbeat") return null;
  if (message.type !== "result" || !Number.isInteger(message.status)
    || message.status < 200 || message.status > 599
    || !Object.hasOwn(message, "body")
    || (message.retryAfter !== null && typeof message.retryAfter !== "string")) {
    throw new Error(STREAM_INVALID_MESSAGE);
  }
  const headers = new Headers({ "content-type": "application/json" });
  if (message.retryAfter !== null) headers.set("retry-after", message.retryAfter);
  return new Response([204, 205, 304].includes(message.status) ? null : JSON.stringify(message.body), {
    status: message.status, headers,
  });
}

export async function fetchStreamed(input: string, init: RequestInit): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("accept", STREAMED_RESPONSE_MEDIA_TYPE);
  let response: Response;
  try {
    response = await fetch(input, { ...init, headers });
  } catch (error) {
    init.signal?.throwIfAborted();
    // Fetch rejects with TypeError when the connection fails before headers.
    // Do not replay the POST: the server may already be processing it.
    if (error instanceof TypeError) throw new Error(STREAM_INTERRUPTED_MESSAGE);
    throw error;
  }
  const mediaType = response.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase();
  // Failures before the stream starts (e.g. at the proxy) arrive as they are.
  if (mediaType !== STREAMED_RESPONSE_MEDIA_TYPE || !response.body) return response;
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffered = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      init.signal?.throwIfAborted();
      if (value) buffered += value;
      let newline = buffered.indexOf("\n");
      while (newline >= 0) {
        const line = buffered.slice(0, newline).trim();
        buffered = buffered.slice(newline + 1);
        newline = buffered.indexOf("\n");
        const result = resultLine(line);
        if (result) return result;
      }
      if (done) {
        const result = resultLine(buffered);
        if (result) return result;
        break;
      }
    }
  } catch (error) {
    init.signal?.throwIfAborted();
    if (error instanceof Error && error.message === STREAM_INVALID_MESSAGE) throw error;
    throw new Error(STREAM_INTERRUPTED_MESSAGE);
  } finally {
    reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
  throw new Error(STREAM_INTERRUPTED_MESSAGE);
}
