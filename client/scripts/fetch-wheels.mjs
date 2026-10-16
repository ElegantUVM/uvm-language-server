// Per-target wheels for an offline Python 3.11–3.14 setup. Locks pin names and SHA-256 digests;
// every cache use is verified. `--lock --target <target>` resolves afresh for a dependency update.
import { execFileSync } from "child_process";
import crypto from "crypto";
import fs from "fs";
import os from "os";
import path from "path";
import { fileURLToPath } from "url";

import { python } from "./python.mjs";

const client = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const PYTHON_VERSIONS = ["3.11", "3.12", "3.13", "3.14"];
export const REQUIREMENTS = ["pyslang>=12", "pygls>=2,<3"];
export const TARGETS = {
  "linux-x64": { platforms: ["manylinux_2_28_x86_64", "manylinux_2_27_x86_64", "manylinux_2_17_x86_64", "manylinux2014_x86_64", "any"] },
  "win32-x64": { platforms: ["win_amd64", "any"] },
  "darwin-arm64": { platforms: ["macosx_11_0_universal2", "macosx_10_9_universal2", "macosx_11_0_arm64", "any"] },
  "darwin-x64": { platforms: ["macosx_11_0_universal2", "macosx_10_9_universal2", "macosx_10_9_x86_64", "macosx_11_0_x86_64", "any"] },
};

export function targetFromArgs(args = process.argv.slice(2), fallback) {
  const i = args.indexOf("--target");
  const target = i < 0 ? fallback : args[i + 1];
  if ((i >= 0 || target !== undefined) && !Object.hasOwn(TARGETS, target)) {
    throw new Error(`unsupported --target ${target}; expected ${Object.keys(TARGETS).join(", ")}`);
  }
  return target;
}

export function lockPath(target = "linux-x64") {
  targetFromArgs([], target);
  return path.join(client, target === "linux-x64" ? "wheels.lock.json" : `wheels.lock.${target}.json`);
}

export function cachePath(target = "linux-x64") {
  targetFromArgs([], target);
  return path.join(process.env.UVMLS_CACHE_DIR || path.join(os.homedir(), ".cache", "uvmls"), "wheels", target);
}

function sha256(file) {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

/** pip download for each supported Python version into `dir` (a version's wheels can overlap). */
function download(dir, target, requirements = REQUIREMENTS) {
  for (const version of PYTHON_VERSIONS) {
    console.log(`pip download for Python ${version} (${target})`);
    execFileSync(python(), ["-m", "pip", "download", "-q", "-d", dir, "--only-binary=:all:",
      "--python-version", version, "--implementation", "cp", "--abi", "abi3", "--abi", `cp${version.replace(".", "")}`,
      "--abi", "none", ...TARGETS[target].platforms.flatMap((p) => ["--platform", p]), ...requirements], { stdio: "inherit" });
  }
}

export function readLock(target = "linux-x64") {
  const lock = JSON.parse(fs.readFileSync(lockPath(target), "utf8"));
  if (lock.platform !== target) throw new Error(`wheel lock is for ${lock.platform}, not ${target}`);
  return lock;
}

function cachedIsGood(entry, cache) {
  const file = path.join(cache, entry.name);
  return fs.existsSync(file) && sha256(file) === entry.sha256;
}

/** Make sure the cache holds every pinned wheel; returns their paths. */
export function fetchWheels(target = "linux-x64") {
  const lock = readLock(target);
  const cache = cachePath(target);
  const missing = lock.files.filter((e) => !cachedIsGood(e, cache));
  if (missing.length) {
    console.log(`wheels: ${missing.length} of ${lock.files.length} not cached in ${cache}; fetching`);
    fs.mkdirSync(cache, { recursive: true });
    const work = fs.mkdtempSync(path.join(cache, "fetch-"));
    try {
      download(work, target, [...new Set(lock.files.map((entry) => {
        const [name, version] = entry.name.split("-");
        return `${name}==${version}`;
      }))]);
      for (const entry of missing) {
        const got = path.join(work, entry.name);
        if (!fs.existsSync(got)) {
          throw new Error(`${entry.name} is pinned in ${path.basename(lockPath(target))} but pip did not download it (run --lock to re-resolve)`);
        }
        const digest = sha256(got);
        if (digest !== entry.sha256) {
          throw new Error(`${entry.name}: sha256 ${digest} is not the pinned ${entry.sha256}`);
        }
        fs.renameSync(got, path.join(cache, entry.name));
      }
    } finally {
      fs.rmSync(work, { recursive: true, force: true });
    }
  }
  console.log(`wheels: ${lock.files.length} pinned wheels cached in ${cache}`);
  return lock.files.map((e) => path.join(cache, e.name));
}

/** Resolve afresh and rewrite the lock. */
export function relock(target = "linux-x64") {
  const lockFile = lockPath(target);
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "uvmls-wheels-"));
  try {
    download(work, target);
    const files = fs.readdirSync(work).filter((f) => f.endsWith(".whl")).sort().map((name) => {
      const file = path.join(work, name);
      return { name, size: fs.statSync(file).size, sha256: sha256(file) };
    });
    const lock = { generated: new Date().toISOString().slice(0, 10), python: PYTHON_VERSIONS, platform: target,
      requirements: REQUIREMENTS, files };
    const cache = cachePath(target);
    fs.mkdirSync(cache, { recursive: true });
    for (const entry of files) fs.copyFileSync(path.join(work, entry.name), path.join(cache, entry.name));
    fs.writeFileSync(lockFile, JSON.stringify(lock, null, 2) + "\n");
    console.log(`wrote ${lockFile}: ${files.length} wheels, ${(files.reduce((n, f) => n + f.size, 0) / 1e6).toFixed(1)} MB`);
  } finally {
    fs.rmSync(work, { recursive: true, force: true });
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const target = targetFromArgs(undefined, "linux-x64");
  if (process.argv.includes("--lock")) {
    relock(target);
  } else {
    fetchWheels(target);
  }
}
