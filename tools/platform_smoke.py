"""Exercise a platform package's first-run install with no package-index access."""
import argparse
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import venv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, choices=["linux-x64", "win32-x64", "darwin-arm64", "darwin-x64"])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    bundle = root / "client" / "server"
    host = {("linux", "x86_64"): "linux-x64", ("win32", "amd64"): "win32-x64",
            ("darwin", "arm64"): "darwin-arm64", ("darwin", "x86_64"): "darwin-x64"}
    if host.get((sys.platform, platform.machine().lower())) != args.target:
        parser.error(f"{args.target} must be smoke-tested on its native runner")
    with tempfile.TemporaryDirectory(prefix="uvmls first run ") as work:
        venv.EnvBuilder(with_pip=True).create(work)
        python = Path(work) / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        env = dict(os.environ, PYTHONPATH=str(bundle))
        env.pop("UVM_HOME", None)
        def run(*argv):
            subprocess.run([str(arg) for arg in argv], cwd=bundle, env=env, check=True, timeout=180)
        run(python, "-m", "pip", "install", "--no-index", "--find-links", bundle / "wheels", "pyslang>=12", "pygls>=2,<3")
        run(python, "-m", "uvmls", "--version")
        example = root / "examples" / "alu_tb"
        run(python, "-m", "uvmls", "lint", example, "-f", example / "alu_tb.f")
        binary = "verible-verilog-format.exe" if sys.platform == "win32" else "verible-verilog-format"
        if args.target != "darwin-x64":
            run(bundle / "uvmls" / "third_party" / "verible" / args.target / binary, "--version")
        else:
            print("darwin-x64 ships wheels only; set uvm.formatter.path to an external Verible")


if __name__ == "__main__":
    main()
