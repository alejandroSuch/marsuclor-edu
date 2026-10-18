export interface Env { PODCASTS: R2Bucket; SITE_ORIGIN: string }

const immutable = "public, max-age=31536000, immutable";
const keyPattern = /^podcasts\/[a-z0-9-]+\/[1-4]-eso\/[a-z0-9-]+\/[0-9a-f]{64}\.mp3$/;

function headers(origin: string): Headers {
  return new Headers({
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Expose-Headers": "Accept-Ranges, Content-Length, Content-Range, ETag",
    "Accept-Ranges": "bytes",
    "Cache-Control": immutable,
    "Content-Type": "audio/mpeg",
    "X-Content-Type-Options": "nosniff",
  });
}

function errorHeaders(origin: string): Headers {
  const result = headers(origin);
  result.set("Cache-Control", "no-store");
  return result;
}

export function parseRange(raw: string, size: number): { offset: number; length: number } | null {
  const match = /^bytes=(\d*)-(\d*)$/.exec(raw);
  if (!match || (!match[1] && !match[2]) || size === 0) return null;
  if (!match[1]) {
    const suffix = Number(match[2]);
    if (!Number.isSafeInteger(suffix) || suffix <= 0) return null;
    return { offset: Math.max(0, size - suffix), length: Math.min(size, suffix) };
  }
  const start = Number(match[1]);
  const end = match[2] ? Number(match[2]) : size - 1;
  if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start >= size || end < start) return null;
  return { offset: start, length: Math.min(end, size - 1) - start + 1 };
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const requested = new URL(request.url);
    const path = requested.pathname;
    if (request.method !== "GET" && request.method !== "HEAD") {
      const responseHeaders = errorHeaders(env.SITE_ORIGIN);
      responseHeaders.set("Allow", "GET, HEAD");
      return new Response(null, { status: 405, headers: responseHeaders });
    }
    if (path === "/health") {
      return new Response(request.method === "HEAD" ? null : "ok", { status: 200,
        headers: { "Content-Type": "text/plain", "Cache-Control": "no-store" } });
    }
    const key = path.slice(1);
    if (!keyPattern.test(key)) return new Response(null, { status: 404, headers: errorHeaders(env.SITE_ORIGIN) });
    if (requested.search && requested.search !== "?download=1") {
      return new Response(null, { status: 404, headers: errorHeaders(env.SITE_ORIGIN) });
    }
    const head = await env.PODCASTS.head(key);
    if (!head) return new Response(null, { status: 404, headers: errorHeaders(env.SITE_ORIGIN) });
    const responseHeaders = headers(env.SITE_ORIGIN);
    responseHeaders.set("ETag", head.httpEtag);
    if (requested.search === "?download=1") {
      responseHeaders.set("Content-Disposition", `attachment; filename="${key.split("/").at(-1)}"`);
    }
    if (request.headers.get("If-None-Match") === head.httpEtag) {
      return new Response(null, { status: 304, headers: responseHeaders });
    }
    const rawRange = request.headers.get("Range");
    const useRange = rawRange && (!request.headers.has("If-Range") || request.headers.get("If-Range") === head.httpEtag);
    const range = useRange ? parseRange(rawRange, head.size) : null;
    if (useRange && !range) {
      responseHeaders.set("Content-Range", `bytes */${head.size}`);
      responseHeaders.set("Cache-Control", "no-store");
      return new Response(null, { status: 416, headers: responseHeaders });
    }
    const length = range ? range.length : head.size;
    responseHeaders.set("Content-Length", String(length));
    if (range) responseHeaders.set("Content-Range", `bytes ${range.offset}-${range.offset + range.length - 1}/${head.size}`);
    const status = range ? 206 : 200;
    if (request.method === "HEAD") return new Response(null, { status, headers: responseHeaders });
    const object = await env.PODCASTS.get(key, range ? { range } : undefined);
    if (!object || !("body" in object)) return new Response(null, { status: 404, headers: errorHeaders(env.SITE_ORIGIN) });
    return new Response(object.body, { status, headers: responseHeaders });
  },
};
