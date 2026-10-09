// Fetch the pinned Verible release once into ~/.cache/uvmls, keeping only verible-verilog-format and
// Verible's LICENSE, so that `npm run package` bundles them offline from then on. The binary never
// enters git: bundle-server.mjs copies it from the cache into client/server/ at package time.
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
export const VERIBLE_LICENSE_URL =
  `https://raw.githubusercontent.com/chipsalliance/verible/${VERIBLE_VERSION}/LICENSE`;
const MEMBER = `verible-${VERIBLE_VERSION}/bin/verible-verilog-format`;

export const CACHE = path.join(os.homedir(), ".cache", "uvmls", "verible", VERIBLE_VERSION);
export const CACHED_BINARY = path.join(CACHE, "verible-verilog-format");
export const CACHED_LICENSE = path.join(CACHE, "LICENSE");

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

/** Make sure the cache holds the formatter and its licence; returns their paths. */
export async function fetchVerible() {
  if (fs.existsSync(CACHED_BINARY) && fs.existsSync(CACHED_LICENSE)) {
    console.log(`verible ${VERIBLE_VERSION}: cached in ${CACHE}`);
    return { binary: CACHED_BINARY, license: CACHED_LICENSE };
  }
  fs.mkdirSync(CACHE, { recursive: true });
  const work = fs.mkdtempSync(path.join(CACHE, "fetch-"));
  try {
    if (!fs.existsSync(CACHED_BINARY)) {
      const file = await tarball(work);
      execFileSync("tar", ["-xzf", file, "-C", work, "--strip-components=2", MEMBER], { stdio: "inherit" });
      const binary = path.join(work, "verible-verilog-format");
      fs.chmodSync(binary, 0o755);
      fs.renameSync(binary, CACHED_BINARY);
    }
    if (!fs.existsSync(CACHED_LICENSE)) {
      const file = path.join(work, "LICENSE");
      await download(VERIBLE_LICENSE_URL, file);
      const text = fs.readFileSync(file, "utf8");
      if (!/Apache License\s+Version 2\.0/.test(text)) {
        throw new Error(`${VERIBLE_LICENSE_URL} is not the Apache License 2.0 text`);
      }
      fs.renameSync(file, CACHED_LICENSE);
    }
  } finally {
    fs.rmSync(work, { recursive: true, force: true });
  }
  console.log(`verible ${VERIBLE_VERSION}: ${CACHED_BINARY} (sha256 ${sha256(CACHED_BINARY)})`);
  return { binary: CACHED_BINARY, license: CACHED_LICENSE };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  fetchVerible().catch((error) => {
    console.error(`fetch-verible: ${error.message}`);
    process.exit(1);
  });
}
