import { readFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";

export async function ensureBucket({ accountId, token, bucket, fetcher = fetch }) {
  if (!accountId || !token) throw new Error("Falten credencials de bootstrap");
  const root = `https://api.cloudflare.com/client/v4/accounts/${accountId}/r2/buckets`;
  const headers = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
  const check = await fetcher(`${root}/${bucket}`, { headers });
  if (check.status === 200) {
    const body = await check.json();
    if (!body.success || body.result?.name !== bucket) throw new Error("Resposta de bucket inconsistent");
    if (body.result.storage_class && body.result.storage_class !== "Standard") {
      throw new Error("El bucket existent no usa R2 Standard");
    }
    return "reused";
  }
  if (check.status !== 404) throw new Error(`Consulta de bucket fallida: HTTP ${check.status}`);
  const created = await fetcher(root, { method: "POST", headers,
    body: JSON.stringify({ name: bucket, storageClass: "Standard" }) });
  const result = await created.json();
  if (!created.ok || !result.success || result.result?.name !== bucket) {
    throw new Error(`Creació de bucket fallida: HTTP ${created.status}`);
  }
  return "created";
}

export async function assertPrivateBucket({ accountId, token, bucket, fetcher = fetch }) {
  const base = `https://api.cloudflare.com/client/v4/accounts/${accountId}/r2/buckets/${bucket}/domains`;
  for (const part of ["managed", "custom"]) {
    const response = await fetcher(`${base}/${part}`, { headers: { Authorization: `Bearer ${token}` } });
    if (response.status !== 200) throw new Error(`Privacitat del bucket no verificable: HTTP ${response.status}`);
    const body = await response.json();
    if (!body.success || !body.result) throw new Error("Resposta de dominis R2 inconsistent");
    if (part === "managed" && typeof body.result.enabled !== "boolean") {
      throw new Error("Estat r2.dev desconegut");
    }
    if (part === "custom" && !Array.isArray(body.result.domains)) {
      throw new Error("Dominis personalitzats desconeguts");
    }
    const exposed = part === "managed" ? body.result.enabled : body.result.domains.some(domain => domain.enabled);
    if (exposed) throw new Error(`Bucket públic per ${part}; desactiva l'accés públic en R2`);
  }
}

export function verifyWorker(url) {
  if (!/^https:\/\/[a-z0-9-]+\.[a-z0-9-]+\.workers\.dev$/.test(url)) {
    throw new Error("La URL del Worker ha de ser workers.dev");
  }
  return url;
}

export async function workerExists({ accountId, token, name, fetcher = fetch }) {
  const response = await fetcher(
    `https://api.cloudflare.com/client/v4/accounts/${accountId}/workers/scripts/${name}/subdomain`,
    { headers: { Authorization: `Bearer ${token}` } },
  );
  if (response.status === 404) return false;
  if (response.status !== 200 || !(await response.json()).success) {
    throw new Error(`Consulta de Worker fallida: HTTP ${response.status}`);
  }
  return true;
}

async function main() {
  const config = JSON.parse((await readFile(new URL("./wrangler.jsonc", import.meta.url), "utf8"))
    .replace(/^\s*\/\/.*$/gm, ""));
  const bucket = config.r2_buckets[0].bucket_name;
  const outcome = await ensureBucket({ accountId: process.env.CLOUDFLARE_ACCOUNT_ID,
    token: process.env.CLOUDFLARE_BOOTSTRAP_API_TOKEN, bucket });
  console.log(`Bucket ${bucket}: ${outcome}`);
  await assertPrivateBucket({ accountId: process.env.CLOUDFLARE_ACCOUNT_ID,
    token: process.env.CLOUDFLARE_BOOTSTRAP_API_TOKEN, bucket });
  const exists = await workerExists({ accountId: process.env.CLOUDFLARE_ACCOUNT_ID,
    token: process.env.CLOUDFLARE_BOOTSTRAP_API_TOKEN, name: config.name });
  const deployToken = exists ? process.env.CLOUDFLARE_API_TOKEN : process.env.CLOUDFLARE_BOOTSTRAP_API_TOKEN;
  if (!deployToken) throw new Error("Falta el token de desplegament del Worker");
  const deploy = spawnSync("npx", ["wrangler", "deploy"], { stdio: "inherit",
    env: { ...process.env, CLOUDFLARE_API_TOKEN: deployToken } });
  if (deploy.status !== 0) throw new Error("Wrangler deploy ha fallat");
  const url = verifyWorker(process.env.MARTINA_AUDIO_BASE_URL || "");
  const response = await fetch(`${url}/health`);
  if (!response.ok || await response.text() !== "ok") throw new Error(`Worker no disponible: HTTP ${response.status}`);
  console.log(`Worker disponible: ${url}`);
}

if (process.argv[1] && import.meta.url === new URL(`file://${process.argv[1]}`).href) {
  main().catch(error => { console.error(error.message); process.exitCode = 1; });
}
