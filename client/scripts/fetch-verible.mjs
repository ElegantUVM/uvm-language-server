// Each target pins both the release archive and the extracted formatter. Only verified binaries
// enter the cache; UVMLS_VERIBLE_TARBALL[_<TARGET>] supports builds from local release archives.
import crypto from "crypto";
import fs from "fs";
import os from "os";
import path from "path";
import { fileURLToPath } from "url";
import { extractMember } from "./archive.mjs";
import { targetFromArgs } from "./fetch-wheels.mjs";

const client = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const lock = JSON.parse(fs.readFileSync(path.join(client, "verible.lock.json"), "utf8"));
export const VERIBLE_VERSION = lock.version;
export const LICENSE = path.join(client, "third_party", "verible-LICENSE");

export function veribleTarget(target = "linux-x64") {
  targetFromArgs([], target);
  // This release has no Intel macOS binary; its package deliberately ships wheels only.
  if (target === "darwin-x64") return undefined;
  const entry = lock.targets[target];
  if (!entry) throw new Error(`Verible ${target} has no verified entry in client/verible.lock.json; pin its archive and binary before packaging`);
  if (![entry.archiveSha256, entry.binarySha256].every((hash) => /^[a-f0-9]{64}$/.test(hash))) {
    throw new Error(`Verible ${target} has invalid SHA-256 pins`);
  }
  const binary = target === "win32-x64" ? "verible-verilog-format.exe" : "verible-verilog-format";
  return { ...entry, binary, url: `https://github.com/chipsalliance/verible/releases/download/${VERIBLE_VERSION}/${entry.asset}` };
}

export function cachePath(target = "linux-x64") {
  targetFromArgs([], target);
  return path.join(process.env.UVMLS_CACHE_DIR || path.join(os.homedir(), ".cache", "uvmls"),
    "verible", VERIBLE_VERSION, target);
}

function sha256(file) {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

export async function fetchVerible(target = "linux-x64") {
  const config = veribleTarget(target);
  if (!config) throw new Error(`${target} ships no bundled formatter; set uvm.formatter.path to a local Verible`);
  const cache = cachePath(target);
  const cached = path.join(cache, config.binary);
  if (!/Apache License\s+Version 2\.0/.test(fs.readFileSync(LICENSE, "utf8"))) {
    throw new Error(`${LICENSE} is not the Apache License 2.0 text`);
  }
  if (fs.existsSync(cached) && sha256(cached) === config.binarySha256) {
    console.log(`verible ${VERIBLE_VERSION} (${target}): cached in ${cache}`);
    return { binary: cached, license: LICENSE };
  }
  fs.mkdirSync(cache, { recursive: true });
  const work = fs.mkdtempSync(path.join(cache, "fetch-"));
  try {
    const archive = path.join(work, config.asset);
    const local = process.env[`UVMLS_VERIBLE_TARBALL_${target.replace(/-/g, "_").toUpperCase()}`]
      || process.env.UVMLS_VERIBLE_TARBALL;
    if (local) {
      fs.copyFileSync(local, archive);
    } else {
      console.log(`fetching ${config.url}`);
      const response = await fetch(config.url, { redirect: "follow" });
      if (!response.ok) throw new Error(`${config.url}: HTTP ${response.status}`);
      fs.writeFileSync(archive, Buffer.from(await response.arrayBuffer()));
    }
    const digest = sha256(archive);
    if (digest !== config.archiveSha256) {
      throw new Error(`${local || config.url}: sha256 ${digest} is not the pinned ${config.archiveSha256}`);
    }
    const binary = path.join(work, config.binary);
    await extractMember(archive, config.member, binary);
    const binaryDigest = sha256(binary);
    if (binaryDigest !== config.binarySha256) {
      throw new Error(`extracted ${config.binary}: sha256 ${binaryDigest} is not the pinned ${config.binarySha256}`);
    }
    if (process.platform !== "win32") fs.chmodSync(binary, 0o755);
    fs.copyFileSync(binary, cached);
  } finally {
    fs.rmSync(work, { recursive: true, force: true });
  }
  console.log(`verible ${VERIBLE_VERSION} (${target}): ${cached}`);
  return { binary: cached, license: LICENSE };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  Promise.resolve().then(() => fetchVerible(targetFromArgs(undefined, "linux-x64"))).catch((error) => {
    console.error(`fetch-verible: ${error.message}`);
    process.exitCode = 1;
  });
}
