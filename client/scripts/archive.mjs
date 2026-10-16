// Python's standard-library archive readers work on Windows too; no unzip executable is needed.
import { execFile } from "child_process";
import { promisify } from "util";
import { python } from "./python.mjs";

const execute = promisify(execFile);
async function run(code, args) {
  const { stdout } = await execute(python(), ["-c", code, ...args], { encoding: "buffer", maxBuffer: 128 * 1024 * 1024 });
  return stdout;
}

export async function zipEntries(file) {
  return JSON.parse((await run("import json,sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); " +
    "print(json.dumps([{'name':i.filename,'mode':i.external_attr >> 16,'system':i.create_system} for i in z.infolist()]))",
  [file])).toString("utf8"));
}

export async function zipRead(file, member) {
  return run("import sys,zipfile; sys.stdout.buffer.write(zipfile.ZipFile(sys.argv[1]).read(sys.argv[2]))", [file, member]);
}

export async function extractMember(file, member, destination) {
  await run("import sys,zipfile,tarfile,pathlib\n" +
    "archive,member,destination=sys.argv[1:]\n" +
    "if zipfile.is_zipfile(archive):\n" +
    "    with zipfile.ZipFile(archive) as z: data=z.read(member)\n" +
    "else:\n" +
    "    with tarfile.open(archive) as t: data=t.extractfile(member).read()\n" +
    "pathlib.Path(destination).write_bytes(data)\n", [file, member, destination]);
}
