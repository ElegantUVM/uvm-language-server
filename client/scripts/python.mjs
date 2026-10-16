import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

/** Python used by the packaging tools, including zipfile (available on every supported host). */
export function python() {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
  const venv = path.join(root, ".venv", ...(process.platform === "win32" ? ["Scripts", "python.exe"] : ["bin", "python"]));
  return process.env.UVMLS_PYTHON || (fs.existsSync(venv) ? venv : process.platform === "win32" ? "python" : "python3");
}
