#!/usr/bin/env python3
"""Scripted OpenAI-compatible mock LLM for Phase 0 plumbing checks (G2/G3/G7).

This is NOT a model. Replies depend only on the requested model name and on how many
assistant turns are already in the conversation, so every run is deterministic:

  mock-solver  solves the known tasks with a fixed command script, then submits
  mock-lazy    submits immediately without doing any work (expected reward 0)
  mock-loop    never submits (used to exercise step/cost limits)

It speaks the tool-calling dialect that mini-swe-agent v2 expects: one `bash` tool call
per reply. Token usage is reported (rough chars/4 estimate) and every request is logged,
so Harbor's usage aggregation can be cross-checked against what the server actually returned.
"""
import argparse
import json
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SUBMIT = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"

SCRIPTS = {
    "smoke-fix-calc": [
        "cd /app && sed -i 's/range(len(values) - window)/range(len(values) - window + 1)/' calc.py"
        " && rm -f legacy_calc.py && mv helpers_old.py helpers.py"
        " && sed -i 's/from helpers_old import clamp/from helpers import clamp/' calc.py"
        " && python3 -c 'import calc; print(calc.moving_sum([1, 2, 3, 4], 2))'",
        SUBMIT,
    ],
    "hello-world": [
        "printf 'Hello, world!\\n' > /app/hello.txt && cat /app/hello.txt",
        SUBMIT,
    ],
}

# (marker found in the task text) -> script name
TASK_MARKERS = {
    "calc.moving_sum": "smoke-fix-calc",
    "hello.txt": "hello-world",
}

CACHE_HIT_RATIO = 0.5  # report part of the prompt as cached from the 2nd call on


def detect_task(messages):
    text = " ".join(
        m["content"] if isinstance(m.get("content"), str) else json.dumps(m.get("content"))
        for m in messages
        if m.get("role") in ("system", "user")
    )
    for marker, name in TASK_MARKERS.items():
        if marker in text:
            return name
    return None


def choose_command(model, messages):
    turn = sum(1 for m in messages if m.get("role") == "assistant")
    name = model.split("/")[-1]
    if name == "mock-loop":
        return f"echo still working (turn {turn})", "unknown"
    if name == "mock-lazy":
        return SUBMIT, "n/a"
    script_name = detect_task(messages)
    script = SCRIPTS.get(script_name)
    if script is None:
        return SUBMIT, str(script_name)
    return (script[turn] if turn < len(script) else SUBMIT), script_name


def estimate_tokens(text):
    return max(1, math.ceil(len(text) / 4))


class Handler(BaseHTTPRequestHandler):
    server_version = "mock-llm/0.1"

    def log_message(self, fmt, *args):  # keep stdout quiet; requests go to the JSONL log
        pass

    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/health", "/v1/health"):
            self._send(200, {"status": "ok"})
        elif self.path in ("/v1/models", "/models"):
            data = [{"id": m, "object": "model"} for m in ("mock-solver", "mock-lazy", "mock-loop")]
            self._send(200, {"object": "list", "data": data})
        else:
            self._send(404, {"error": {"message": f"not found: {self.path}"}})

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send(404, {"error": {"message": f"not found: {self.path}"}})
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            req = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"error": {"message": "invalid JSON"}})
            return
        if req.get("stream"):
            self._send(400, {"error": {"message": "stream=true is not supported by the mock"}})
            return

        model = req.get("model", "mock-lazy")
        messages = req.get("messages", [])
        command, script_name = choose_command(model, messages)
        content = f"Running scripted step for {script_name}."
        arguments = json.dumps({"command": command})

        with self.server.lock:
            self.server.n_calls += 1
            call_no = self.server.n_calls
        prompt_tokens = sum(
            estimate_tokens(m["content"] if isinstance(m.get("content"), str) else json.dumps(m.get("content")))
            for m in messages
        )
        completion_tokens = estimate_tokens(content + arguments)
        has_history = any(m.get("role") == "assistant" for m in messages)
        cached = int(prompt_tokens * CACHE_HIT_RATIO) if has_history else 0

        response = {
            "id": f"chatcmpl-mock-{call_no}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": content,
                        "tool_calls": [
                            {
                                "id": f"call_mock_{call_no}",
                                "type": "function",
                                "function": {"name": "bash", "arguments": arguments},
                            }
                        ],
                    },
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "prompt_tokens_details": {"cached_tokens": cached},
            },
        }
        self._send(200, response)

        if self.server.log_path:
            record = {
                "ts": time.time(),
                "call_no": call_no,
                "client": self.client_address[0],
                "model": model,
                "task": script_name,
                "n_messages": len(messages),
                "command": command,
                "usage": response["usage"],
            }
            with self.server.lock, open(self.server.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="172.17.0.1", help="bind address (default: docker0 gateway, not 0.0.0.0)")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--log", default=None, help="JSONL request log")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.lock = threading.Lock()
    server.n_calls = 0
    server.log_path = args.log
    print(f"mock LLM listening on http://{args.host}:{args.port}/v1", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
