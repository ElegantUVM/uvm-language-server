const assert = require("node:assert/strict");
const { execFile } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");
const { promisify } = require("node:util");
const { pathToFileURL } = require("node:url");
const execute = promisify(execFile);
const client = path.resolve(__dirname, "..");
const targets = ["linux-x64", "win32-x64", "darwin-arm64", "darwin-x64"];
const hash = (data) => crypto.createHash("sha256").update(data).digest("hex");

function writeFixture(file, data) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, data);
}

// Synthetic release assets exercise verified extraction and packaging without a network.
async function fixture(t) {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "uvmls packages # $-")));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const dir = path.join(root, "client");
  fs.mkdirSync(dir);
  for (const name of ["scripts", "third_party"]) fs.cpSync(path.join(client, name), path.join(dir, name), { recursive: true });
  fs.mkdirSync(path.join(root, "server", "uvmls"), { recursive: true });
  writeFixture(path.join(root, "server", "uvmls", "__init__.py"), "# fixture\n");
  fs.mkdirSync(path.join(dir, "dist"));
  writeFixture(path.join(dir, "dist", "webview.js"), "/* Copyright (c) 2017 Kiel University */");
  writeFixture(path.join(dir, "package.json"), JSON.stringify({ name: "fixture", version: "1.0.0", dependencies: {} }));
  for (const file of ["LICENSE", "NOTICE", "CHANGELOG.md"]) writeFixture(path.join(root, file), file);
  const { python } = await import(pathToFileURL(path.join(client, "scripts", "python.mjs")));
  const env = { ...process.env, UVMLS_PYTHON: python(), UVMLS_CACHE_DIR: path.join(root, "cache") };
  const run = (script, args = []) => execute(process.execPath, [path.join(dir, "scripts", script), ...args], { env });
  const py = (code, args = []) => execute(python(), ["-c", code, ...args]);
  const manifest = { version: "fixture", targets: {} };
  for (const target of targets) {
    const binary = target === "win32-x64" ? "verible-verilog-format.exe" : "verible-verilog-format";
    const asset = target + (target === "win32-x64" ? ".zip" : ".tar.gz");
    const archive = path.join(root, asset);
    const body = `fixture formatter ${target}`;
    const member = `release/bin/${binary}`;
    await py("import io,sys,tarfile,zipfile\np,m,s=sys.argv[1:]\nb=s.encode()\n" +
      "if p.endswith('.zip'):\n with zipfile.ZipFile(p,'w') as z: z.writestr(m,b)\n" +
      "else:\n with tarfile.open(p,'w:gz') as z:\n  i=tarfile.TarInfo(m); i.size=len(b); z.addfile(i,io.BytesIO(b))\n",
    [archive, member, body]);
    if (target !== "darwin-x64") manifest.targets[target] = { asset, member, archiveSha256: hash(fs.readFileSync(archive)), binarySha256: hash(body) };
    env[`UVMLS_VERIBLE_TARBALL_${target.replace(/-/g, "_").toUpperCase()}`] = archive;
    const cache = path.join(env.UVMLS_CACHE_DIR, "wheels", target);
    fs.mkdirSync(cache, { recursive: true });
    const name = `demo-1.0-py3-none-${target.replace(/-/g, "_")}.whl`;
    const file = path.join(cache, name);
    await py("import sys,zipfile\nwith zipfile.ZipFile(sys.argv[1],'w') as z:\n z.writestr('demo-1.0.dist-info/METADATA','License-Expression: MIT\\n')\n z.writestr('demo-1.0.dist-info/licenses/LICENSE','MIT licence fixture')", [file]);
    const lock = { platform: target, files: [{ name, size: fs.statSync(file).size, sha256: hash(fs.readFileSync(file)) }] };
    writeFixture(path.join(dir, target === "linux-x64" ? "wheels.lock.json" : `wheels.lock.${target}.json`), JSON.stringify(lock));
  }
  writeFixture(path.join(dir, "verible.lock.json"), JSON.stringify(manifest));
  async function pack(target) {
    const uvm = path.join(dir, "server", "uvmls", "third_party", "uvm-1.2");
    fs.mkdirSync(uvm, { recursive: true });
    for (const name of ["LICENSE.txt", "NOTICE.txt"]) writeFixture(path.join(uvm, name), name);
    const module = pathToFileURL(path.join(dir, "scripts", "notices.mjs")).href;
    await execute(process.execPath, ["--input-type=module", "-e",
      `import { writeNotices } from ${JSON.stringify(module)}; await writeNotices([], 'THIRD_PARTY_NOTICES.txt');`], { env });
    const vsix = path.join(dir, `fixture${target ? `-${target}` : ""}-1.0.0.vsix`);
    await py("import pathlib,sys,zipfile\nr=pathlib.Path(sys.argv[1])\nwith zipfile.ZipFile(sys.argv[2],'w') as z:\n" +
      " for p in (r/'client'/'server').rglob('*'):\n  if p.is_file(): z.write(p,'extension/'+p.relative_to(r/'client').as_posix())\n" +
      " for name,member in [('LICENSE','LICENSE.txt'),('NOTICE','NOTICE'),('CHANGELOG.md','changelog.md')]: z.write(r/name,'extension/'+member)\n" +
      " for name in ['dist/webview.js','THIRD_PARTY_NOTICES.txt']: z.write(r/'client'/name,'extension/'+name)\n", [root, vsix]);
    return vsix;
  }
  return { dir, env, run, pack };
}

test("per-target archives, caches, notices and bundles survive a generic rebuild", async (t) => {
  const f = await fixture(t);
  for (const target of targets) {
    await f.run("bundle-server.mjs", ["--target", target]);
    const formatterDir = path.join(f.dir, "server", "uvmls", "third_party", "verible");
    if (target === "darwin-x64") assert.ok(!fs.existsSync(formatterDir));
    else assert.deepEqual(fs.readdirSync(formatterDir), [target]);
    await f.pack(target);
    await f.run("check-notices.mjs", ["--target", target]);
  }
  await f.run("bundle-server.mjs");
  assert.ok(!fs.existsSync(path.join(f.dir, "server", "wheels")));
  assert.ok(!fs.existsSync(path.join(f.dir, "server", "uvmls", "third_party", "verible")));
  await f.pack();
  await f.run("check-notices.mjs");
});

test("corrupt archives and cached binaries never bypass their pins", async (t) => {
  const f = await fixture(t);
  const target = "win32-x64";
  await f.run("fetch-verible.mjs", ["--target", target]);
  const cached = path.join(f.env.UVMLS_CACHE_DIR, "verible", "fixture", target, "verible-verilog-format.exe");
  writeFixture(cached, "tampered cache");
  await f.run("fetch-verible.mjs", ["--target", target]);
  assert.equal(fs.readFileSync(cached, "utf8"), "fixture formatter win32-x64");
  writeFixture(cached, "tampered again");
  writeFixture(f.env.UVMLS_VERIBLE_TARBALL_WIN32_X64, "corrupt archive");
  await assert.rejects(f.run("fetch-verible.mjs", ["--target", target]), { code: 1 });
});

test("VSIX verification rejects a modified wheel and unexpected target files", async (t) => {
  const f = await fixture(t);
  await f.run("bundle-server.mjs", ["--target", "linux-x64"]);
  const wheels = path.join(f.dir, "server", "wheels");
  fs.appendFileSync(path.join(wheels, fs.readdirSync(wheels)[0]), "modified");
  // Generate notices before adding the intentionally invalid wheel.
  await f.pack("linux-x64");
  const vsix = path.join(f.dir, "fixture-linux-x64-1.0.0.vsix");
  const { python } = await import(pathToFileURL(path.join(client, "scripts", "python.mjs")));
  await execute(python(), ["-c", "import sys,zipfile\nwith zipfile.ZipFile(sys.argv[1],'a') as z: z.writestr('extension/server/wheels/unexpected.whl','extra')", vsix]);
  await assert.rejects(f.run("check-notices.mjs", ["--target", "linux-x64"]), { code: 1 });
});

test("target arguments reject unsupported or missing values", async () => {
  const { targetFromArgs, lockPath, cachePath } = await import(pathToFileURL(path.join(client, "scripts", "fetch-wheels.mjs")));
  assert.throws(() => targetFromArgs(["--target"]), /unsupported/);
  assert.throws(() => targetFromArgs(["--target", "linux-arm64"]), /unsupported/);
  assert.equal(targetFromArgs([]), undefined);
  assert.equal(targetFromArgs([], "linux-x64"), "linux-x64");
  assert.ok(lockPath("darwin-arm64").endsWith("wheels.lock.darwin-arm64.json"));
  assert.notEqual(cachePath("darwin-arm64"), cachePath("darwin-x64"));
});


test("Intel macOS rejects a leaked arm64 formatter", async (t) => {
  const f = await fixture(t);
  await f.run("bundle-server.mjs", ["--target", "darwin-x64"]);
  const foreign = path.join(f.dir, "server", "uvmls", "third_party", "verible", "darwin-arm64");
  fs.mkdirSync(foreign, { recursive: true });
  writeFixture(path.join(foreign, "verible-verilog-format"), "arm64 binary");
  await f.pack("darwin-x64");
  await assert.rejects(f.run("check-notices.mjs", ["--target", "darwin-x64"]), { code: 1 });
});
