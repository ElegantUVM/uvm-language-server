"""The optional Verible process and transport normalization, independent of the index."""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
from dataclasses import dataclass

from .fixes import EOL_RE
from .project import expand_path, norm

TIMEOUT = 5.0
CLEANUP_TIMEOUT = 1.0


@dataclass(frozen=True)
class Settings:
    enabled: bool = True
    path: str = ""

    @classmethod
    def from_settings(cls, settings: dict) -> Settings:
        cfg = settings.get("formatter")
        cfg = cfg if isinstance(cfg, dict) else {}
        path = cfg.get("path", "")
        return cls(cfg.get("enabled") is not False, path if isinstance(path, str) else "")


def executable(path: str, roots: list[str]) -> str | None:
    if not path:
        found = shutil.which("verible-verilog-format")
        return norm(found) if found else None
    expanded = expand_path(path, roots, relative=False)
    # Classify before normalization removes ./, including in expanded variables.
    if not os.path.dirname(expanded):
        found = shutil.which(expanded)
        return norm(found) if found else None
    if not os.path.isabs(expanded) and not roots:
        return None
    expanded = os.path.normpath(expand_path(path, roots))
    return expanded if os.path.isfile(expanded) and os.access(expanded, os.X_OK) else None


def working_directory(path: str) -> str:
    """The document's directory, or its nearest existing ancestor: an open buffer outlives a
    directory removed under it, and Verible reads the buffer from stdin."""
    directory = os.path.dirname(path)
    while not os.path.isdir(directory) and os.path.dirname(directory) != directory:
        directory = os.path.dirname(directory)
    return directory


def normalize(source: str, output: bytes) -> str:
    text = output.decode("utf-8")
    if not text:
        raise ValueError("empty output for a nonempty document")
    text = EOL_RE.sub("\n", text)
    if source.endswith("\n"):
        if not text.endswith("\n"):
            text += "\n"
    else:
        text = text.rstrip("\n")
    if not text:
        raise ValueError("empty output for a nonempty document")
    return text.replace("\n", "\r\n") if "\r\n" in source else text


def kill(proc):
    try:
        if os.name == "posix":
            # A wrapper can exit while its descendants still own the output pipes.
            os.killpg(proc.pid, signal.SIGKILL)
        elif proc.returncode is None:
            proc.kill()
    except ProcessLookupError:
        pass


async def reap(proc, communication):
    kill(proc)
    waiters = [proc.wait()]
    if communication is not None:
        waiters.append(communication)
    try:
        results = await asyncio.wait_for(asyncio.gather(*waiters), CLEANUP_TIMEOUT)
        return results[1][1] if communication is not None else b""
    except asyncio.TimeoutError:
        # Detached descendants can escape the group. Process has no public pipe-close API;
        # closing its transport releases our pipes and leaves child reaping to the watcher.
        proc._transport.close()
        return b""


class Formatter:
    def __init__(self, log):
        self.log = log
        self.missing: set[tuple] = set()
        self.tasks: set[asyncio.Task] = set()
        self.cleaning: set[asyncio.Task] = set()
        self.processes: set[asyncio.subprocess.Process] = set()
        self.stopped = False

    def stop(self):
        self.stopped = True
        for proc in self.processes:
            kill(proc)
        for task in self.tasks:
            if task not in self.cleaning:
                task.cancel()

    async def shutdown(self):
        self.stop()
        tasks = list(self.tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def run(self, source: str, path: str, size: int, settings: Settings, roots: list[str],
                  lines: tuple[int, int] | None = None) -> str | None:
        if self.stopped or not settings.enabled or not source or type(size) is not int or size <= 0:
            return None
        endings = {m.group() for m in EOL_RE.finditer(source)}
        if len(endings) > 1 or "\r" in endings:
            self.log("formatter: mixed or unsupported line endings; no edits returned")
            return None
        try:
            data = source.encode("utf-8")
        except UnicodeEncodeError as e:  # a lone surrogate the client sent as a \uXXXX escape
            line = source.count("\n", 0, e.start) + 1
            self.log(f"formatter: line {line} holds a lone UTF-16 surrogate, which is not valid UTF-8; "
                     "no edits returned")
            return None
        binary = executable(settings.path, roots)
        if binary is None:
            key = (settings, tuple(roots), os.environ.get("PATH"), expand_path(settings.path, roots))
            if key not in self.missing:
                self.missing.add(key)
                self.log(f"formatter: cannot find executable {settings.path or 'verible-verilog-format'!r}; "
                         "set uvm.formatter.path or install verible-verilog-format on the server's PATH")
            return None
        argv = [binary, "--failsafe_success=false", f"--indentation_spaces={size}"]
        if lines is not None:
            argv.append(f"--lines={lines[0]}-{lines[1]}")
        argv.append("-")
        task = asyncio.current_task()
        self.tasks.add(task)
        proc, communication = None, None
        timed_out = False
        # Shield startup so cancellation cannot lose a child created before the await resumes.
        spawn = asyncio.ensure_future(asyncio.create_subprocess_exec(
            *argv, cwd=working_directory(path), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name == "posix"))
        try:
            proc = await asyncio.shield(spawn)
            self.processes.add(proc)
            communication = asyncio.ensure_future(proc.communicate(data))
            try:
                out, err = await asyncio.wait_for(asyncio.shield(communication), TIMEOUT)
            except asyncio.TimeoutError:
                timed_out = True
                return None
            if proc.returncode:
                self.failure(binary, f"exited with status {proc.returncode}", err)
                return None
            try:
                result = normalize(source, out)
            except UnicodeDecodeError:
                self.failure(binary, "output is not UTF-8", err)
                return None
            except ValueError as e:
                self.failure(binary, str(e), err)
                return None
            if err.strip():
                self.failure(binary, "stderr", err)
            return result
        except OSError as e:
            self.failure(binary, f"cannot start: {e}")
            return None
        finally:
            self.cleaning.add(task)
            try:
                if proc is None:
                    try:
                        proc = await spawn
                    except OSError:
                        pass
                if proc is not None:
                    err = await reap(proc, communication)
                    self.processes.discard(proc)
                    if timed_out:
                        self.failure(binary, "took longer than 5 s and was stopped", err)
            finally:
                self.tasks.discard(task)
                self.cleaning.discard(task)

    def failure(self, binary: str, reason: str, stderr: bytes = b""):
        excerpt = stderr[:4000].decode("utf-8", errors="replace").strip()
        self.log(f"formatter {binary!r}: {reason}" + (f": {excerpt}" if excerpt else ""))
