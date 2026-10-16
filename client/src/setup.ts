// The pure parts of the first-run Python setup, kept free of the vscode API so tests can load them.

/** The server's Python dependencies, as pip requirement strings. */
export const REQUIREMENTS = ["pyslang>=12", "pygls>=2,<3"];

/**
 * Interpreters to try, in order, for creating the extension's private environment: the generic name
 * first, then versioned ones. Enterprise Linux 8 and 9 (the usual verification farm) keep `python3`
 * at 3.6/3.9 and ship 3.11+ only as `python3.11`, `python3.12`.
 */
export function basePythonNames(platform: string = process.platform): string[] {
  if (platform === "win32") {
    return ["py", "python"];
  }
  return ["python3", "python3.14", "python3.13", "python3.12", "python3.11", "python"];
}

/** The launcher needs a version switch; carry it through both probing and venv creation. */
export function basePythonCommands(platform: string = process.platform): { command: string; args: string[] }[] {
  return basePythonNames(platform).flatMap((command) => command === "py"
    ? ["3.14", "3.13", "3.12", "3.11"].map((version) => ({ command, args: [`-${version}`] }))
    : [{ command, args: [] }]);
}

export function venvBinary(platform: string = process.platform): string {
  return platform === "win32" ? "Scripts\\python.exe" : "bin/python";
}

/**
 * The pip commands to run, in order, until one succeeds: from the wheels a platform package ships
 * (no network at all), then from the index pip is configured for, with the user's extra arguments
 * (a mirror's --index-url, a --proxy, --trusted-host).
 */
export function installPlan(wheels: string | undefined, pipArgs: readonly string[]): string[][] {
  const plan: string[][] = [];
  if (wheels) {
    plan.push(["-m", "pip", "install", "--no-index", "--find-links", wheels, ...REQUIREMENTS]);
  }
  plan.push(["-m", "pip", "install", ...pipArgs, ...REQUIREMENTS]);
  return plan;
}
