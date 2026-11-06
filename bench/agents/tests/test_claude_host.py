"""Tests for claude_host.py. No test calls a real model or touches the real Claude login.

Run from the repository root with Harbor's Python (it has no pytest, so this is plain unittest):

    "$(dirname "$(readlink -f "$(command -v harbor)")")/python" -m unittest bench.agents.tests.test_claude_host -v

Two kinds of test:
  * A fake `claude` executable with scripted output covers exit paths. It talks to the REAL sandbox_mcp.py and bridge.
  * `RealCliIsolationTest` runs the REAL `claude` binary with a throwaway HOME, a dummy API key and a local fake
    Anthropic API, and inspects the requests the CLI sends: the strongest available evidence for what a model can see.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

# Harbor imports LiteLLM, which otherwise tries to download a price list on import.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from harbor.agents.installed.base import NonZeroAgentExitCodeError  # noqa: E402
from harbor.models.agent.context import AgentContext  # noqa: E402
from harbor.models.trajectories import Trajectory  # noqa: E402

from bench.agents import claude_host  # noqa: E402
from bench.agents.claude_host import (  # noqa: E402
    ALLOWED_TOOL,
    AgentStartupError,
    ApiConnectionError,
    ApiInternalServerError,
    ApiRateLimitError,
    ApiUsageLimitError,
    AuthenticationError,
    ClaudeHostAgent,
    ContextWindowExceededError,
    ModelNotFoundError,
    PermissionDeniedError,
    child_env,
    classify_failure,
    model_matches,
    usage_from_events,
)

MODEL = "claude-test-model"
SECRET_CANARY = "secret-canary-person@example.invalid"

FAKE_CLAUDE = r'''#!__PYTHON__
"""Scripted stand-in for the `claude` CLI. Runs the real MCP server named in --mcp-config."""
import json, os, subprocess, sys, time

out_dir = os.environ["FAKE_CLAUDE_DIR"]
scenario = os.environ.get("FAKE_CLAUDE_SCENARIO", "success")
argv = sys.argv[1:]

if argv[:1] == ["--version"]:
    print("9.8.7 (Claude Code)")
    sys.exit(0)
if argv[:2] == ["auth", "status"]:
    if scenario == "auth_status_garbage":
        print("not json")
        sys.exit(0)
    logged_in = scenario != "not_logged_in"
    print(json.dumps({"loggedIn": logged_in, "authMethod": "claude.ai" if logged_in else "none", "apiProvider": "firstParty",
                      "email": "__SECRET__", "orgName": "__SECRET__", "subscriptionType": "pro"}))
    sys.exit(0 if logged_in else 1)

json.dump({"argv": argv, "env": dict(os.environ), "cwd": os.getcwd(), "cwd_entries": os.listdir("."),
           "stdin": sys.stdin.read(), "pid": os.getpid()}, open(f"{out_dir}/invocation.json", "w"))
model = argv[argv.index("--model") + 1]
config = json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"]["sandbox"]
USAGE = {"input_tokens": 10, "cache_read_input_tokens": 3, "cache_creation_input_tokens": 2, "output_tokens": 7}

def emit(event):
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()

def init(tools=("mcp__sandbox__bash",), status="connected", model_name=None, servers=None):
    emit({"type": "system", "subtype": "init", "session_id": "sess-1", "tools": list(tools), "permissionMode": "dontAsk",
          "mcp_servers": servers or [{"name": "sandbox", "status": status}], "model": model_name or model,
          "apiKeySource": "none", "claude_code_version": "9.8.7"})

def assistant(msg_id, content, usage=USAGE, model_name=None, **extra):
    emit({"type": "assistant", "timestamp": "2026-01-01T00:00:00.000Z",
          "message": {"id": msg_id, "model": model_name or model, "role": "assistant", "content": content, "usage": usage}, **extra})

def tool_result(call_id, text, is_error=False):
    emit({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": call_id, "content": text, "is_error": is_error}]}})

def result(subtype="success", is_error=False, text="done", **extra):
    fields = {"type": "result", "subtype": subtype, "is_error": is_error, "result": text, "num_turns": 2, "duration_ms": 5,
              "duration_api_ms": 3, "total_cost_usd": 0.5, "permission_denials": [],
              "usage": USAGE, "modelUsage": {model: {"inputTokens": 20, "cacheReadInputTokens": 6, "cacheCreationInputTokens": 4,
                                                     "outputTokens": 14, "costUSD": 0.5}}}
    fields.update(extra)
    emit(fields)

class Mcp:
    def __init__(self):
        self.proc = subprocess.Popen([config["command"], *config["args"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.next_id = 0
        self.rpc("initialize", {"protocolVersion": "2025-06-18"})

    def rpc(self, method, params):
        self.next_id += 1
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params}) + "\n")
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())["result"]

    def bash(self, command):
        return self.rpc("tools/call", {"name": "bash", "arguments": {"command": command}})["content"][0]["text"]

def tool_step(command, msg_id="m1", call_id="c1"):
    assistant(msg_id, [{"type": "text", "text": "running"},])
    assistant(msg_id, [{"type": "tool_use", "id": call_id, "name": "mcp__sandbox__bash", "input": {"command": command}}])
    output = Mcp().bash(command)
    open(f"{out_dir}/tool_output.txt", "w").write(output)
    tool_result(call_id, output)

def finish_ok():
    assistant("m2", [{"type": "text", "text": "All done."}])
    result(text="All done.")

if scenario in ("success", "bridge_output"):
    init()
    tool_step(os.environ.get("FAKE_CLAUDE_COMMAND", "echo hi"))
    finish_ok()
elif scenario == "nonzero":
    init()
    print("boom on stderr", file=sys.stderr)
    sys.exit(3)
elif scenario in ("auth_failed", "usage_limit", "rate_limited", "server_error"):
    error = {"auth_failed": "authentication_failed", "usage_limit": "billing_error", "rate_limited": "rate_limit", "server_error": "server_error"}[scenario]
    init()
    assistant("m1", [{"type": "text", "text": "API Error"}], model_name="<synthetic>", error=error)
    result(subtype="success", is_error=True, text="API Error")
    sys.exit(1)
elif scenario == "model_not_found":
    init()
    result(subtype="error_during_execution", is_error=True, text="", api_error_status=404, usage={}, modelUsage={})
    sys.exit(1)
elif scenario == "context_full":
    init()
    result(subtype="success", is_error=True, text="Prompt is too long")
    sys.exit(1)
elif scenario == "network_down":
    init()
    print("connect ECONNREFUSED 1.2.3.4:443", file=sys.stderr)
    sys.exit(1)
elif scenario == "permission_denied":
    init()
    assistant("m1", [{"type": "tool_use", "id": "c1", "name": "mcp__sandbox__bash", "input": {"command": "x"}}])
    tool_result("c1", "Permission denied", is_error=True)
    assistant("m2", [{"type": "text", "text": "gave up"}])
    result(permission_denials=[{"tool_name": "mcp__sandbox__bash", "tool_use_id": "c1", "tool_input": {}}])
elif scenario == "hallucinated_tool":
    init()
    assistant("m1", [{"type": "tool_use", "id": "c1", "name": "Bash", "input": {"command": "x"}}])
    tool_result("c1", "<tool_use_error>Error: No such tool available: Bash.</tool_use_error>", is_error=True)
    finish_ok()
elif scenario == "disallowed_tool_ran":
    init()
    assistant("m1", [{"type": "tool_use", "id": "c1", "name": "Bash", "input": {"command": "x"}}])
    tool_result("c1", "it ran on the host")
    time.sleep(60)
elif scenario == "extra_tool":
    init(tools=("Bash", "mcp__sandbox__bash"))
    open(f"{out_dir}/alive_after_init", "w").close()
    time.sleep(60)
elif scenario == "extra_mcp":
    init(servers=[{"name": "sandbox", "status": "connected"}, {"name": "other", "status": "connected"}])
    time.sleep(60)
elif scenario == "mcp_failed":
    init(status="failed")
    time.sleep(60)
elif scenario == "model_mismatch_init":
    init(model_name="claude-other-model")
    time.sleep(60)
elif scenario == "model_mismatch_later":
    init()
    assistant("m1", [{"type": "text", "text": "hi"}], model_name="claude-other-model")
    time.sleep(60)
elif scenario == "garbage":
    print("this is not json")
    print("[1, 2, 3]")
    print('{"type": "assistant", "message": {"content": 5}}')
    init()
    finish_ok()
elif scenario == "garbage_only":
    print("this is not json")
elif scenario == "no_result_exit0":
    init()
elif scenario == "oversized_line":
    init()
    sys.stdout.write("x" * int(os.environ["FAKE_CLAUDE_LINE_BYTES"]) + "\n")
    finish_ok()
elif scenario == "usage_missing":
    init()
    assistant("m1", [{"type": "text", "text": "ok"}], usage=None)
    result(usage=None, modelUsage=None, total_cost_usd=None)
elif scenario == "usage_incomplete":
    init()
    assistant("m1", [{"type": "text", "text": "ok"}])
    result(modelUsage={model: {"inputTokens": 20, "outputTokens": 14}})
elif scenario == "hang":
    init()
    assistant("m1", [{"type": "text", "text": "thinking"}])
    assistant("m1", [{"type": "text", "text": "still"}])
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"], stdout=subprocess.DEVNULL)
    open(f"{out_dir}/child_pid", "w").write(str(child.pid))
    time.sleep(300)
elif scenario == "hang_ignores_sigterm":
    import signal
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    init()
    time.sleep(300)
elif scenario == "child_holds_pipes":
    init()
    finish_ok()
    # The leader exits while a child keeps stdout and stderr open.
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    open(f"{out_dir}/child_pid", "w").write(str(child.pid))
else:
    sys.exit(f"unknown scenario {scenario}")
'''


class FakeEnvironment:
    """Stands in for Harbor's sandbox. Runs nothing: commands are only recorded."""

    def __init__(self, stdout: str = "sandbox-output", stderr: str = "", return_code: int = 0, delay: float = 0, error: Exception | None = None):
        self.calls: list[dict] = []
        self.result = SimpleNamespace(stdout=stdout, stderr=stderr, return_code=return_code)
        self.delay = delay
        self.error = error
        self.cancelled = False

    async def exec(self, command, env=None, timeout_sec=None, **kwargs):
        self.calls.append({"command": command, "env": env, "timeout_sec": timeout_sec})
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.error:
            raise self.error
        return self.result


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still accepts signal 0 until its parent reaps it; it is not running.
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


class FakeCliCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="cct-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fake_dir = self.tmp / "fake"
        self.fake_dir.mkdir()
        self.logs = self.tmp / "logs"
        self.bin = self.tmp / "claude"
        self.bin.write_text(FAKE_CLAUDE.replace("__PYTHON__", sys.executable).replace("__SECRET__", SECRET_CANARY))
        self.bin.chmod(self.bin.stat().st_mode | stat.S_IXUSR)
        self.context = AgentContext()

    def agent(self, scenario: str, model: str | None = MODEL, env: dict[str, str] | None = None, **kwargs) -> ClaudeHostAgent:
        patcher = mock.patch.dict(os.environ, {"FAKE_CLAUDE_DIR": str(self.fake_dir), "FAKE_CLAUDE_SCENARIO": scenario, **(env or {})})
        patcher.start()
        self.addCleanup(patcher.stop)
        return ClaudeHostAgent(logs_dir=self.logs, model_name=model, claude_bin=str(self.bin), **kwargs)

    async def run_scenario(self, scenario: str, environment: FakeEnvironment | None = None, env: dict[str, str] | None = None, **kwargs) -> FakeEnvironment:
        environment = environment or FakeEnvironment()
        await self.agent(scenario, env=env, **kwargs).run("Fix the bug.", environment, self.context)
        return environment

    async def assert_fails(self, scenario: str, error: type[Exception], kind: str, env: dict[str, str] | None = None, within: float = 30) -> Exception:
        started = time.monotonic()
        with self.assertRaises(error) as caught:
            await self.run_scenario(scenario, env=env)
        self.assertIs(type(caught.exception), error, "Harbor's error policy keys on the exact class name")
        self.assertEqual(self.context.metadata["failure_kind"], kind)
        self.assertLess(time.monotonic() - started, within)
        return caught.exception

    def invocation(self) -> dict:
        return json.loads((self.fake_dir / "invocation.json").read_text())

    def tool_call_log(self) -> list[dict]:
        path = self.logs / "sandbox-tool-calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


class NormalRunTest(FakeCliCase):
    async def test_flags_reach_the_cli(self) -> None:
        await self.run_scenario("success", reasoning_effort="low")
        argv = self.invocation()["argv"]

        def value(flag: str) -> str:
            return argv[argv.index(flag) + 1]

        self.assertEqual(value("--model"), MODEL)
        self.assertEqual(value("--tools"), "")
        self.assertEqual(value("--allowedTools"), ALLOWED_TOOL)
        self.assertEqual(value("--permission-mode"), "dontAsk")
        self.assertEqual(value("--permission-prompts"), "none")
        self.assertEqual(value("--setting-sources"), "")
        self.assertEqual(value("--output-format"), "stream-json")
        self.assertEqual(value("--effort"), "low")
        for flag in ("-p", "--verbose", "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence", "--no-chrome"):
            self.assertIn(flag, argv)
        joined = " ".join(argv)
        for forbidden in ("--dangerously-skip-permissions", "--allow-dangerously-skip-permissions", "bypassPermissions", "--fallback-model", "--bare"):
            self.assertNotIn(forbidden, joined)
        self.assertEqual(list(json.loads(value("--mcp-config"))["mcpServers"]), ["sandbox"])

    async def test_prompt_has_the_instruction_and_goes_through_stdin(self) -> None:
        await self.run_scenario("success")
        invocation = self.invocation()
        self.assertIn("Fix the bug.", invocation["stdin"])
        self.assertIn(ALLOWED_TOOL, invocation["stdin"])
        self.assertNotIn("sandbox-bash", invocation["stdin"])
        self.assertNotIn("Fix the bug.", " ".join(invocation["argv"]))

    async def test_commands_go_through_the_bridge_and_not_the_host(self) -> None:
        marker = self.tmp / "must-stay-absent"
        command = f"touch {marker}"
        environment = await self.run_scenario("success", env={"FAKE_CLAUDE_COMMAND": command})
        self.assertEqual([c["command"] for c in environment.calls], [command])
        self.assertFalse(marker.exists(), "the command ran on the host")
        self.assertIn("sandbox-output", (self.fake_dir / "tool_output.txt").read_text())
        self.assertEqual([c["command"] for c in self.tool_call_log()], [command])
        self.assertEqual(self.context.metadata["n_sandbox_calls"], 1)

    async def test_cwd_is_empty_and_removed_afterwards(self) -> None:
        await self.run_scenario("success")
        invocation = self.invocation()
        self.assertEqual(invocation["cwd_entries"], [])
        self.assertTrue(invocation["cwd"].startswith("/tmp/cch-"))
        self.assertFalse(Path(invocation["cwd"]).parent.exists())

    async def test_usage_and_metadata(self) -> None:
        await self.run_scenario("success")
        self.assertEqual(self.context.n_input_tokens, 30)  # 20 uncached + 6 cache read + 4 cache write
        self.assertEqual(self.context.n_output_tokens, 14)
        self.assertEqual(self.context.n_cache_tokens, 6)
        self.assertEqual(self.context.model_usage[MODEL].n_input_tokens, 30)
        metadata = self.context.metadata
        self.assertIsNone(self.context.cost_usd, "a list-price estimate must not be reported as cost")
        self.assertEqual(metadata["cli_list_price_estimate_usd_not_charged"], 0.5)
        self.assertEqual(metadata["cache_write_tokens"], 4)
        self.assertEqual(metadata["usage_source"], "result.modelUsage")
        self.assertIs(metadata["usage_is_partial"], False)
        self.assertEqual(metadata["requested_model"], MODEL)
        self.assertEqual(metadata["init_model"], MODEL)
        self.assertEqual(metadata["init_tools"], [ALLOWED_TOOL])
        self.assertEqual(metadata["claude_version"], "9.8.7")
        self.assertIsNone(metadata["failure_kind"])

    async def test_trajectory_is_valid_atif(self) -> None:
        await self.run_scenario("success")
        data = json.loads((self.logs / "trajectory.json").read_text())
        trajectory = Trajectory(**data)
        self.assertEqual(trajectory.agent.name, "claude-host")
        self.assertEqual([s.source for s in trajectory.steps], ["user", "agent", "agent"])
        tool_step = trajectory.steps[1]
        self.assertEqual(tool_step.message, "running", "two events of one message merge into one step")
        self.assertEqual(tool_step.tool_calls[0].function_name, ALLOWED_TOOL)
        self.assertIn("sandbox-output", tool_step.observation.results[0].content)
        self.assertEqual(tool_step.metrics.prompt_tokens, 15)
        self.assertEqual(trajectory.final_metrics.total_prompt_tokens, 30)
        self.assertIsNone(trajectory.final_metrics.total_cost_usd)

    async def test_logs_are_kept_and_hold_no_account_details_or_env_values(self) -> None:
        with mock.patch.dict(os.environ, {"HTTPS_PROXY": "http://user:proxy-password-canary@proxy:1"}):
            await self.run_scenario("success")
            self.assertIn("proxy-password-canary", self.invocation()["env"]["HTTPS_PROXY"], "the proxy must still reach the CLI")
        for name in ("claude-events.jsonl", "claude-stderr.txt", "claude-prompt.txt", "claude-run-config.json", "claude-final-message.txt", "trajectory.json"):
            self.assertTrue((self.logs / name).exists(), name)
        written = " ".join(p.read_text(errors="replace") for p in self.logs.iterdir() if p.is_file())
        written += json.dumps(self.context.metadata)
        self.assertNotIn(SECRET_CANARY, written)
        self.assertNotIn("proxy-password-canary", written)
        self.assertEqual(self.context.metadata["auth_status"]["authMethod"], "claude.ai")
        self.assertNotIn("email", self.context.metadata["auth_status"])

    async def test_model_name_is_required(self) -> None:
        agent = self.agent("success", model=None)
        with self.assertRaisesRegex(ValueError, "needs a model"):
            await agent.run("x", FakeEnvironment(), self.context)
        self.assertFalse((self.fake_dir / "invocation.json").exists())

    async def test_a_model_that_names_an_unavailable_tool_stays_a_valid_run(self) -> None:
        await self.run_scenario("hallucinated_tool")
        self.assertEqual(self.context.metadata["n_unavailable_tool_attempts"], 1)
        self.assertIsNone(self.context.metadata["failure_kind"])


class FailureTest(FakeCliCase):
    async def test_nonzero_exit(self) -> None:
        exc = await self.assert_fails("nonzero", NonZeroAgentExitCodeError, "cli_error")
        self.assertIn("boom on stderr", str(exc))
        self.assertEqual(self.context.metadata["exit_code"], 3)

    async def test_not_logged_in_stops_before_any_run(self) -> None:
        await self.assert_fails("not_logged_in", AuthenticationError, "auth")
        self.assertFalse((self.fake_dir / "invocation.json").exists(), "the task must not start without a login")

    async def test_unreadable_auth_status(self) -> None:
        await self.assert_fails("auth_status_garbage", AgentStartupError, "cli_startup")
        self.assertFalse((self.fake_dir / "invocation.json").exists())

    async def test_missing_binary(self) -> None:
        self.bin.unlink()
        await self.assert_fails("success", AgentStartupError, "cli_startup")

    async def test_authentication_failure_during_run(self) -> None:
        await self.assert_fails("auth_failed", AuthenticationError, "auth")

    async def test_permission_denied(self) -> None:
        exc = await self.assert_fails("permission_denied", PermissionDeniedError, "permission_denied")
        self.assertIn(ALLOWED_TOOL, str(exc))
        self.assertEqual(self.context.metadata["n_permission_denied"], 1)

    async def test_provider_failures_are_told_apart(self) -> None:
        cases = [
            ("usage_limit", ApiUsageLimitError, "usage_limit"),
            ("rate_limited", ApiRateLimitError, "rate_limit"),
            ("server_error", ApiInternalServerError, "api_server"),
            ("model_not_found", ModelNotFoundError, "model_not_found"),
            ("context_full", ContextWindowExceededError, "context_window"),
            ("network_down", ApiConnectionError, "api_connection"),
        ]
        for scenario, error, kind in cases:
            with self.subTest(scenario):
                await self.assert_fails(scenario, error, kind)

    async def test_extra_tool_stops_the_cli_at_init(self) -> None:
        exc = await self.assert_fails("extra_tool", PermissionDeniedError, "tool_policy_violation", within=20)
        self.assertIn("Bash", str(exc))
        self.assertFalse(pid_alive(self.invocation()["pid"]))

    async def test_extra_mcp_server_stops_the_cli(self) -> None:
        await self.assert_fails("extra_mcp", PermissionDeniedError, "tool_policy_violation", within=20)

    async def test_a_disallowed_tool_that_ran_stops_the_cli(self) -> None:
        await self.assert_fails("disallowed_tool_ran", PermissionDeniedError, "tool_policy_violation", within=20)

    async def test_mcp_connection_failure_is_a_startup_failure(self) -> None:
        await self.assert_fails("mcp_failed", AgentStartupError, "mcp_startup", within=20)

    async def test_another_model_is_never_accepted(self) -> None:
        for scenario in ("model_mismatch_init", "model_mismatch_later"):
            with self.subTest(scenario):
                exc = await self.assert_fails(scenario, ModelNotFoundError, "model_mismatch", within=20)
                self.assertIn("claude-other-model", str(exc))

    async def test_failed_sandbox_command_is_reported_to_the_model_and_the_run_continues(self) -> None:
        environment = FakeEnvironment(error=OSError("container gone"))
        await self.run_scenario("bridge_output", environment=environment)
        self.assertIn("sandbox exec failed", (self.fake_dir / "tool_output.txt").read_text())
        self.assertEqual(self.context.metadata["n_sandbox_calls_failed"], 1)


class OutputHandlingTest(FakeCliCase):
    async def test_garbage_lines_are_counted_and_do_not_end_the_run(self) -> None:
        await self.run_scenario("garbage")
        self.assertEqual(self.context.metadata["n_malformed_output_lines"], 2)  # not JSON objects; the third is an empty message
        self.assertEqual(self.context.metadata["final_message"], "All done.")
        trajectory = Trajectory(**json.loads((self.logs / "trajectory.json").read_text()))
        self.assertEqual([s.source for s in trajectory.steps], ["user", "agent"])

    async def test_output_with_no_events_is_a_failure(self) -> None:
        await self.assert_fails("garbage_only", NonZeroAgentExitCodeError, "malformed_output")

    async def test_exit_zero_without_a_result_is_a_failure(self) -> None:
        await self.assert_fails("no_result_exit0", NonZeroAgentExitCodeError, "malformed_output")

    async def test_oversized_line_is_skipped(self) -> None:
        with mock.patch.object(claude_host, "MAX_EVENT_LINE_BYTES", 100_000):
            await self.run_scenario("oversized_line", env={"FAKE_CLAUDE_LINE_BYTES": "500000"})
        self.assertEqual(self.context.metadata["n_malformed_output_lines"], 1)
        self.assertEqual(self.context.metadata["final_message"], "All done.")

    async def test_log_size_is_capped(self) -> None:
        with mock.patch.object(claude_host, "MAX_LOG_BYTES", 2000):
            await self.run_scenario("oversized_line", env={"FAKE_CLAUDE_LINE_BYTES": "50000"})
        self.assertIs(self.context.metadata["log_truncated"], True)
        self.assertLessEqual((self.logs / "claude-events.jsonl").stat().st_size, 2000)
        self.assertEqual(self.context.metadata["final_message"], "All done.", "parsing continues past the cap")

    async def test_large_sandbox_output_is_truncated_by_the_mcp_server(self) -> None:
        await self.run_scenario("bridge_output", environment=FakeEnvironment(stdout="y" * 200_000))
        text = (self.fake_dir / "tool_output.txt").read_text()
        self.assertLess(len(text), 13_000)
        self.assertIn("characters omitted", text)

    async def test_missing_usage_stays_unknown(self) -> None:
        await self.run_scenario("usage_missing")
        self.assertIsNone(self.context.n_input_tokens)
        self.assertIsNone(self.context.n_output_tokens)
        self.assertIsNone(self.context.n_cache_tokens)
        self.assertIsNone(self.context.model_usage)
        self.assertIsNone(self.context.metadata["usage_source"])
        self.assertIsNone(self.context.metadata["cli_list_price_estimate_usd_not_charged"])
        self.assertIsNone(Trajectory(**json.loads((self.logs / "trajectory.json").read_text())).final_metrics.total_prompt_tokens)

    async def test_incomplete_usage_is_not_filled_with_zero(self) -> None:
        await self.run_scenario("usage_incomplete")
        self.assertIsNone(self.context.n_input_tokens, "cache counts are missing, so the total is unknown")
        self.assertIsNone(self.context.n_cache_tokens)
        self.assertEqual(self.context.n_output_tokens, 14)
        self.assertEqual(self.context.metadata["uncached_input_tokens"], 20)
        self.assertIsNone(self.context.model_usage)


class TimeoutTest(FakeCliCase):
    async def wait_for_file(self, path: Path) -> None:
        for _ in range(200):
            if path.exists():
                return
            await asyncio.sleep(0.05)
        self.fail(f"{path} did not appear")

    async def test_timeout_cleans_up_and_keeps_what_was_recorded(self) -> None:
        agent = self.agent("hang")
        task = asyncio.create_task(agent.run("x", FakeEnvironment(), self.context))
        await self.wait_for_file(self.fake_dir / "child_pid")
        await asyncio.sleep(0.2)
        task.cancel()  # what Harbor's asyncio.wait_for does at the agent timeout
        with self.assertRaises(asyncio.CancelledError):
            await task
        invocation = self.invocation()
        child_pid = int((self.fake_dir / "child_pid").read_text())
        self.assertFalse(pid_alive(invocation["pid"]), "the CLI is still running")
        self.assertFalse(pid_alive(child_pid), "a process started by the CLI is still running")
        self.assertFalse(Path(invocation["cwd"]).parent.exists(), "the temp dir was left behind")
        metadata = self.context.metadata
        self.assertEqual(metadata["failure_kind"], "timeout_or_cancelled")
        self.assertEqual(metadata["usage_source"], "assistant-events")
        self.assertIs(metadata["usage_is_partial"], True)
        self.assertEqual(self.context.n_input_tokens, 15, "one message, counted once despite two events")
        self.assertEqual(self.context.n_output_tokens, 7)
        self.assertIsNone(self.context.cost_usd)
        self.assertIn("thinking", (self.logs / "claude-events.jsonl").read_text())
        Trajectory(**json.loads((self.logs / "trajectory.json").read_text()))

    async def test_a_cli_ignoring_sigterm_is_killed(self) -> None:
        agent = self.agent("hang_ignores_sigterm")
        with mock.patch.object(claude_host, "TERMINATE_GRACE_SEC", 0.5):
            task = asyncio.create_task(agent.run("x", FakeEnvironment(), self.context))
            await self.wait_for_file(self.logs / "claude-events.jsonl")
            await self.wait_for_file(self.fake_dir / "invocation.json")
            await asyncio.sleep(0.3)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(pid_alive(self.invocation()["pid"]))

    async def test_a_child_holding_the_pipes_cannot_hang_the_run(self) -> None:
        with mock.patch.object(claude_host, "DRAIN_TIMEOUT_SEC", 5):
            started = time.monotonic()
            await self.run_scenario("child_holds_pipes")
        self.assertLess(time.monotonic() - started, 15)
        self.assertFalse(pid_alive(int((self.fake_dir / "child_pid").read_text())))

    async def test_sandbox_command_in_flight_is_cancelled(self) -> None:
        # `success` blocks on its one tool call; the bridge must drop it when the run is cancelled.
        environment = FakeEnvironment(delay=300)
        agent = self.agent("success", env={"FAKE_CLAUDE_COMMAND": "sleep 300"})
        task = asyncio.create_task(agent.run("x", environment, self.context))
        for _ in range(200):
            if environment.calls:
                break
            await asyncio.sleep(0.05)
        self.assertTrue(environment.calls, "the command never reached the sandbox")
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(environment.cancelled)


class ChildEnvTest(unittest.TestCase):
    def test_session_and_account_overrides_are_dropped(self) -> None:
        env = child_env({
            "PATH": "/bin", "HOME": "/h", "HTTPS_PROXY": "p", "NODE_EXTRA_CA_CERTS": "c",
            "CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "s", "CLAUDE_CODE_ENTRYPOINT": "e", "CLAUDE_EFFORT": "max",
            "CLAUDE_CODE_MESSAGING_TOKEN": "t", "CLAUDE_CODE_USE_BEDROCK": "1", "ANTHROPIC_API_KEY": "k", "ANTHROPIC_MODEL": "m",
            "ANTHROPIC_BASE_URL": "u", "MCP_CONNECTION_NONBLOCKING": "1", "OTEL_EXPORTER_OTLP_ENDPOINT": "o", "VSCODE_IPC_HOOK_CLI": "v",
            "NODE_OPTIONS": "--require=/x.js",
        })
        self.assertEqual(env["PATH"], "/bin")
        self.assertEqual(env["HTTPS_PROXY"], "p")
        self.assertEqual(env["NODE_EXTRA_CA_CERTS"], "c")
        for name in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_EFFORT", "CLAUDE_CODE_MESSAGING_TOKEN",
                     "CLAUDE_CODE_USE_BEDROCK", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "ANTHROPIC_BASE_URL", "MCP_CONNECTION_NONBLOCKING",
                     "OTEL_EXPORTER_OTLP_ENDPOINT", "VSCODE_IPC_HOOK_CLI", "NODE_OPTIONS"):
            self.assertNotIn(name, env)

    def test_login_location_survives(self) -> None:
        env = child_env({"CLAUDE_CONFIG_DIR": "/c", "CLAUDE_CODE_OAUTH_TOKEN": "t"})
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], "/c")
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "t")

    def test_isolation_switches_cannot_be_overridden_by_the_parent(self) -> None:
        env = child_env({"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "0", "ENABLE_TOOL_SEARCH": "true", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "0"})
        self.assertEqual(env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"], "1")
        self.assertEqual(env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"], "1")
        self.assertEqual(env["ENABLE_TOOL_SEARCH"], "false")


class HelperTest(unittest.TestCase):
    def test_model_matches(self) -> None:
        for requested, actual, expected in [
            ("claude-opus-5-5", "claude-opus-5-5", True),
            ("claude-opus-5-5", "claude-opus-5-5-20260101", True),
            ("claude-opus-5-5-20260101", "claude-opus-5-5", True),
            ("claude-opus-5-5[1m]", "claude-opus-5-5", True),
            ("opus", "claude-opus-5-5", True),
            ("sonnet", "claude-sonnet-5-5[1m]", True),
            ("claude-opus-5-5", "claude-sonnet-5-5", False),
            ("claude-opus-5", "claude-opus-5-5", True),
            ("claude-opus-5-5", "claude-opus-5-55", False),
            ("opus", "claude-sonnet-5-5", False),
            ("best", "claude-opus-5-5", None),
        ]:
            with self.subTest(requested=requested, actual=actual):
                self.assertIs(model_matches(requested, actual), expected)

    def test_classify_prefers_structured_signals(self) -> None:
        self.assertEqual(classify_failure("rate limit", "authentication_failed")[0], "auth")
        self.assertEqual(classify_failure("rate limit", None, 401)[0], "auth")
        self.assertEqual(classify_failure("", None, 403)[1], PermissionDeniedError)
        self.assertEqual(classify_failure("", None, 529)[0], "api_server")
        self.assertEqual(classify_failure("Not logged in · Please run /login")[0], "auth")
        self.assertEqual(classify_failure("something else")[1], NonZeroAgentExitCodeError)

    def test_usage_prefers_model_usage_and_counts_cache_in_input(self) -> None:
        tokens = usage_from_events(
            {"modelUsage": {"a": {"inputTokens": 1, "cacheReadInputTokens": 2, "cacheCreationInputTokens": 3, "outputTokens": 4},
                            "b": {"inputTokens": 10, "cacheReadInputTokens": 20, "cacheCreationInputTokens": 30, "outputTokens": 40}}},
            {},
        )
        self.assertEqual((tokens["input"], tokens["cache_read"], tokens["cache_write"], tokens["output"]), (66, 22, 33, 44))

    def test_no_usage_at_all_is_none(self) -> None:
        self.assertIsNone(usage_from_events(None, {}))
        self.assertIsNone(usage_from_events({"usage": {}, "modelUsage": {}}, {}))


class _FakeAnthropic(BaseHTTPRequestHandler):
    """Local stand-in for the Messages API. Scripted: run one sandbox command, try a host tool, then finish."""

    requests: list[dict] = []
    has_real_looking_auth: list[str] = []

    def log_message(self, *args) -> None:
        pass

    def do_HEAD(self) -> None:
        self.send_response(200)
        self.end_headers()

    def do_GET(self) -> None:
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        type(self).requests.append(body)
        type(self).has_real_looking_auth.append(self.headers.get("x-api-key") or "")
        n_turns = sum(1 for m in body.get("messages", []) if m["role"] == "assistant")
        if n_turns == 0:
            block = {"type": "tool_use", "id": "toolu_1", "name": ALLOWED_TOOL, "input": {"command": type(self).sandbox_command}}
        elif n_turns == 1:
            block = {"type": "tool_use", "id": "toolu_2", "name": "Bash", "input": {"command": f"touch {type(self).host_marker}"}}
        else:
            block = {"type": "text", "text": "finished"}
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        self.wfile.write(self._stream(n_turns, block))

    @staticmethod
    def _stream(n: int, block: dict) -> bytes:
        usage = {"input_tokens": 10, "output_tokens": 1, "cache_read_input_tokens": 3, "cache_creation_input_tokens": 2}
        start = dict(block, **({"input": {}} if block["type"] == "tool_use" else {"text": ""}))
        delta = (
            {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
            if block["type"] == "tool_use" else {"type": "text_delta", "text": block["text"]}
        )
        events = [
            ("message_start", {"type": "message_start", "message": {
                "id": f"msg_{n}", "type": "message", "role": "assistant", "model": MODEL, "content": [],
                "stop_reason": None, "stop_sequence": None, "usage": usage}}),
            ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": start}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": delta}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta", "delta": {
                "stop_reason": "tool_use" if block["type"] == "tool_use" else "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 7}}),
            ("message_stop", {"type": "message_stop"}),
        ]
        return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode()


class _OfflineClaudeHostAgent(ClaudeHostAgent):
    """The real CLI and real isolation settings, but a throwaway HOME, a dummy key and the local fake API."""

    offline_env: dict[str, str] = {}

    def _child_env(self) -> dict[str, str]:
        return {**child_env({"PATH": os.environ["PATH"]}), **self.offline_env}


CANARIES = ("CANARY_USER_CLAUDE_MD", "CANARY_PARENT_CLAUDE_MD", "CANARY_SKILL", "CANARY_AGENT", "CANARY_HOOK_OUTPUT")


@unittest.skipUnless(shutil.which("claude"), "the claude CLI is not installed")
class RealCliIsolationTest(unittest.IsolatedAsyncioTestCase):
    """What the real CLI actually sends to a model. Uses no real login and reaches no real API."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ccr-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        claude_dir = self.home / ".claude"
        (claude_dir / "skills/canaryskill").mkdir(parents=True)
        (claude_dir / "agents").mkdir()
        (claude_dir / "CLAUDE.md").write_text("CANARY_USER_CLAUDE_MD\n")
        (self.tmp / "CLAUDE.md").write_text("CANARY_PARENT_CLAUDE_MD\n")
        (claude_dir / "skills/canaryskill/SKILL.md").write_text("---\nname: canaryskill\ndescription: CANARY_SKILL\n---\nbody\n")
        (claude_dir / "agents/canaryagent.md").write_text("---\nname: canaryagent\ndescription: CANARY_AGENT\n---\nbody\n")
        self.hook_marker = self.tmp / "hook-ran"
        (claude_dir / "settings.json").write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [
            {"type": "command", "command": f"touch {self.hook_marker}; echo CANARY_HOOK_OUTPUT"}]}]}}))
        (self.home / ".claude.json").write_text(json.dumps({
            "hasCompletedOnboarding": True, "mcpServers": {"canarymcp": {"type": "stdio", "command": "/bin/true"}}}))

        _FakeAnthropic.requests = []
        _FakeAnthropic.has_real_looking_auth = []
        self.host_marker = self.tmp / "host-tool-ran"
        self.sandbox_marker = self.tmp / "sandbox-command-ran-on-host"
        _FakeAnthropic.host_marker = self.host_marker
        _FakeAnthropic.sandbox_command = f"touch {self.sandbox_marker}"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeAnthropic)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.offline_env = {
            "HOME": str(self.home), "ANTHROPIC_API_KEY": "dummy-not-a-key",
            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{self.server.server_address[1]}",
            "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1",
        }
        patcher = mock.patch.object(_OfflineClaudeHostAgent, "offline_env", self.offline_env)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_the_model_sees_only_the_sandbox_tool_and_no_user_configuration(self) -> None:
        environment = FakeEnvironment(stdout="ran-in-fake-sandbox")
        agent = _OfflineClaudeHostAgent(logs_dir=self.tmp / "logs", model_name=MODEL)
        context = AgentContext()
        await asyncio.wait_for(agent.run("Fix the bug.", environment, context), timeout=120)

        self.assertTrue(_FakeAnthropic.requests)
        self.assertEqual(set(_FakeAnthropic.has_real_looking_auth), {"dummy-not-a-key"}, "this test must never send a real login")
        for request in _FakeAnthropic.requests:
            self.assertEqual([t.get("name") for t in request.get("tools", [])], [ALLOWED_TOOL])
            self.assertEqual(request["model"], MODEL)
            text = json.dumps(request)
            for canary in (*CANARIES, "canarymcp"):
                self.assertNotIn(canary, text, f"{canary} reached the model")
        self.assertFalse(self.hook_marker.exists(), "a user hook ran")
        self.assertEqual([c["command"] for c in environment.calls], [_FakeAnthropic.sandbox_command])
        self.assertFalse(self.sandbox_marker.exists(), "the sandbox command ran on the host")
        self.assertFalse(self.host_marker.exists(), "the host Bash tool ran")

        metadata = context.metadata
        self.assertIsNone(metadata["failure_kind"])
        self.assertEqual(metadata["init_tools"], [ALLOWED_TOOL])
        self.assertEqual(metadata["init_mcp_servers"][0]["status"], "connected")
        self.assertEqual(metadata["n_unavailable_tool_attempts"], 1)
        self.assertEqual(metadata["n_sandbox_calls"], 1)
        self.assertEqual(metadata["n_permission_denied"], 0)
        self.assertEqual(context.n_input_tokens, 45)  # 3 turns * (10 uncached + 3 cache read + 2 cache write)
        self.assertEqual(context.n_output_tokens, 21)
        self.assertEqual(context.n_cache_tokens, 9)
        self.assertIsNone(context.cost_usd)
        trajectory = Trajectory(**json.loads((self.tmp / "logs/trajectory.json").read_text()))
        self.assertEqual(trajectory.final_metrics.total_prompt_tokens, 45)
        self.assertEqual(trajectory.steps[1].tool_calls[0].function_name, ALLOWED_TOOL)
        self.assertIn("ran-in-fake-sandbox", trajectory.steps[1].observation.results[0].content)

        # The real CLI must match the fake CLI's event assumptions: one denied-by-absence tool, never a transcript.
        projects = self.home / ".claude/projects"
        self.assertEqual(list(projects.rglob("*.jsonl")) if projects.exists() else [], [], "--no-session-persistence was ignored")

    def test_control_without_isolation_the_canaries_do_reach_the_model(self) -> None:
        """Without this, the absence of canaries above could just mean the canaries do not work."""
        cwd = self.tmp / "cwd"
        cwd.mkdir()
        subprocess.run(
            ["claude", "-p", "--model", MODEL, "--output-format", "stream-json", "--verbose", "--permission-mode", "dontAsk"],
            input="hello", cwd=cwd, capture_output=True, text=True, timeout=120,
            env={"PATH": os.environ["PATH"], "NO_COLOR": "1", **self.offline_env},
        )
        self.assertTrue(_FakeAnthropic.requests)
        request = _FakeAnthropic.requests[0]
        text = json.dumps(request)
        for canary in CANARIES:
            self.assertIn(canary, text, f"{canary} does not reach the model even without isolation")
        self.assertIn("Bash", [t.get("name") for t in request.get("tools", [])])
        self.assertTrue(self.hook_marker.exists())


if __name__ == "__main__":
    unittest.main()
