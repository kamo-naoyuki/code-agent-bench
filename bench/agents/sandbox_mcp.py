#!/usr/bin/env python3
"""Minimal MCP (stdio) server exposing ONE tool, `bash`, that runs inside the task sandbox.

It is started by the host-side Copilot CLI (see copilot_host.py). It never executes anything
itself: every call is forwarded over a unix socket to the Harbor agent process, which runs it
with `environment.exec(...)` in the sandbox. Standard library only (Harbor's venv has no `mcp`).

Usage: sandbox_mcp.py <bridge-socket-path>
"""
import json
import socket
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

DEFAULT_PROTOCOL = "2025-06-18"
DEFAULT_TIMEOUT_SEC = 120
MAX_TIMEOUT_SEC = 900
MAX_OUTPUT_CHARS = 12000

TOOLS = [
    {
        "name": "bash",
        "description": (
            "Run a bash command inside the task sandbox (a remote Linux container that holds the "
            "task's files). This is the ONLY way to read, change or run anything. Each call starts a "
            "fresh non-interactive shell in the sandbox's default working directory, so `cd`, shell "
            "variables and background processes do not persist between calls. Output is the exit "
            "code followed by combined stdout/stderr (long output is truncated in the middle)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The bash command to run."},
                "timeout_sec": {
                    "type": "integer",
                    "description": f"Optional timeout in seconds (default {DEFAULT_TIMEOUT_SEC}, max {MAX_TIMEOUT_SEC}).",
                },
            },
            "required": ["command"],
        },
    }
]

_write_lock = threading.Lock()


def send(message):
    line = json.dumps(message, separators=(",", ":"))
    with _write_lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def log(text):
    print(f"[sandbox-mcp] {text}", file=sys.stderr, flush=True)


def truncate(text):
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    half = MAX_OUTPUT_CHARS // 2
    omitted = len(text) - 2 * half
    return f"{text[:half]}\n... [{omitted} characters omitted] ...\n{text[-half:]}"


def call_bridge(socket_path, command, timeout_sec):
    request = json.dumps({"command": command, "timeout_sec": timeout_sec}) + "\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(timeout_sec + 60)
        conn.connect(socket_path)
        conn.sendall(request.encode("utf-8"))
        chunks = []
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
            if chunk.endswith(b"\n"):
                break
    return json.loads(b"".join(chunks).decode("utf-8"))


def run_tool(socket_path, arguments):
    command = arguments.get("command")
    if not isinstance(command, str) or not command.strip():
        return "Error: `command` must be a non-empty string.", True
    try:
        timeout_sec = int(arguments.get("timeout_sec") or DEFAULT_TIMEOUT_SEC)
    except (TypeError, ValueError):
        timeout_sec = DEFAULT_TIMEOUT_SEC
    timeout_sec = max(1, min(timeout_sec, MAX_TIMEOUT_SEC))
    try:
        reply = call_bridge(socket_path, command, timeout_sec)
    except Exception as exc:  # bridge gone or protocol error: report to the model, do not crash
        log(f"bridge error: {exc!r}")
        return f"Error: the sandbox is not reachable ({type(exc).__name__}).", True
    if reply.get("timed_out"):
        return f"Command timed out after {timeout_sec} seconds.\n{truncate(reply.get('output', ''))}", True
    code = reply.get("return_code")
    return f"exit code: {code}\n{truncate(reply.get('output', ''))}", False


def handle_call(socket_path, request_id, params):
    name = params.get("name")
    if name != "bash":
        send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": f"unknown tool: {name}"}})
        return
    text, is_error = run_tool(socket_path, params.get("arguments") or {})
    send({
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {"content": [{"type": "text", "text": text}], "isError": is_error},
    })


def main():
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    socket_path = sys.argv[1]
    pool = ThreadPoolExecutor(max_workers=4)
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            log(f"ignoring non-JSON input: {raw[:100]!r}")
            continue
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}
        if method == "initialize":
            protocol = params.get("protocolVersion")
            send({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": protocol if isinstance(protocol, str) else DEFAULT_PROTOCOL,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "sandbox", "version": "1.0.0"},
                },
            })
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": request_id, "result": {}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            pool.submit(handle_call, socket_path, request_id, params)
        elif request_id is not None:
            send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"method not found: {method}"}})
        # notifications (no id), e.g. notifications/initialized, need no reply
    pool.shutdown(wait=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
