import { describe, expect, it, vi } from "vitest";
import handler, { parseRange } from "../src/index";

const digest = "a".repeat(64);
const url = `https://martina-edu-audio.example.workers.dev/podcasts/geografia/1-eso/terra/${digest}.mp3`;
const bytes = new TextEncoder().encode("0123456789");
const metadata = { size: bytes.length, httpEtag: '"abc"' };

function fixture(exists = true) {
  const head = vi.fn(async () => exists ? metadata : null);
  const get = vi.fn(async (_key: string, options?: { range?: { offset: number; length: number } }) => {
    const range = options?.range;
    const payload = range ? bytes.slice(range.offset, range.offset + range.length) : bytes;
    return { body: new ReadableStream({ start(controller) { controller.enqueue(payload); controller.close(); } }) };
  });
  return { env: { PODCASTS: { head, get }, SITE_ORIGIN: "https://alejandrosuch.github.io" } as unknown as
    { PODCASTS: R2Bucket; SITE_ORIGIN: string }, head, get };
}

describe("read only podcast Worker", () => {
  it("serves full GET and HEAD with immutable headers", async () => {
    const { env, get } = fixture();
    const response = await handler.fetch(new Request(url), env);
    expect(response.status).toBe(200);
    expect(await response.text()).toBe("0123456789");
    expect(response.headers.get("Content-Length")).toBe("10");
    expect(response.headers.get("ETag")).toBe('"abc"');
    expect(response.headers.get("Accept-Ranges")).toBe("bytes");
    expect(response.headers.get("Cache-Control")).toContain("immutable");
    expect(response.headers.get("Access-Control-Allow-Origin")).toBe("https://alejandrosuch.github.io");
    const head = await handler.fetch(new Request(url, { method: "HEAD" }), env);
    expect(head.status).toBe(200);
    expect(head.headers.get("Content-Length")).toBe("10");
    expect(get).toHaveBeenCalledTimes(1);
  });

  it("handles ranges and rejects invalid ones", async () => {
    const { env, get } = fixture();
    const partial = await handler.fetch(new Request(url, { headers: { Range: "bytes=2-5" } }), env);
    expect(partial.status).toBe(206);
    expect(partial.headers.get("Content-Range")).toBe("bytes 2-5/10");
    expect(partial.headers.get("Content-Length")).toBe("4");
    expect(partial.headers.get("Accept-Ranges")).toBe("bytes");
    expect(await partial.text()).toBe("2345");
    expect(get).toHaveBeenCalledWith(expect.any(String), { range: { offset: 2, length: 4 } });
    const suffix = await handler.fetch(new Request(url, { headers: { Range: "bytes=-3" } }), env);
    expect(await suffix.text()).toBe("789");
    const invalid = await handler.fetch(new Request(url, { headers: { Range: "bytes=99-" } }), env);
    expect(invalid.status).toBe(416);
    expect(invalid.headers.get("Content-Range")).toBe("bytes */10");
    expect(parseRange("bytes=0-1,3-4", 10)).toBeNull();
  });

  it("downloads the immutable MP3 as an attachment without affecting playback", async () => {
    const { env } = fixture();
    const playback = await handler.fetch(new Request(url), env);
    expect(playback.headers.get("Content-Disposition")).toBeNull();
    const downloaded = await handler.fetch(new Request(url + "?download=1"), env);
    expect(downloaded.status).toBe(200);
    expect(downloaded.headers.get("Content-Disposition")).toBe(`attachment; filename="${digest}.mp3"`);
    expect(await downloaded.text()).toBe("0123456789");
    const partial = await handler.fetch(new Request(url + "?download=1", { headers: { Range: "bytes=5-" } }), env);
    expect(partial.status).toBe(206);
    expect(partial.headers.get("Content-Range")).toBe("bytes 5-9/10");
    expect(partial.headers.get("Content-Disposition")).toContain("attachment");
    expect((await handler.fetch(new Request(url + "?unknown=1"), env)).status).toBe(404);
  });

  it("returns 304, 404 and 405 as appropriate", async () => {
    const { env, get } = fixture();
    expect((await handler.fetch(new Request(url, { headers: { "If-None-Match": '"abc"' } }), env)).status).toBe(304);
    expect((await handler.fetch(new Request(url.replace(".mp3", ".json")), env)).status).toBe(404);
    expect((await handler.fetch(new Request(url, { method: "PUT" }), env)).status).toBe(405);
    expect((await handler.fetch(new Request(url), fixture(false).env)).status).toBe(404);
    expect(get).not.toHaveBeenCalled();
  });
});
