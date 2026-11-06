"""Harbor external agent: the HOST's Claude Code CLI, working inside the sandbox through one MCP tool.

    claude (host) --MCP stdio--> sandbox_mcp.py --unix socket--> this agent --environment.exec--> sandbox

The login stays on the host: the CLI reads its own stored login, nothing is copied or shown, and nothing is
passed to the sandbox. See CLAUDE_USAGE.md for how to run it and IMPLEMENTATION_RULES.md for the requirements.

Isolation has two layers, because a flag that silently stops working must not produce a "valid" result:
  * Configuration (`_build_command`, `child_env`). `--tools ""` removes every built-in tool, `--strict-mcp-config`
    drops other MCP servers, `--setting-sources ""` drops user/project/local settings (hooks, plugins, ...),
    `--disable-slash-commands` drops skills, CLAUDE_CODE_DISABLE_CLAUDE_MDS / _AUTO_MEMORY drop instruction files
    and memory. Permissions are NOT bypassed: `dontAsk` denies anything outside `--allowedTools`.
  * Runtime check (`_StreamState`). The CLI reports its tools in the `init` event, before the first model reply. If it lists
    anything but the one sandbox tool the CLI is stopped at once and the trial fails as a policy violation.

`--bare` is not used: it refuses OAuth logins (see `claude --help`), so it cannot use the host login.
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, override

from harbor.agents.base import BaseAgent
from harbor.agents.capabilities import AgentCapabilities
from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext, ModelUsage
from harbor.models.trajectories import (
    Agent,
    FinalMetrics,
    Metrics,
    Observation,
    ObservationResult,
    Step,
    ToolCall,
    Trajectory,
)

# Internal names of the Copilot agent, reused unchanged so both agents share one bridge and one task prompt.
from bench.agents.copilot_host import (
    MCP_SCRIPT,
    PROMPT_TEMPLATE as _COPILOT_PROMPT_TEMPLATE,
    PROMPT_VERSION,
    ApiConnectionError,
    ApiRateLimitError,
    AuthenticationError,
    ModelNotFoundError,
    PermissionDeniedError,
    _Bridge,
)

MCP_SERVER_NAME = "sandbox"
ALLOWED_TOOL = f"mcp__{MCP_SERVER_NAME}__bash"
PROMPT_TEMPLATE = _COPILOT_PROMPT_TEMPLATE.replace("sandbox-bash", ALLOWED_TOOL)

TERMINATE_GRACE_SEC = 8
DRAIN_TIMEOUT_SEC = 10
EXIT_POLL_SEC = 0.1
AUTH_STATUS_TIMEOUT_SEC = 60
MAX_LOG_BYTES = 512 * 1024 * 1024
MAX_EVENT_LINE_BYTES = 64 * 1024 * 1024
FINAL_MESSAGE_METADATA_CHARS = 2000
# Above sandbox_mcp.py's own limit (MAX_TIMEOUT_SEC + 60 s), so that limit is the one that applies.
MCP_TOOL_TIMEOUT_MS = 1_000_000

# Variables that bind the child to the session that launched this process, or that change its model, account,
# endpoint, tools or telemetry. Dropping them is what makes the run independent of the launching shell.
_DROP_ENV_PREFIXES = (
    "ANTHROPIC_", "CLAUDE_", "CLAUDECODE", "OTEL_", "MCP_", "VSCODE_", "ENABLE_", "DISABLE_", "VERTEX_REGION_",
)
_DROP_ENV_NAMES = {"NODE_OPTIONS", "API_TIMEOUT_MS", "MAX_MCP_OUTPUT_TOKENS", "CLOUD_ML_REGION"}
# Only the login's location and the CLI's own login token variable survive. ANTHROPIC_API_KEY deliberately does
# not: it would silently replace the host login with API billing.
_KEEP_ENV = {"CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN"}
_FORCED_ENV = {
    "NO_COLOR": "1",
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    # Without this an MCP tool can be deferred behind ToolSearch, which `--tools ""` removes.
    "ENABLE_TOOL_SEARCH": "false",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "DISABLE_AUTOUPDATER": "1",
    "MCP_TOOL_TIMEOUT": str(MCP_TOOL_TIMEOUT_MS),
}
_AUTH_STATUS_RECORDED_FIELDS = ("loggedIn", "authMethod", "apiProvider", "subscriptionType")  # no email or org
_MODEL_FAMILY_ALIASES = {"fable", "opus", "sonnet", "haiku"}
_SYNTHETIC_MODEL = "<synthetic>"


# Harbor's error policy (harbor/models/trial/error_policy.py) keys on these class names.
class ApiUsageLimitError(RuntimeError):
    """The subscription's usage limit, not a per-minute rate limit."""


class ApiInternalServerError(RuntimeError):
    pass


class ContextWindowExceededError(RuntimeError):
    pass


class AgentStartupError(RuntimeError):
    """The CLI could not be set up to run the task. Not in Harbor's policy table, so never a valid reward."""


@dataclass(frozen=True)
class _Failure:
    kind: str
    error: type[Exception]
    message: str

    def exception(self) -> Exception:
        return self.error(f"[{self.kind}] {self.message}")


_ASSISTANT_ERROR_FAILURES: dict[str, tuple[str, type[Exception]]] = {
    "authentication_failed": ("auth", AuthenticationError),
    "billing_error": ("usage_limit", ApiUsageLimitError),
    "rate_limit": ("rate_limit", ApiRateLimitError),
    "server_error": ("api_server", ApiInternalServerError),
}
_TEXT_FAILURES: tuple[tuple[str, type[Exception], tuple[str, ...]], ...] = (
    ("auth", AuthenticationError, (
        "not logged in", "please run /login", "invalid api key", "authentication_error", "authentication_failed",
        "oauth token has expired", "invalid bearer",
    )),
    ("model_not_found", ModelNotFoundError, (
        "issue with the selected model", "model_not_found", "may not exist or you may not have access",
        "unrecognized_model_error",
    )),
    ("usage_limit", ApiUsageLimitError, (
        "usage limit", "hit your limit", "limit reached", "credit balance", "spending limit",
    )),
    ("rate_limit", ApiRateLimitError, ("rate limit", "rate_limit", "too many requests")),
    ("context_window", ContextWindowExceededError, ("prompt is too long", "context_length", "context window")),
    ("permission_denied", PermissionDeniedError, ("permission_error", "forbidden")),
    ("api_connection", ApiConnectionError, (
        "econnreset", "etimedout", "enotfound", "econnrefused", "fetch failed", "socket hang up", "connection error",
        "unable to connect",
    )),
    ("api_server", ApiInternalServerError, ("overloaded", "internal server error", "api_error")),
)


def classify_failure(text: str, assistant_error: str | None = None, api_status: Any = None) -> tuple[str, type[Exception]]:
    """Structured signals first; message text only when the CLI gave none."""
    if assistant_error in _ASSISTANT_ERROR_FAILURES:
        return _ASSISTANT_ERROR_FAILURES[assistant_error]
    if api_status == 401:
        return "auth", AuthenticationError
    if api_status == 403:
        return "permission_denied", PermissionDeniedError
    if api_status == 404:
        return "model_not_found", ModelNotFoundError
    if api_status == 429:
        return "rate_limit", ApiRateLimitError
    if isinstance(api_status, int) and api_status >= 500:
        return "api_server", ApiInternalServerError
    lowered = text.lower()
    for kind, error, markers in _TEXT_FAILURES:
        if any(marker in lowered for marker in markers):
            return kind, error
    return "cli_error", NonZeroAgentExitCodeError


def model_matches(requested: str, actual: str) -> bool | None:
    """Whether the model the CLI used is the one requested. None: the request cannot be checked (e.g. "best")."""
    requested = re.sub(r"\[.*\]$", "", requested.strip().lower())
    actual = re.sub(r"\[.*\]$", "", actual.strip().lower())
    if requested in _MODEL_FAMILY_ALIASES:
        return requested in actual
    if requested.startswith("claude-") or re.search(r"\d", requested):
        return actual == requested or actual.startswith(requested + "-") or requested.startswith(actual + "-")
    return None


def child_env(parent: dict[str, str] | None = None) -> dict[str, str]:
    source = os.environ if parent is None else parent
    env = {
        key: value
        for key, value in source.items()
        if key in _KEEP_ENV or not (key.startswith(_DROP_ENV_PREFIXES) or key in _DROP_ENV_NAMES)
    }
    env.update(_FORCED_ENV)
    return env


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _tokens(uncached: Any, cache_read: Any, cache_write: Any, output: Any) -> dict[str, int | None]:
    """Harbor's `n_input_tokens` includes cache reads and writes. If a part is missing the total is unknown, not 0."""
    parts = [_int_or_none(uncached), _int_or_none(cache_read), _int_or_none(cache_write)]
    return {
        "input": None if None in parts else sum(parts),  # type: ignore[arg-type]
        "uncached_input": parts[0],
        "cache_read": parts[1],
        "cache_write": parts[2],
        "output": _int_or_none(output),
    }


def _sum_known(rows: list[dict[str, int | None]]) -> dict[str, int | None]:
    return {
        key: None if any(row[key] is None for row in rows) else sum(row[key] for row in rows)  # type: ignore[misc]
        for key in ("input", "uncached_input", "cache_read", "cache_write", "output")
    }


def usage_from_events(result: dict[str, Any] | None, assistant_usage: dict[str, tuple[str, dict[str, Any]]]) -> dict[str, Any] | None:
    """Tokens from the most complete source available. The result event only exists if the CLI ended by itself."""
    model_usage = (result or {}).get("modelUsage")
    if isinstance(model_usage, dict) and model_usage and all(isinstance(m, dict) for m in model_usage.values()):
        per_model = {
            name: _tokens(m.get("inputTokens"), m.get("cacheReadInputTokens"), m.get("cacheCreationInputTokens"), m.get("outputTokens"))
            for name, m in model_usage.items()
        }
        return {**_sum_known(list(per_model.values())), "per_model": per_model, "source": "result.modelUsage", "partial": False}
    usage = (result or {}).get("usage")
    if isinstance(usage, dict) and usage:
        tokens = _tokens(usage.get("input_tokens"), usage.get("cache_read_input_tokens"), usage.get("cache_creation_input_tokens"), usage.get("output_tokens"))
        return {**tokens, "per_model": {}, "source": "result.usage", "partial": False}
    if assistant_usage:
        # Killed or crashed run: what was reported per message so far. A request in flight is not included.
        per_model_rows: dict[str, list[dict[str, int | None]]] = {}
        for model, usage in assistant_usage.values():
            per_model_rows.setdefault(model, []).append(
                _tokens(usage.get("input_tokens"), usage.get("cache_read_input_tokens"), usage.get("cache_creation_input_tokens"), usage.get("output_tokens"))
            )
        per_model = {model: _sum_known(rows) for model, rows in per_model_rows.items()}
        return {**_sum_known(list(per_model.values())), "per_model": per_model, "source": "assistant-events", "partial": True}
    return None


def _tool_result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part["text"] if isinstance(part, dict) and isinstance(part.get("text"), str) else json.dumps(part, ensure_ascii=False)
            for part in content
        )
    return "" if content is None else json.dumps(content, ensure_ascii=False)


def _content_blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content")
    return [block for block in content if isinstance(block, dict)] if isinstance(content, list) else []


class _StreamState:
    """Everything learned from the CLI's stream-json output, and the isolation checks made on it as it arrives."""

    def __init__(self, requested_model: str):
        self.requested_model = requested_model
        self.events: list[dict[str, Any]] = []
        self.init: dict[str, Any] | None = None
        self.result: dict[str, Any] | None = None
        self.final_message = ""
        self.tool_names: dict[str, str] = {}
        self.assistant_usage: dict[str, tuple[str, dict[str, Any]]] = {}
        self.models_seen: list[str] = []
        self.last_assistant_error: str | None = None
        self.last_retry_error: str | None = None
        self.n_tool_requests = 0
        self.n_unavailable_tool_attempts = 0
        self.n_api_retries = 0
        self.n_malformed_lines = 0
        self.abort: _Failure | None = None
        self.abort_event = asyncio.Event()

    def _abort(self, kind: str, error: type[Exception], message: str) -> None:
        if self.abort is None:
            self.abort = _Failure(kind, error, message)
            self.abort_event.set()

    def feed_line(self, line: bytes) -> None:
        if not line.strip():
            return
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.n_malformed_lines += 1
            return
        if not isinstance(event, dict):
            self.n_malformed_lines += 1
            return
        self.events.append(event)
        try:
            self._handle(event)
        except (AttributeError, KeyError, TypeError, ValueError):  # valid JSON of an unexpected shape
            self.n_malformed_lines += 1

    def _handle(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            self._check_init(event)
        elif kind == "system" and event.get("subtype") == "api_retry":
            self.n_api_retries += 1
            self.last_retry_error = str(event.get("error"))
        elif kind == "assistant":
            self._handle_assistant(event)
        elif kind == "user":
            self._handle_tool_results(event)
        elif kind == "result":
            self.result = event

    def _check_init(self, event: dict[str, Any]) -> None:
        self.init = event
        tools = event.get("tools")
        if tools != [ALLOWED_TOOL]:
            self._abort("tool_policy_violation", PermissionDeniedError, f"expected only {ALLOWED_TOOL}, the CLI offers {tools!r}")
            return
        if event.get("permissionMode") != "dontAsk":
            self._abort("tool_policy_violation", PermissionDeniedError, f"unexpected permission mode {event.get('permissionMode')!r}")
            return
        servers = event.get("mcp_servers")
        names = [s.get("name") for s in servers] if isinstance(servers, list) else None
        if names != [MCP_SERVER_NAME]:
            self._abort("tool_policy_violation", PermissionDeniedError, f"unexpected MCP servers {servers!r}")
            return
        if servers[0].get("status") != "connected":
            self._abort("mcp_startup", AgentStartupError, f"the sandbox MCP server did not connect: {servers[0]!r}")
            return
        self._check_model(event.get("model"))

    def _check_model(self, actual: Any) -> None:
        if not isinstance(actual, str) or not actual or actual == _SYNTHETIC_MODEL:
            return
        if actual not in self.models_seen:
            self.models_seen.append(actual)
        if model_matches(self.requested_model, actual) is False:
            self._abort("model_mismatch", ModelNotFoundError, f"requested {self.requested_model!r}, the CLI used {actual!r}")

    def _handle_assistant(self, event: dict[str, Any]) -> None:
        message = event.get("message") or {}
        model = message.get("model")
        self._check_model(model)
        if event.get("error"):
            self.last_assistant_error = str(event["error"])
        if isinstance(message.get("id"), str) and isinstance(message.get("usage"), dict) and model != _SYNTHETIC_MODEL:
            # One event per content block, all carrying the same message id and usage: count each message once.
            self.assistant_usage[message["id"]] = (str(model), message["usage"])
        for block in _content_blocks(message):
            if block.get("type") == "text" and block.get("text"):
                self.final_message = block["text"]
            elif block.get("type") == "tool_use":
                self.n_tool_requests += 1
                self.tool_names[str(block.get("id"))] = str(block.get("name"))

    def _handle_tool_results(self, event: dict[str, Any]) -> None:
        for block in _content_blocks(event.get("message") or {}):
            if block.get("type") != "tool_result":
                continue
            name = self.tool_names.get(str(block.get("tool_use_id")))
            if name is None or name == ALLOWED_TOOL:
                continue
            # A model may name a tool that does not exist; the CLI answers with an error and nothing runs.
            # That is the model's behaviour and the run stays valid. A successful result would be a real escape.
            if block.get("is_error"):
                self.n_unavailable_tool_attempts += 1
            else:
                self._abort("tool_policy_violation", PermissionDeniedError, f"the disallowed tool {name!r} ran on the host")

    @property
    def permission_denials(self) -> list[Any]:
        denials = (self.result or {}).get("permission_denials")
        return denials if isinstance(denials, list) else []


class _TrackedBridge(_Bridge):
    """`_Bridge`, plus: stopping it also cancels sandbox commands still in flight, so none outlive the run."""

    def __init__(self, environment: BaseEnvironment, call_log: Path):
        super().__init__(environment, call_log)
        self._active: set[asyncio.Task[Any]] = set()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        self._active.add(task)
        try:
            await super()._handle(reader, writer)
        finally:
            self._active.discard(task)
            writer.close()

    async def stop(self) -> None:
        await super().stop()
        active = list(self._active)
        for task in active:
            task.cancel()
        if active:
            await asyncio.wait(active, timeout=DRAIN_TIMEOUT_SEC)


class ClaudeHostAgent(BaseAgent):
    """Claude Code CLI on the host, acting in the sandbox through the bridged `mcp__sandbox__bash` tool."""

    capabilities = AgentCapabilities(atif=True)

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        reasoning_effort: str | None = None,
        claude_bin: str = "claude",
        **kwargs: Any,
    ):
        super().__init__(logs_dir=logs_dir, model_name=model_name, **kwargs)
        self._reasoning_effort = reasoning_effort
        self._claude_bin = shutil.which(claude_bin) or claude_bin
        self._claude_version = self._read_claude_version()

    def _read_claude_version(self) -> str:
        try:
            out = subprocess.run(
                [self._claude_bin, "--version"], env=self._child_env(), stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=60,
            ).stdout
        except Exception:
            return "unknown"
        match = re.search(r"(\d+\.\d+\.\d+)", out)
        return match.group(1) if match else "unknown"

    @staticmethod
    @override
    def name() -> str:
        return "claude-host"

    @override
    def version(self) -> str:
        return f"claude-{self._claude_version}+bridge-prompt-v{PROMPT_VERSION}"

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        if shutil.which(self._claude_bin) is None:
            raise FileNotFoundError(f"Claude Code CLI not found on the host: {self._claude_bin!r}")

    def _child_env(self) -> dict[str, str]:
        return child_env()

    def _build_command(self, mcp_config: dict[str, Any]) -> list[str]:
        if not self.model_name:
            raise ValueError("claude-host needs a model: pass -m <model name accepted by claude --model>")
        # The prompt goes through stdin: `--tools` and `--allowedTools` are variadic and would swallow an argument.
        # No --fallback-model: a different model must never be used without the user asking for it.
        command = [
            self._claude_bin, "-p",
            "--model", self.model_name,
            "--input-format", "text",
            "--output-format", "stream-json", "--verbose",
            "--tools", "",
            "--mcp-config", json.dumps(mcp_config), "--strict-mcp-config",
            "--setting-sources", "",
            "--allowedTools", ALLOWED_TOOL,
            "--permission-mode", "dontAsk", "--permission-prompts", "none",
            "--disable-slash-commands", "--no-chrome", "--no-session-persistence",
        ]
        if self._reasoning_effort:
            command += ["--effort", self._reasoning_effort]
        return command

    async def _read_auth_status(self, cwd: str) -> dict[str, Any]:
        """The CLI's own view of the login. Keeps only non-identifying fields; costs no model call."""
        proc = await asyncio.create_subprocess_exec(
            self._claude_bin, "auth", "status", "--json",
            cwd=cwd, env=self._child_env(), stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, start_new_session=True,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=AUTH_STATUS_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            raise AgentStartupError("`claude auth status` did not answer") from None
        finally:
            await _terminate_group(proc)
        try:
            status = json.loads(stdout)
            if not isinstance(status, dict):
                raise ValueError("not an object")
        except ValueError:
            raise AgentStartupError(f"`claude auth status` gave unreadable output (exit code {proc.returncode})") from None
        return {field: status.get(field) for field in _AUTH_STATUS_RECORDED_FIELDS}

    def _decide_failure(self, state: _StreamState, returncode: int | None, stderr_text: str) -> _Failure | None:
        if state.abort is not None:
            return state.abort
        result = state.result
        if returncode == 0 and result is not None and not result.get("is_error") and result.get("subtype") == "success":
            denials = state.permission_denials
            if denials:
                tools = [d.get("tool_name") if isinstance(d, dict) else d for d in denials]
                return _Failure("permission_denied", PermissionDeniedError, f"{len(denials)} tool call(s) were denied by the CLI: {tools!r}")
            return None
        result_text = str((result or {}).get("result") or "") + " " + " ".join(map(str, (result or {}).get("errors") or []))
        text = "\n".join((stderr_text, result_text, state.final_message if state.last_assistant_error else "", state.last_retry_error or ""))
        kind, error = classify_failure(text, state.last_assistant_error, (result or {}).get("api_error_status"))
        if kind == "cli_error" and returncode == 0 and result is None:
            kind = "malformed_output"
        detail = (
            f"exit code {returncode}, result subtype {(result or {}).get('subtype')!r}, "
            f"assistant error {state.last_assistant_error!r}, stderr {stderr_text.strip()[:500]!r}, result {result_text.strip()[:500]!r}"
        )
        return _Failure(kind, error, detail)

    def _write_atif(self, logs_dir: Path, prompt: str, state: _StreamState, tokens: dict[str, Any] | None, model: str | None) -> None:
        steps: list[Step] = [Step(step_id=1, source="user", message=prompt)]
        by_message_id: dict[str, Step] = {}
        by_tool_call_id: dict[str, Step] = {}
        for event in state.events:
            timestamp = event.get("timestamp") if isinstance(event.get("timestamp"), str) else None
            message = event.get("message")
            if not isinstance(message, dict):
                continue
            if event.get("type") == "assistant":
                blocks = _content_blocks(message)
                if not blocks:
                    continue
                step = by_message_id.get(str(message.get("id")))
                if step is None:
                    usage = message.get("usage")
                    is_synthetic = message.get("model") == _SYNTHETIC_MODEL
                    metrics = None
                    if isinstance(usage, dict) and not is_synthetic:
                        row = _tokens(usage.get("input_tokens"), usage.get("cache_read_input_tokens"), usage.get("cache_creation_input_tokens"), usage.get("output_tokens"))
                        metrics = Metrics(
                            prompt_tokens=row["input"], completion_tokens=row["output"], cached_tokens=row["cache_read"],
                            extra={"cache_creation_input_tokens": row["cache_write"]},
                        )
                    step = Step(
                        step_id=len(steps) + 1, timestamp=timestamp, source="agent", message="",
                        model_name=None if is_synthetic else message.get("model"), metrics=metrics,
                        extra={"cli_error": event["error"]} if event.get("error") else None,
                    )
                    steps.append(step)
                    by_message_id[str(message.get("id"))] = step
                for block in blocks:
                    if block.get("type") == "text":
                        step.message = f"{step.message}\n{block.get('text', '')}" if step.message else block.get("text", "")
                    elif block.get("type") == "thinking" and block.get("thinking"):
                        step.reasoning_content = (step.reasoning_content or "") + block["thinking"]
                    elif block.get("type") == "tool_use":
                        call_id = str(block.get("id"))
                        arguments = block.get("input") if isinstance(block.get("input"), dict) else {}
                        step.tool_calls = (step.tool_calls or []) + [
                            ToolCall(tool_call_id=call_id, function_name=str(block.get("name")), arguments=arguments)
                        ]
                        by_tool_call_id[call_id] = step
            elif event.get("type") == "user":
                for block in _content_blocks(message):
                    if block.get("type") != "tool_result":
                        continue
                    call_id = str(block.get("tool_use_id"))
                    step = by_tool_call_id.get(call_id)
                    if step is None:
                        continue
                    if step.observation is None:
                        step.observation = Observation(results=[])
                    step.observation.results.append(ObservationResult(
                        source_call_id=call_id, content=_tool_result_text(block.get("content")),
                        extra={"is_error": True} if block.get("is_error") else None,
                    ))
        trajectory = Trajectory(
            session_id=(state.init or {}).get("session_id"),
            agent=Agent(name=self.name(), version=self.version(), model_name=model),
            steps=steps,
            final_metrics=FinalMetrics(
                total_prompt_tokens=(tokens or {}).get("input"),
                total_completion_tokens=(tokens or {}).get("output"),
                total_cached_tokens=(tokens or {}).get("cache_read"),
                total_cost_usd=None,  # see cost_usd in _finalize
                total_steps=len(steps),
                extra={"usage_source": (tokens or {}).get("source"), "usage_is_partial": (tokens or {}).get("partial")},
            ),
        )
        (logs_dir / "trajectory.json").write_text(json.dumps(trajectory.to_json_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    def _finalize(
        self,
        context: AgentContext,
        logs_dir: Path,
        prompt: str,
        state: _StreamState,
        bridge: _Bridge,
        auth_status: dict[str, Any] | None,
        failure_kind: str | None,
        returncode: int | None,
        log_truncated: bool,
        wall_seconds: float,
    ) -> None:
        tokens = usage_from_events(state.result, state.assistant_usage)
        if tokens:
            context.n_input_tokens = tokens["input"]
            context.n_output_tokens = tokens["output"]
            context.n_cache_tokens = tokens["cache_read"]
            usable = {
                name: ModelUsage(n_input_tokens=row["input"], n_output_tokens=row["output"], n_cache_tokens=row["cache_read"])
                for name, row in tokens["per_model"].items()
                if None not in (row["input"], row["output"], row["cache_read"])
            }
            if usable:
                context.model_usage = usable
        # The CLI's total_cost_usd is computed from a list price, also under a subscription, where it is not what was
        # charged. It is kept below under a name that says so, and never reported as cost.
        context.cost_usd = None
        result = state.result or {}
        init = state.init or {}
        actual_model = init.get("model") if isinstance(init.get("model"), str) else None
        context.metadata = {
            "agent": self.name(),
            "claude_version": self._claude_version,
            "cli_reported_version": init.get("claude_code_version"),
            "prompt_version": PROMPT_VERSION,
            "requested_model": self.model_name,
            "init_model": actual_model,
            "models_seen": state.models_seen,
            "requested_effort": self._reasoning_effort,
            "auth_status": auth_status,
            "api_key_source": init.get("apiKeySource"),
            "init_tools": init.get("tools"),
            "init_mcp_servers": init.get("mcp_servers"),
            "init_permission_mode": init.get("permissionMode"),
            "failure_kind": failure_kind,
            "exit_code": returncode,
            "result_subtype": result.get("subtype"),
            "terminal_reason": result.get("terminal_reason"),
            "usage_source": tokens["source"] if tokens else None,
            "usage_is_partial": tokens["partial"] if tokens else None,
            "uncached_input_tokens": tokens["uncached_input"] if tokens else None,
            "cache_write_tokens": tokens["cache_write"] if tokens else None,
            "per_model_tokens": tokens["per_model"] if tokens else None,
            "cli_list_price_estimate_usd_not_charged": result.get("total_cost_usd"),
            "n_turns": result.get("num_turns"),
            "api_duration_ms": result.get("duration_api_ms"),
            "session_duration_ms": result.get("duration_ms"),
            "claude_session_id": init.get("session_id"),
            "n_tool_requests": state.n_tool_requests,
            "n_sandbox_calls": bridge.n_calls,
            "n_sandbox_calls_failed": bridge.n_failed,
            "sandbox_seconds": round(bridge.seconds, 1),
            "n_permission_denied": len(state.permission_denials),
            "n_unavailable_tool_attempts": state.n_unavailable_tool_attempts,
            "n_api_retries": state.n_api_retries,
            "n_malformed_output_lines": state.n_malformed_lines,
            "log_truncated": log_truncated,
            "wall_seconds": round(wall_seconds, 1),
            "final_message": state.final_message[:FINAL_MESSAGE_METADATA_CHARS],
        }
        (logs_dir / "claude-final-message.txt").write_text(state.final_message, encoding="utf-8")
        try:
            self._write_atif(logs_dir, prompt, state, tokens, actual_model or self.model_name)
        except Exception as exc:
            self.logger.warning("claude-host: could not write the ATIF trajectory: %r", exc)

    @override
    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        if not self.model_name:  # fail before any temp dir exists
            raise ValueError("claude-host needs a model: pass -m <model name accepted by claude --model>")
        # The child runs in an empty temp dir, so every path handed to it must be absolute.
        logs_dir = self.logs_dir.resolve()
        logs_dir.mkdir(parents=True, exist_ok=True)
        events_path = logs_dir / "claude-events.jsonl"
        stderr_path = logs_dir / "claude-stderr.txt"
        prompt_path = logs_dir / "claude-prompt.txt"
        prompt = PROMPT_TEMPLATE.format(instruction=instruction)
        prompt_path.write_text(prompt, encoding="utf-8")

        # /tmp, not the system default: a unix socket path has a length limit. mkdtemp is 0700.
        work_dir = Path(tempfile.mkdtemp(prefix="cch-", dir="/tmp"))
        (work_dir / "cwd").mkdir()
        socket_path = str(work_dir / "bridge.sock")
        bridge = _TrackedBridge(environment, logs_dir / "sandbox-tool-calls.jsonl")
        mcp_config = {
            "mcpServers": {MCP_SERVER_NAME: {"type": "stdio", "command": sys.executable, "args": [str(MCP_SCRIPT), socket_path]}}
        }
        command = self._build_command(mcp_config)
        env = self._child_env()
        # Names only: a value could hold a proxy password or a token.
        (logs_dir / "claude-run-config.json").write_text(json.dumps({
            "argv": command,
            "cwd": str(work_dir / "cwd"),
            "prompt_via": "stdin",
            "env_names_passed": sorted(env),
            "env_names_dropped": sorted(set(os.environ) - set(env)),
            "env_forced": _FORCED_ENV,
        }, indent=2, ensure_ascii=False), encoding="utf-8")

        state = _StreamState(self.model_name)
        proc: asyncio.subprocess.Process | None = None
        readers: list[asyncio.Task[Any]] = []
        truncated = [False]
        auth_status: dict[str, Any] | None = None
        failure: _Failure | None = None
        failure_kind: str | None = None
        started = time.monotonic()
        try:
            auth_status = await self._read_auth_status(str(work_dir / "cwd"))
            if auth_status.get("loggedIn") is not True:
                raise AuthenticationError("the Claude Code CLI on this host is not logged in; run `claude auth login`")
            await bridge.start(socket_path)
            with prompt_path.open("rb") as stdin:
                proc = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=str(work_dir / "cwd"),
                    env=env,
                    stdin=stdin,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                )
            assert proc.stdout is not None and proc.stderr is not None
            readers = [
                asyncio.create_task(_pump_events(proc.stdout, events_path, state, truncated)),
                asyncio.create_task(_pump_raw(proc.stderr, stderr_path, truncated)),
            ]
            waiters = [asyncio.create_task(_wait_for_exit(proc)), asyncio.create_task(state.abort_event.wait())]
            try:
                await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for waiter in waiters:
                    waiter.cancel()
        except asyncio.CancelledError:  # Harbor's agent timeout, or the job being cancelled
            failure_kind = "timeout_or_cancelled"
            raise
        except AuthenticationError as exc:
            failure = _Failure("auth", AuthenticationError, str(exc))
        except (AgentStartupError, OSError) as exc:
            failure = _Failure("cli_startup", AgentStartupError, f"{type(exc).__name__}: {exc}")
        finally:
            try:
                # Also after a normal exit: nothing the CLI started may outlive it.
                await _terminate_group(proc)
                # A leftover process holding a pipe must not hang the trial; what was read is already on disk.
                if readers:
                    await asyncio.wait(readers, timeout=DRAIN_TIMEOUT_SEC)
                for reader in readers:
                    reader.cancel()
                await bridge.stop()
            finally:
                shutil.rmtree(work_dir, ignore_errors=True)
                returncode = proc.returncode if proc is not None else None
                if failure is None and failure_kind is None and proc is not None:
                    stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else ""
                    failure = self._decide_failure(state, returncode, stderr_text)
                try:
                    # Runs on every path: a timed-out trial has still used quota, and that must reach result.json.
                    self._finalize(
                        context, logs_dir, prompt, state, bridge, auth_status,
                        failure.kind if failure else failure_kind, returncode, truncated[0], time.monotonic() - started,
                    )
                except Exception as exc:
                    self.logger.warning("claude-host: could not record usage/metadata: %r", exc)
        if failure is not None:
            raise failure.exception()


async def _wait_for_exit(proc: asyncio.subprocess.Process) -> None:
    """Not `proc.wait()`: that also waits for the pipes, so a leftover child holding one would hang the trial."""
    while proc.returncode is None:
        await asyncio.sleep(EXIT_POLL_SEC)


async def _terminate_group(proc: asyncio.subprocess.Process | None) -> None:
    """SIGTERM, then SIGKILL for the whole process group (the CLI and the MCP server it started)."""
    if proc is None:
        return
    if proc.returncode is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=TERMINATE_GRACE_SEC)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    if proc.returncode is None:
        try:
            await asyncio.wait_for(proc.wait(), timeout=TERMINATE_GRACE_SEC)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass


def _write_capped(sink: Any, chunk: bytes, written: int, truncated: list[bool]) -> int:
    if written + len(chunk) > MAX_LOG_BYTES:
        truncated[0] = True
        return written
    sink.write(chunk)
    sink.flush()
    return written + len(chunk)


async def _pump_raw(stream: asyncio.StreamReader, path: Path, truncated: list[bool]) -> None:
    written = 0
    with path.open("wb") as sink:
        while chunk := await stream.read(65536):
            written = _write_capped(sink, chunk, written, truncated)


async def _pump_events(stream: asyncio.StreamReader, path: Path, state: _StreamState, truncated: list[bool]) -> None:
    """Keeps the raw output and parses it line by line. An oversized line is skipped, never fatal."""
    written = 0
    buffer = bytearray()
    skipping = False
    with path.open("wb") as sink:
        while chunk := await stream.read(65536):
            written = _write_capped(sink, chunk, written, truncated)
            buffer.extend(chunk)
            while (newline := buffer.find(b"\n")) >= 0:
                line = bytes(buffer[:newline])
                del buffer[: newline + 1]
                if skipping:
                    skipping = False
                else:
                    state.feed_line(line)
            if len(buffer) > MAX_EVENT_LINE_BYTES:
                buffer.clear()
                if not skipping:
                    state.n_malformed_lines += 1
                skipping = True
        if buffer and not skipping:
            state.feed_line(bytes(buffer))
