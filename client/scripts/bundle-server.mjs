// Copy the Python language server into the extension so the .vsix is self-contained.
import fs from "fs";
import path from "path";

const here = path.dirname(new URL(import.meta.url).pathname);
const src = path.resolve(here, "..", "..", "server", "uvmls");
const dst = path.resolve(here, "..", "server", "uvmls");
fs.rmSync(path.dirname(dst), { recursive: true, force: true });
fs.cpSync(src, dst, { recursive: true, filter: (p) => !p.includes("__pycache__") });
console.log(`bundled ${src} -> ${dst}`);
