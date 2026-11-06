"""Harbor external agent: the HOST's GitHub Copilot CLI, working inside the sandbox through one MCP tool.

Why this exists (reports/phase0.md §3.4 and F8-F12): Harbor's own `copilot-cli` agent runs the CLI
*inside* the sandbox and therefore needs a GitHub token placed there. This agent keeps the Copilot login on the
host (the CLI uses its stored login, no key is typed or copied) and lets the model act in the sandbox only via
a `bash` tool that is bridged to `environment.exec`:

    copilot (host) --MCP stdio--> sandbox_mcp.py --unix socket--> this agent --environment.exec--> sandbox

Isolation guarantees (each one verified in Phase 0, see the report):
  * `--available-tools sandbox-bash`: the model sees exactly one tool. Without it the model also sees the host's
    built-in bash / view / edit / web_fetch / session_store_sql ... (hidden tests live on the host).
  * the working directory of the host process is an empty temp dir; custom instructions, memory, built-in MCP
    servers, remote control and auto-update are disabled.
  * the sandbox needs no network at all (the model runs on the host), so `[agent] network_mode = "allowlist"`
    with no hosts is enough.

Usage (from the repository root):
    PYTHONPATH=$PWD harbor run -p tasks/dev/smoke-fix-calc -a bench.agents.copilot_host:CopilotHostAgent -m claude-haiku-4.5

Cost is reported as Copilot premium requests (`context.metadata["premium_requests"]`); there is no USD figure.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, override

from harbor.agents.base import BaseAgent
from harbor.agents.capabilities import AgentCapabilities
from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext, ModelUsage

MCP_SCRIPT = Path(__file__).resolve().with_name("sandbox_mcp.py")
PROMPT_VERSION = "1"
PROMPT_TEMPLATE = """\
You are an autonomous software engineering agent. Complete the task below.

Environment:
- You work inside a remote Linux sandbox that holds the task's files. You can act ONLY through the \
`sandbox-bash` tool: every command you run executes in that sandbox.
- Each call starts a fresh non-interactive bash shell in the sandbox's default working directory; `cd`, shell \
variables and background processes do not persist between calls. Chain dependent commands with `&&`.
- You have no other tools and no access to anything outside the sandbox. Nobody will answer questions, so make \
reasonable decisions on your own.
- Inspect the files first, make the change, then verify it yourself by running the relevant code or tests.
- When you have finished, reply with a short summary of what you did.

Task:
{instruction}
"""

# Environment variables that bind a child CLI to the session that launched this process (COPILOT_*), or that
# could send telemetry somewhere else (OTEL_*). Only the login location and the file exporter are re-added.
_DROP_ENV_PREFIXES = ("COPILOT_", "OTEL_")
_KEEP_ENV = {"COPILOT_HOME", "COPILOT_GITHUB_TOKEN"}


# The names below are matched by Harbor's error policy (harbor/models/trial/error_policy.py), which keys on
# the exception class name: provider-side failures must not be scored as agent failures.
class AuthenticationError(RuntimeError):
    pass


class PermissionDeniedError(RuntimeError):
    pass


class ModelNotFoundError(RuntimeError):
    pass


class ApiRateLimitError(RuntimeError):
    pass


class ApiConnectionError(RuntimeError):
    pass


def classify_failure(text: str) -> type[Exception]:
    lowered = text.lower()
    if "no authentication information" in lowered or "not authenticated" in lowered or "bad credentials" in lowered:
        return AuthenticationError
    if "model" in lowered and any(p in lowered for p in ("not found", "not available", "unknown model", "invalid model", "not supported")):
        return ModelNotFoundError
    if any(p in lowered for p in ("rate limit", "too many requests", "429", "quota")):
        return ApiRateLimitError
    if any(p in lowered for p in ("policy", "forbidden", "403", "not enabled", "disabled by")):
        return PermissionDeniedError
    if any(p in lowered for p in ("econnreset", "etimedout", "enotfound", "econnrefused", "fetch failed", "network", "socket hang up")):
        return ApiConnectionError
    return NonZeroAgentExitCodeError


def parse_usage_file(path: Path) -> dict[str, Any] | None:
    """Read the CLI's own end-of-run statistics (`--usage-output-file`).

    `inputTokens` already includes the cached tokens (uncached + cache read + cache write), which is Harbor's
    convention for `n_input_tokens`. The file only exists after a normal exit.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    per_model: dict[str, dict[str, int]] = {}
    for name, metrics in (data.get("modelMetrics") or {}).items():
        usage = metrics.get("usage") or {}
        per_model[name] = {
            "input": int(usage.get("inputTokens") or 0),
            "output": int(usage.get("outputTokens") or 0),
            "cache_read": int(usage.get("cacheReadTokens") or 0),
            "cache_write": int(usage.get("cacheWriteTokens") or 0),
            "calls": int((metrics.get("requests") or {}).get("count") or 0),
        }
    if not per_model:
        return None
    totals = {key: sum(m[key] for m in per_model.values()) for key in ("input", "output", "cache_read", "cache_write", "calls")}
    return {
        **totals,
        "per_model": per_model,
        "nano_aiu": data.get("totalNanoAiu"),
        "premium": data.get("totalPremiumRequestCost"),
    }


def parse_otel_usage(path: Path) -> dict[str, Any] | None:
    """Fallback when the usage file is missing (e.g. the CLI was killed): sum Copilot's OpenTelemetry `chat` spans.

    Per call the CLI reports input/output/reasoning tokens and `nano_aiu` (usage-based credits). It reports no
    cache split per call. `github.copilot.cost` (premium requests) repeats on every call of one interaction and
    only the user-initiated call counts, so it is summed over `initiator == "user"` spans.
    """
    if not path.exists():
        return None
    usage = {"calls": 0, "input": 0, "output": 0, "reasoning": 0, "nano_aiu": 0.0, "premium": 0.0}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            span = json.loads(line)
        except json.JSONDecodeError:
            continue
        attrs = span.get("attributes") if isinstance(span, dict) and span.get("type") == "span" else None
        if not attrs or attrs.get("gen_ai.operation.name") != "chat":
            continue
        usage["calls"] += 1
        usage["input"] += int(attrs.get("gen_ai.usage.input_tokens") or 0)
        usage["output"] += int(attrs.get("gen_ai.usage.output_tokens") or 0)
        usage["reasoning"] += int(attrs.get("gen_ai.usage.reasoning.output_tokens") or 0)
        usage["nano_aiu"] += float(attrs.get("github.copilot.nano_aiu") or 0)
        if attrs.get("github.copilot.initiator") == "user":
            usage["premium"] += float(attrs.get("github.copilot.cost") or 0)
    return usage if usage["calls"] else None


class _Bridge:
    """Unix-socket server that runs the model's `bash` calls in the sandbox via `environment.exec`."""

    def __init__(self, environment: BaseEnvironment, call_log: Path):
        self._environment = environment
        self._call_log = call_log
        self._server: asyncio.AbstractServer | None = None
        self.n_calls = 0
        self.n_failed = 0
        self.seconds = 0.0

    async def start(self, path: str) -> None:
        self._server = await asyncio.start_unix_server(self._handle, path=path)
        os.chmod(path, 0o600)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        reply: dict[str, Any]
        command = ""
        started = time.monotonic()
        try:
            request = json.loads(await reader.readline())
            command = str(request["command"])
            timeout_sec = max(1, min(int(request.get("timeout_sec") or 120), 900))
            try:
                result = await self._environment.exec(
                    command=command,
                    env={"PAGER": "cat", "GIT_PAGER": "cat", "DEBIAN_FRONTEND": "noninteractive"},
                    timeout_sec=timeout_sec,
                )
                output = result.stdout or ""
                if result.stderr:
                    output += ("\n" if output and not output.endswith("\n") else "") + result.stderr
                reply = {"return_code": result.return_code, "output": output}
            except (asyncio.TimeoutError, TimeoutError):
                reply = {"timed_out": True, "return_code": -1, "output": ""}
            except RuntimeError as exc:
                # Harbor's Docker environment reports a command timeout as RuntimeError("Command timed out ...").
                if "timed out" not in str(exc).lower():
                    raise
                reply = {"timed_out": True, "return_code": -1, "output": ""}
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # report to the model instead of crashing the trial
            reply = {"return_code": -1, "output": f"sandbox exec failed: {type(exc).__name__}: {exc}"}
        elapsed = time.monotonic() - started
        self.n_calls += 1
        self.seconds += elapsed
        if reply.get("timed_out") or reply.get("return_code") not in (0, None):
            self.n_failed += 1
        try:
            with self._call_log.open("a", encoding="utf-8") as log:
                log.write(json.dumps({
                    "command": command, "return_code": reply.get("return_code"),
                    "timed_out": bool(reply.get("timed_out")), "seconds": round(elapsed, 2),
                    "output_chars": len(reply.get("output", "")),
                }) + "\n")
            writer.write((json.dumps(reply) + "\n").encode("utf-8"))
            await writer.drain()
        finally:
            writer.close()


def child_env(otel_path: Path | None = None) -> dict[str, str]:
    """Environment for the host `copilot` process: no session binding, no foreign telemetry sinks."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key in _KEEP_ENV or not key.startswith(_DROP_ENV_PREFIXES)
    }
    env["NO_COLOR"] = "1"
    if otel_path is not None:
        # Usage goes to a local file only; prompt/response content capture stays off (the CLI default).
        env["COPILOT_OTEL_FILE_EXPORTER_PATH"] = str(otel_path)
        env["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "false"
    return env


def probe_model(model: str, copilot_bin: str = "copilot", timeout_sec: int = 120) -> dict[str, Any]:
    """One minimal prompt with no tools: checks the model id works and measures its premium-request multiplier.

    Costs exactly one multiplier-unit of the user's Copilot quota. The multiplier is what every trial of that
    model costs (premium requests are billed per prompt, not per model call or tool call), so
    `trials * multiplier` is an exact budget. Run it from an empty directory.
    """
    work = Path(tempfile.mkdtemp(prefix="cbh-probe-", dir="/tmp"))
    try:
        proc = subprocess.run(
            [
                shutil.which(copilot_bin) or copilot_bin, "-p", "Reply with exactly the two letters OK and nothing else.",
                "--model", model, "--output-format", "json", "--stream", "off",
                "--disable-builtin-mcps", "--no-custom-instructions", "--no-ask-user", "--no-experimental",
                "--no-auto-update", "--no-remote", "--no-remote-export",
            ],
            cwd=work, env=child_env(), stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout_sec,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"ok": False, "model": model, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        shutil.rmtree(work, ignore_errors=True)
    reply, premium, exit_code = "", None, proc.returncode
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "assistant.message":
            reply = (event.get("data") or {}).get("content") or reply
        elif event.get("type") == "result":
            premium = (event.get("usage") or {}).get("premiumRequests")
            exit_code = event.get("exitCode", exit_code)
    ok = exit_code == 0 and "OK" in reply and premium is not None
    return {
        "ok": ok, "model": model, "premium_requests": premium, "reply": reply[:50],
        "error": None if ok else (proc.stderr.strip() or "no result event")[:300],
    }


class CopilotHostAgent(BaseAgent):
    """GitHub Copilot CLI on the host, acting in the sandbox through the bridged `sandbox-bash` tool."""

    capabilities = AgentCapabilities(atif=True)

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        reasoning_effort: str | None = None,
        copilot_bin: str = "copilot",
        **kwargs: Any,
    ):
        super().__init__(logs_dir=logs_dir, model_name=model_name, **kwargs)
        self._reasoning_effort = reasoning_effort
        self._copilot_bin = shutil.which(copilot_bin) or copilot_bin
        self._copilot_version = self._read_copilot_version()

    def _read_copilot_version(self) -> str:
        try:
            out = subprocess.run([self._copilot_bin, "--version"], capture_output=True, text=True, timeout=60).stdout
        except Exception:
            return "unknown"
        match = re.search(r"(\d+\.\d+\.\d+)", out)
        return match.group(1) if match else "unknown"

    @staticmethod
    @override
    def name() -> str:
        return "copilot-host"

    @override
    def version(self) -> str:
        return f"copilot-{self._copilot_version}+bridge-prompt-v{PROMPT_VERSION}"

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        if shutil.which(self._copilot_bin) is None:
            raise FileNotFoundError(f"Copilot CLI not found on the host: {self._copilot_bin!r}")

    def _child_env(self, otel_path: Path) -> dict[str, str]:
        return child_env(otel_path)

    def _build_command(self, prompt: str, mcp_config: dict[str, Any], log_dir: Path, usage_file: Path) -> list[str]:
        if not self.model_name:
            raise ValueError("copilot-host needs a model: pass -m <copilot model id>, e.g. claude-haiku-4.5")
        command = [
            self._copilot_bin, "-p", prompt,
            "--model", self.model_name,
            "--output-format", "json", "--stream", "off",
            "--additional-mcp-config", json.dumps(mcp_config),
            # Visibility: the model may see ONLY the sandbox tool (verified; see module docstring).
            "--available-tools", "sandbox-bash",
            # Permissions (separate from visibility): allow only that tool, explicitly deny host kinds.
            "--allow-tool", "sandbox(bash)",
            "--deny-tool", "shell", "--deny-tool", "write", "--deny-tool", "url",
            "--disable-builtin-mcps", "--no-custom-instructions", "--no-ask-user", "--no-experimental",
            "--no-auto-update", "--no-remote", "--no-remote-export",
            "--log-dir", str(log_dir),
            "--usage-output-file", str(usage_file),
        ]
        if self._reasoning_effort:
            command += ["--reasoning-effort", self._reasoning_effort]
        return command

    @staticmethod
    async def _terminate(proc: asyncio.subprocess.Process | None) -> None:
        """SIGTERM first (the CLI then writes its usage file), SIGKILL if it does not exit promptly."""
        if proc is None or proc.returncode is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=8)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        if proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def _write_atif(self, events_path: Path, logs_dir: Path, usage: dict[str, Any] | None) -> None:
        """Convert the CLI's JSONL events with Harbor's own Copilot converter (same event schema)."""
        from harbor.agents.installed.copilot_cli import CopilotCli

        helper = CopilotCli(logs_dir=logs_dir, model_name=self.model_name)
        helper._version = self._copilot_version
        trajectory = helper._convert_jsonl_to_trajectory(events_path)
        if trajectory is None:
            return
        try:  # the converter labels the agent "copilot-cli"; this one is a different harness
            trajectory.agent.name = self.name()
            trajectory.agent.version = self.version()
        except Exception:
            pass
        if usage and trajectory.final_metrics is not None:
            trajectory.final_metrics.total_prompt_tokens = usage["input"]
            trajectory.final_metrics.total_completion_tokens = usage["output"]
            trajectory.final_metrics.total_cached_tokens = usage.get("cache_read")
        (logs_dir / "trajectory.json").write_text(
            json.dumps(trajectory.to_json_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _finalize(
        self,
        context: AgentContext,
        logs_dir: Path,
        result_event: dict[str, Any] | None,
        final_message: str,
        n_tool_requests: int,
        n_denied: int,
        bridge: _Bridge,
        wall_seconds: float,
    ) -> None:
        result_usage = (result_event or {}).get("usage") or {}
        file_usage = parse_usage_file(logs_dir / "copilot-usage.json")
        otel = parse_otel_usage(logs_dir / "copilot-otel.jsonl")
        tokens = file_usage or (
            {"input": otel["input"], "output": otel["output"], "cache_read": None, "cache_write": None, "calls": otel["calls"],
             "per_model": {}, "nano_aiu": otel["nano_aiu"], "premium": otel["premium"]}
            if otel else None
        )
        if tokens:
            context.n_input_tokens = tokens["input"]
            context.n_output_tokens = tokens["output"]
            context.n_cache_tokens = tokens["cache_read"]
            per_model = tokens["per_model"] or {self.model_name or "unknown": tokens}
            context.model_usage = {
                name: ModelUsage(
                    n_input_tokens=m["input"], n_output_tokens=m["output"], n_cache_tokens=m.get("cache_read") or 0
                )
                for name, m in per_model.items()
            }
        context.cost_usd = None
        premium = result_usage.get("premiumRequests")
        if premium is None and tokens:
            premium = tokens.get("premium")
        context.metadata = {
            "agent": "copilot-host",
            "copilot_version": self._copilot_version,
            "prompt_version": PROMPT_VERSION,
            "model": self.model_name,
            "reasoning_effort": self._reasoning_effort,
            "premium_requests": premium,
            "total_nano_aiu": tokens["nano_aiu"] if tokens else None,
            "cache_write_tokens": tokens["cache_write"] if tokens else None,
            "reasoning_output_tokens": otel["reasoning"] if otel else None,
            "usage_source": "usage-file" if file_usage else ("otel" if otel else None),
            "api_duration_ms": result_usage.get("totalApiDurationMs"),
            "session_duration_ms": result_usage.get("sessionDurationMs"),
            "copilot_session_id": (result_event or {}).get("sessionId"),
            "n_model_calls": tokens["calls"] if tokens else None,
            "n_tool_requests": n_tool_requests,
            "n_sandbox_calls": bridge.n_calls,
            "n_sandbox_calls_failed": bridge.n_failed,
            "sandbox_seconds": round(bridge.seconds, 1),
            "n_permission_denied": n_denied,
            "wall_seconds": round(wall_seconds, 1),
            "final_message": final_message[:2000],
        }
        (logs_dir / "copilot-final-message.txt").write_text(final_message, encoding="utf-8")
        events_path = logs_dir / "copilot-events.jsonl"
        if events_path.exists():
            try:
                self._write_atif(events_path, logs_dir, tokens)
            except Exception as exc:
                self.logger.warning("copilot-host: could not write the ATIF trajectory: %r", exc)

    @override
    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        if not self.model_name:  # fail before any temp dir exists
            raise ValueError("copilot-host needs a model: pass -m <copilot model id>, e.g. claude-haiku-4.5")
        # The child runs in an empty temp dir, so every path handed to it must be absolute.
        logs_dir = self.logs_dir.resolve()
        logs_dir.mkdir(parents=True, exist_ok=True)
        events_path = logs_dir / "copilot-events.jsonl"
        stderr_path = logs_dir / "copilot-stderr.txt"
        otel_path = logs_dir / "copilot-otel.jsonl"
        otel_path.unlink(missing_ok=True)
        work_dir = Path(tempfile.mkdtemp(prefix="cbh-", dir="/tmp"))  # mkdtemp is 0700
        (work_dir / "cwd").mkdir()
        socket_path = str(work_dir / "bridge.sock")
        bridge = _Bridge(environment, logs_dir / "sandbox-tool-calls.jsonl")
        mcp_config = {
            "mcpServers": {
                "sandbox": {"type": "local", "command": sys.executable, "args": [str(MCP_SCRIPT), socket_path], "tools": ["*"]}
            }
        }
        prompt = PROMPT_TEMPLATE.format(instruction=instruction)
        command = self._build_command(prompt, mcp_config, logs_dir / "copilot-logs", logs_dir / "copilot-usage.json")

        proc: asyncio.subprocess.Process | None = None
        started = time.monotonic()
        result_event: dict[str, Any] | None = None
        final_message = ""
        n_tool_requests = 0
        n_denied = 0
        stderr_task: asyncio.Task | None = None
        try:
            await bridge.start(socket_path)
            proc = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(work_dir / "cwd"),
                env=self._child_env(otel_path),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                limit=64 * 1024 * 1024,
            )

            async def pump_stderr() -> None:
                assert proc is not None and proc.stderr is not None
                with stderr_path.open("wb") as sink:
                    while chunk := await proc.stderr.read(65536):
                        sink.write(chunk)
                        sink.flush()

            stderr_task = asyncio.create_task(pump_stderr())
            assert proc.stdout is not None
            with events_path.open("wb") as sink:
                while line := await proc.stdout.readline():
                    sink.write(line)
                    sink.flush()
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    kind = event.get("type")
                    data = event.get("data") or {}
                    if kind == "result":
                        result_event = event
                    elif kind == "assistant.message":
                        if data.get("content"):
                            final_message = data["content"]
                        n_tool_requests += len(data.get("toolRequests") or [])
                    elif kind == "tool.execution_complete" and (data.get("error") or {}).get("code") == "denied":
                        n_denied += 1
            await proc.wait()
            await stderr_task
        finally:
            await self._terminate(proc)
            if stderr_task is not None and not stderr_task.done():
                stderr_task.cancel()
            await bridge.stop()
            shutil.rmtree(work_dir, ignore_errors=True)
            # Runs on every path, including a Harbor timeout/cancellation: a timed-out trial still costs
            # premium requests, so whatever usage was recorded must reach result.json.
            try:
                self._finalize(context, logs_dir, result_event, final_message, n_tool_requests, n_denied, bridge, time.monotonic() - started)
            except Exception as exc:
                self.logger.warning("copilot-host: could not record usage/metadata: %r", exc)

        stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else ""
        exit_code = (result_event or {}).get("exitCode", proc.returncode if proc else None)
        if proc is not None and (proc.returncode != 0 or result_event is None or exit_code not in (0, None)):
            reason = classify_failure(stderr_text + "\n" + final_message)
            raise reason(
                f"copilot exited with code {proc.returncode} (result exitCode={exit_code}); stderr: {stderr_text.strip()[:500]!r}"
            )
        if n_denied:
            raise PermissionDeniedError(
                f"{n_denied} sandbox tool call(s) were denied by the Copilot CLI permission layer; the run is not a valid measurement"
            )


if __name__ == "__main__":  # python -m bench.agents.copilot_host probe <model>
    import argparse

    parser = argparse.ArgumentParser(description="Helpers for the host-side Copilot agent")
    sub = parser.add_subparsers(dest="command", required=True)
    probe_parser = sub.add_parser("probe", help="send one minimal prompt; print the model's premium-request multiplier as JSON")
    probe_parser.add_argument("model")
    cli_args = parser.parse_args()
    outcome = probe_model(cli_args.model)
    print(json.dumps(outcome))
    sys.exit(0 if outcome["ok"] else 1)
