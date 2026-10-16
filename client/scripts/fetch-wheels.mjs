// Fetch the Python wheels the linux-x64 package ships under server/wheels, so the extension can set
// up its environment without any network: pyslang (one abi3 wheel for Python 3.11+), its per-version
// nanobind backend, pygls and their pure-Python dependencies, for manylinux x86_64. The exact files
// are pinned in wheels.lock.json (name and sha256); the cache in ~/.cache/uvmls/wheels is checked
// against the lock on every use, like the Verible binary, so a tampered or stale cache is re-fetched
// rather than shipped. `node scripts/fetch-wheels.mjs --lock` resolves afresh with pip and rewrites
// the lock (review the diff: it is the dependency upgrade).
import { execFileSync } from "child_process";
import crypto from "crypto";
import fs from "fs";
import os from "os";
import path from "path";
import { fileURLToPath } from "url";

const client = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const LOCK = path.join(client, "wheels.lock.json");
export const CACHE = path.join(os.homedir(), ".cache", "uvmls", "wheels");
export const PYTHON_VERSIONS = ["3.11", "3.12", "3.13", "3.14"];
export const REQUIREMENTS = ["pyslang>=12", "pygls>=2,<3"];
// Wheels a Python 3.11+ on a glibc 2.27+ x86_64 Linux (Enterprise Linux 8 and later) can use.
const PLATFORMS = ["manylinux_2_28_x86_64", "manylinux_2_27_x86_64", "manylinux_2_17_x86_64", "manylinux2014_x86_64", "any"];

function sha256(file) {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

/** The interpreter whose pip resolves the wheels: UVMLS_PYTHON, the repository's venv, else python3. */
function python() {
  const venv = path.resolve(client, "..", ".venv", "bin", "python");
  return process.env.UVMLS_PYTHON || (fs.existsSync(venv) ? venv : "python3");
}

/** pip download for each supported Python version into `dir` (a version's wheels can overlap). */
function download(dir) {
  for (const version of PYTHON_VERSIONS) {
    console.log(`pip download for Python ${version} (linux x86_64)`);
    execFileSync(python(), ["-m", "pip", "download", "-q", "-d", dir, "--only-binary=:all:",
      "--python-version", version, "--implementation", "cp", "--abi", "abi3", "--abi", `cp${version.replace(".", "")}`,
      "--abi", "none", ...PLATFORMS.flatMap((p) => ["--platform", p]), ...REQUIREMENTS], { stdio: "inherit" });
  }
}

export function readLock() {
  return JSON.parse(fs.readFileSync(LOCK, "utf8"));
}

function cachedIsGood(entry) {
  const file = path.join(CACHE, entry.name);
  return fs.existsSync(file) && sha256(file) === entry.sha256;
}

/** Make sure the cache holds every pinned wheel; returns their paths. */
export function fetchWheels() {
  const lock = readLock();
  const missing = lock.files.filter((e) => !cachedIsGood(e));
  if (missing.length) {
    console.log(`wheels: ${missing.length} of ${lock.files.length} not cached in ${CACHE}; fetching`);
    fs.mkdirSync(CACHE, { recursive: true });
    const work = fs.mkdtempSync(path.join(CACHE, "fetch-"));
    try {
      download(work);
      for (const entry of missing) {
        const got = path.join(work, entry.name);
        if (!fs.existsSync(got)) {
          throw new Error(`${entry.name} is pinned in wheels.lock.json but pip did not download it (run --lock to re-resolve)`);
        }
        const digest = sha256(got);
        if (digest !== entry.sha256) {
          throw new Error(`${entry.name}: sha256 ${digest} is not the pinned ${entry.sha256}`);
        }
        fs.renameSync(got, path.join(CACHE, entry.name));
      }
    } finally {
      fs.rmSync(work, { recursive: true, force: true });
    }
  }
  console.log(`wheels: ${lock.files.length} pinned wheels cached in ${CACHE}`);
  return lock.files.map((e) => path.join(CACHE, e.name));
}

/** Resolve afresh and rewrite the lock. */
export function relock() {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "uvmls-wheels-"));
  try {
    download(work);
    const files = fs.readdirSync(work).filter((f) => f.endsWith(".whl")).sort().map((name) => {
      const file = path.join(work, name);
      return { name, size: fs.statSync(file).size, sha256: sha256(file) };
    });
    const lock = { generated: new Date().toISOString().slice(0, 10), python: PYTHON_VERSIONS, platform: "linux-x64",
      requirements: REQUIREMENTS, files };
    fs.writeFileSync(LOCK, JSON.stringify(lock, null, 2) + "\n");
    console.log(`wrote ${LOCK}: ${files.length} wheels, ${(files.reduce((n, f) => n + f.size, 0) / 1e6).toFixed(1)} MB`);
  } finally {
    fs.rmSync(work, { recursive: true, force: true });
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (process.argv.includes("--lock")) {
    relock();
  } else {
    fetchWheels();
  }
}
