import { safeError } from "@/lib/server/evidence-store";

/**
 * Long interactive answers can exceed the proxy's idle timeout (HTTP 524).
 * A client that accepts
 * `application/x-ndjson` gets the response at once, a heartbeat line every
 * 15 s while the answer is prepared, and then one `result` line carrying the
 * status and JSON body that the plain route would have returned.
 */
export const STREAMED_RESPONSE_MEDIA_TYPE = "application/x-ndjson";
export const STREAMED_RESPONSE_HEARTBEAT_MS = 15_000;

export type StreamedResponseLine =
  | { type: "started" }
  | { type: "heartbeat" }
  | { type: "result"; status: number; retryAfter: string | null; body: unknown };

export function wantsStreamedResponse(request: Request) {
  return (request.headers.get("accept") ?? "").split(",").some((part) => {
    const [mediaType, ...parameters] = part.trim().toLowerCase().split(";");
    const quality = parameters.map(value => value.trim()).find(value => value.startsWith("q="));
    return mediaType.trim() === STREAMED_RESPONSE_MEDIA_TYPE
      && (quality === undefined || (Number(quality.slice(2)) > 0 && Number(quality.slice(2)) <= 1));
  });
}

/**
 * Run `respond` behind a keep-alive stream. A client that disconnects aborts
 * the signal of the request handed to `respond`, so upstream model work stops.
 */
export function streamedResponse(
  request: Request,
  respond: (request: Request) => Promise<Response>,
  heartbeatMs = STREAMED_RESPONSE_HEARTBEAT_MS,
) {
  const abort = new AbortController();
  // vinext can hand us a Request from its Node compatibility layer. Passing it
  // to workerd's native Request constructor treats it as "[object Request]".
  // Preserve its body/method implementations and override only cancellation.
  const inner = new Proxy(request, {
    get(target, property) {
      if (property === "signal") return abort.signal;
      const value = Reflect.get(target, property, target);
      return typeof value === "function" ? value.bind(target) : value;
    },
  });
  const encoder = new TextEncoder();
  let heartbeat: ReturnType<typeof setInterval> | undefined;
  let closed = false;
  let forwardAbort: () => void;
  const cleanup = () => {
    clearInterval(heartbeat);
    request.signal.removeEventListener("abort", forwardAbort);
  };
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      forwardAbort = () => {
        if (closed) return;
        closed = true;
        abort.abort(request.signal.reason);
        cleanup();
        controller.error(request.signal.reason ?? new DOMException("Request aborted", "AbortError"));
      };
      request.signal.addEventListener("abort", forwardAbort, { once: true });
      if (request.signal.aborted) {
        forwardAbort();
        return;
      }
      const send = (line: StreamedResponseLine) => {
        if (closed) return;
        controller.enqueue(encoder.encode(`${JSON.stringify(line)}\n`));
      };
      send({ type: "started" });
      heartbeat = setInterval(() => send({ type: "heartbeat" }), heartbeatMs);
      try {
        let response: Response;
        try {
          response = await respond(inner);
        } catch (error) {
          response = safeError(error);
        }
        // An unreadable body must fail the stream, never become a successful {}.
        const body: unknown = await response.json();
        if (!closed) {
          send({ type: "result", status: response.status, retryAfter: response.headers.get("retry-after"), body });
          closed = true;
          controller.close();
        }
      } catch (error) {
        if (!closed) {
          closed = true;
          controller.error(error);
        }
      } finally {
        cleanup();
      }
    },
    cancel(reason) {
      closed = true;
      cleanup();
      abort.abort(reason);
    },
  });
  return new Response(stream, {
    status: 200,
    headers: {
      "content-type": `${STREAMED_RESPONSE_MEDIA_TYPE}; charset=utf-8`,
      // `no-transform` keeps proxies from buffering the heartbeats for compression.
      "cache-control": "private, no-store, no-transform",
      "x-content-type-options": "nosniff",
    },
  });
}
