import { describe, expect, it, vi } from "vitest";
import { ensureBucket, assertPrivateBucket, verifyWorker, workerExists } from "../bootstrap.mjs";

const input = { accountId: "account", token: "token", bucket: "martina-edu-podcasts" };

describe("non-destructive bootstrap", () => {
  it("reuses an existing bucket", async () => {
    const fetcher = vi.fn(async () => ({ status: 200, json: async () => ({ success: true, result: { name: input.bucket } }) }));
    expect(await ensureBucket({ ...input, fetcher })).toBe("reused");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("creates only after an explicit 404", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce({ status: 404 }).mockResolvedValueOnce({
      status: 200, ok: true, json: async () => ({ success: true, result: { name: input.bucket } }),
    });
    expect(await ensureBucket({ ...input, fetcher })).toBe("created");
    expect(fetcher.mock.calls[1][1].method).toBe("POST");
    expect(fetcher.mock.calls[1][1].body).toContain("Standard");
  });
  it("fails closed on 403 and restricts worker URL", async () => {
    const fetcher = vi.fn(async () => ({ status: 403 }));
    await expect(ensureBucket({ ...input, fetcher })).rejects.toThrow("403");
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(() => verifyWorker("https://example.com")).toThrow();
  });
  it("distinguishes existing Worker from missing and denied", async () => {
    const existing = vi.fn(async () => ({ status: 200, json: async () => ({ success: true }) }));
    expect(await workerExists({ accountId: "account", token: "token", name: "audio", fetcher: existing })).toBe(true);
    expect(await workerExists({ accountId: "account", token: "token", name: "audio",
      fetcher: vi.fn(async () => ({ status: 404 })) })).toBe(false);
    await expect(workerExists({ accountId: "account", token: "token", name: "audio",
      fetcher: vi.fn(async () => ({ status: 403 })) })).rejects.toThrow("403");
  });
  it("requires a private bucket", async () => {
    const privateFetch = vi.fn().mockResolvedValueOnce({ status: 200, json: async () => ({ success: true, result: { enabled: false } }) })
      .mockResolvedValueOnce({ status: 200, json: async () => ({ success: true, result: { domains: [] } }) });
    await expect(assertPrivateBucket({ ...input, fetcher: privateFetch })).resolves.toBeUndefined();
    const publicFetch = vi.fn(async () => ({ status: 200, json: async () => ({ success: true, result: { enabled: true } }) }));
    await expect(assertPrivateBucket({ ...input, fetcher: publicFetch })).rejects.toThrow("Bucket públic");
    expect(publicFetch).toHaveBeenCalledTimes(1);
  });
});
