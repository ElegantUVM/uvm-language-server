"""A small MCP client over stdio, for the tests and for checking a server by hand.

    python tools/mcp_smoke.py [--cwd DIR] [--env NAME=VALUE ...] [--protocol V] [--list]
                              [--call TOOL [JSON]] [--read URI] -- COMMAND ARGS...

    python tools/mcp_smoke.py --call topology '{"test": "alu_error_test"}' -- \\
        .venv/bin/python -m uvmls mcp examples/alu_tb -f alu_tb.f

The command after `--` is started as a subprocess with the given working directory and environment
(entries added to this process's). The client does the initialize handshake, then runs the actions
in the order given (tools/list by default) and prints each result as JSON on a line of its own.
The exit status is 1 when the server answered an error or an isError result. As a module, `Smoke`
gives request(), notify(), call(), data() and read_resource(), and keeps the server's stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading

DEFAULT_PROTOCOL = "2025-06-18"


class Smoke:
    def __init__(self, command: list[str], cwd: str | None = None, env: dict | None = None,
                 timeout: float = 120.0):
        self.proc = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.timeout = timeout
        self.next_id = 0
        self.messages: queue.Queue = queue.Queue()
        self.others: list = []  # messages that answered no request of ours
        self._stderr: list[bytes] = []
        threading.Thread(target=self._pump, daemon=True).start()
        threading.Thread(target=self._drain, daemon=True).start()

    def _pump(self):
        try:
            for line in self.proc.stdout:
                try:
                    self.messages.put(json.loads(line))
                except ValueError:
                    self.messages.put(ValueError(f"not JSON: {line[:200]!r}"))
        finally:
            self.messages.put(EOFError("the server closed its stdout"))

    def _drain(self):
        for chunk in iter(lambda: self.proc.stderr.read(4096), b""):
            self._stderr.append(chunk)

    @property
    def stderr(self) -> str:
        return b"".join(self._stderr).decode(errors="replace")

    def send_raw(self, text: str):
        self.proc.stdin.write(text.encode("utf-8") + b"\n")
        self.proc.stdin.flush()

    def send(self, message) -> None:
        self.send_raw(json.dumps(message))

    def read(self, timeout: float | None = None):
        """The next message from the server; raises when it exits or sends no JSON."""
        try:
            msg = self.messages.get(timeout=self.timeout if timeout is None else timeout)
        except queue.Empty:
            raise TimeoutError("no message from the server") from None
        if isinstance(msg, Exception):
            raise msg
        return msg

    def request(self, method: str, params=None, timeout: float | None = None) -> dict:
        """The server's whole reply (`result` or `error`) to a request."""
        self.next_id += 1
        rid = self.next_id
        message = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            message["params"] = params
        self.send(message)
        while True:
            msg = self.read(timeout)
            if isinstance(msg, dict) and msg.get("id") == rid and "method" not in msg:
                return msg
            self.others.append(msg)

    def result(self, method: str, params=None, timeout: float | None = None):
        reply = self.request(method, params, timeout)
        if "error" in reply:
            raise RuntimeError(f"{method}: {reply['error']}")
        return reply["result"]

    def notify(self, method: str, params=None):
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self.send(message)

    def initialize(self, protocol: str = DEFAULT_PROTOCOL) -> dict:
        reply = self.result("initialize", {"protocolVersion": protocol, "capabilities": {},
                                           "clientInfo": {"name": "mcp_smoke", "version": "1"}})
        self.notify("notifications/initialized")
        return reply

    def call(self, tool: str, arguments: dict | None = None, timeout: float | None = None) -> dict:
        """A tool's result object (content, structuredContent, isError)."""
        return self.result("tools/call", {"name": tool, "arguments": arguments or {}}, timeout)

    def data(self, tool: str, arguments: dict | None = None, timeout: float | None = None):
        """A tool's structured result; an isError result raises with its text."""
        found = self.call(tool, arguments, timeout)
        if found.get("isError"):
            raise RuntimeError(f"{tool}: {found['content'][0]['text']}")
        return found["structuredContent"]

    def error_text(self, tool: str, arguments: dict | None = None) -> str:
        """The text of a tool's isError result; a success raises."""
        found = self.call(tool, arguments)
        if not found.get("isError"):
            raise AssertionError(f"{tool} succeeded: {found}")
        return found["content"][0]["text"]

    def read_resource(self, uri: str) -> dict:
        return self.result("resources/read", {"uri": uri})

    def close(self, timeout: float = 10.0) -> int:
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            return self.proc.wait()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cwd", help="working directory for the server")
    ap.add_argument("--env", action="append", default=[], metavar="NAME=VALUE",
                    help="environment entry for the server, added to this process's")
    ap.add_argument("--protocol", default=DEFAULT_PROTOCOL, help="protocol version to ask for")
    ap.add_argument("--list", action="store_true", help="print tools/list")
    ap.add_argument("--call", action="append", default=[], nargs="+", metavar=("TOOL", "JSON"),
                    help="call a tool with JSON arguments (default {})")
    ap.add_argument("--read", action="append", default=[], metavar="URI", help="read a resource")
    ap.add_argument("--timeout", type=float, default=120.0, help="seconds to wait for each reply")
    ap.add_argument("command", nargs="+", help="the server command (after --)")
    args = ap.parse_args(argv)
    env = dict(os.environ)
    for entry in args.env:
        name, _, value = entry.partition("=")
        env[name] = value
    client = Smoke(args.command, cwd=args.cwd, env=env, timeout=args.timeout)
    failed = False

    def show(label, reply):
        nonlocal failed
        failed = failed or "error" in reply or bool((reply.get("result") or {}).get("isError"))
        print(json.dumps({label: reply.get("result", reply.get("error"))}, indent=1))

    try:
        show("initialize", client.request("initialize", {"protocolVersion": args.protocol, "capabilities": {},
                                                         "clientInfo": {"name": "mcp_smoke", "version": "1"}}))
        client.notify("notifications/initialized")
        if args.list or not (args.call or args.read):
            show("tools/list", client.request("tools/list"))
        for call in args.call:
            arguments = json.loads(call[1]) if len(call) > 1 else {}
            show(f"tools/call {call[0]}", client.request("tools/call", {"name": call[0], "arguments": arguments}))
        for uri in args.read:
            show(f"resources/read {uri}", client.request("resources/read", {"uri": uri}))
    except (EOFError, ValueError, TimeoutError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        failed = True
    finally:
        status = client.close()
        if client.stderr.strip():
            print(client.stderr.rstrip(), file=sys.stderr)
    return 1 if failed or status else 0


if __name__ == "__main__":
    sys.exit(main())
