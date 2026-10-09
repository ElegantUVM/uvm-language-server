// Fetch the pinned Verible release once into ~/.cache/uvmls, keeping only verible-verilog-format, so
// that `npm run package` bundles it offline from then on. The binary never enters git:
// bundle-server.mjs copies it from the cache into client/server/ at package time. Its licence text is
// vendored in client/third_party/verible-LICENSE. The cached binary is checked against its own pinned
// digest on every use, so a tampered or corrupt cache is re-fetched rather than shipped.
import { execFileSync } from "child_process";
import crypto from "crypto";
import fs from "fs";
import os from "os";
import path from "path";
import { fileURLToPath } from "url";

export const VERIBLE_VERSION = "v0.0-4296-g0f262651";
export const VERIBLE_PLATFORM = "linux-static-x86_64";
export const VERIBLE_TARBALL_URL = "https://github.com/chipsalliance/verible/releases/download/" +
  `${VERIBLE_VERSION}/verible-${VERIBLE_VERSION}-${VERIBLE_PLATFORM}.tar.gz`;
export const VERIBLE_TARBALL_SHA256 = "8569defb891d2316067613ea00442af28a7a09d405d95b54c0c91f9942d26635";
// sha256 of the extracted verible-verilog-format inside that tarball.
export const VERIBLE_BINARY_SHA256 = "daf292645c850a64a320a3a13edd8a3521f80151fd049326da8887a9bba753f8";
const MEMBER = `verible-${VERIBLE_VERSION}/bin/verible-verilog-format`;

export const CACHE = path.join(os.homedir(), ".cache", "uvmls", "verible", VERIBLE_VERSION);
export const CACHED_BINARY = path.join(CACHE, "verible-verilog-format");
export const LICENSE = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "third_party", "verible-LICENSE");

function sha256(file) {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

async function download(url, to) {
  console.log(`fetching ${url}`);
  const response = await fetch(url, { redirect: "follow" });
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  fs.writeFileSync(to, Buffer.from(await response.arrayBuffer()));
}

/** Verified tarball: a local copy from UVMLS_VERIBLE_TARBALL (offline use), else the release asset. */
async function tarball(work) {
  const file = path.join(work, "verible.tar.gz");
  const local = process.env.UVMLS_VERIBLE_TARBALL;
  if (local) {
    console.log(`using tarball ${local}`);
    fs.copyFileSync(local, file);
  } else {
    await download(VERIBLE_TARBALL_URL, file);
  }
  const digest = sha256(file);
  if (digest !== VERIBLE_TARBALL_SHA256) {
    throw new Error(`${local || VERIBLE_TARBALL_URL}: sha256 ${digest} is not the pinned ${VERIBLE_TARBALL_SHA256}`);
  }
  return file;
}

function cachedBinaryIsGood() {
  return fs.existsSync(CACHED_BINARY) && sha256(CACHED_BINARY) === VERIBLE_BINARY_SHA256;
}

/** Make sure the cache holds the pinned formatter; returns its path and the vendored licence's. */
export async function fetchVerible() {
  if (!/Apache License\s+Version 2\.0/.test(fs.readFileSync(LICENSE, "utf8"))) {
    throw new Error(`${LICENSE} is not the Apache License 2.0 text`);
  }
  if (cachedBinaryIsGood()) {
    console.log(`verible ${VERIBLE_VERSION}: cached in ${CACHE}`);
    return { binary: CACHED_BINARY, license: LICENSE };
  }
  if (fs.existsSync(CACHED_BINARY)) {
    console.log(`verible ${VERIBLE_VERSION}: the cached binary does not match its pinned digest; fetching again`);
    fs.rmSync(CACHED_BINARY, { force: true });
  }
  fs.mkdirSync(CACHE, { recursive: true });
  const work = fs.mkdtempSync(path.join(CACHE, "fetch-"));
  try {
    const file = await tarball(work);
    execFileSync("tar", ["-xzf", file, "-C", work, "--strip-components=2", MEMBER], { stdio: "inherit" });
    const binary = path.join(work, "verible-verilog-format");
    const digest = sha256(binary);
    if (digest !== VERIBLE_BINARY_SHA256) {
      throw new Error(`extracted verible-verilog-format: sha256 ${digest} is not the pinned ${VERIBLE_BINARY_SHA256}`);
    }
    fs.chmodSync(binary, 0o755);
    fs.renameSync(binary, CACHED_BINARY);
  } finally {
    fs.rmSync(work, { recursive: true, force: true });
  }
  console.log(`verible ${VERIBLE_VERSION}: ${CACHED_BINARY} (sha256 ${VERIBLE_BINARY_SHA256})`);
  return { binary: CACHED_BINARY, license: LICENSE };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  fetchVerible().catch((error) => {
    console.error(`fetch-verible: ${error.message}`);
    process.exit(1);
  });
}
