"""Typed config layer derived from workflow front matter + environment resolution."""

import os
from dataclasses import dataclass, field
from typing import Optional


def _env(value: str) -> str:
    """Resolve $VAR_NAME references to environment variables."""
    if isinstance(value, str) and value.startswith("$"):
        var = value[1:]
        resolved = os.environ.get(var, "")
        return resolved
    return value


@dataclass
class TrackerConfig:
    kind: str = "github"                    # "github" | "linear"
    repo: Optional[str] = None              # "owner/repo" for github; project_slug for linear
    api_key: Optional[str] = None
    endpoint: Optional[str] = None
    active_states: list[str] = field(default_factory=lambda: ["ready"])
    terminal_states: list[str] = field(default_factory=lambda: ["done", "cancelled", "closed", "duplicate"])


@dataclass
class PollingConfig:
    interval_ms: int = 30_000


@dataclass
class WorkspaceConfig:
    root: str = "/tmp/symphony_workspaces"
    hook_timeout_ms: int = 60_000


@dataclass
class HooksConfig:
    after_create: Optional[str] = None
    before_run: Optional[str] = None
    after_run: Optional[str] = None
    before_remove: Optional[str] = None
    timeout_ms: int = 60_000


@dataclass
class AgentConfig:
    max_concurrent_agents: int = 5
    max_turns: int = 30
    max_retry_backoff_ms: int = 300_000
    max_concurrent_agents_by_state: dict[str, int] = field(default_factory=dict)


@dataclass
class ClaudeConfig:
    """Claude Code CLI agent config."""
    command: str = "claude"
    args: list[str] = field(default_factory=lambda: ["--print"])
    allowed_tools: list[str] = field(default_factory=lambda: [
        "Read", "Edit", "Write", "Bash", "Glob", "Grep",
    ])
    approval_policy: str = "on-request"
    turn_timeout_ms: int = 3_600_000
    stall_timeout_ms: int = 300_000


@dataclass
class ServiceConfig:
    tracker: TrackerConfig = field(default_factory=TrackerConfig)
    polling: PollingConfig = field(default_factory=PollingConfig)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    hooks: HooksConfig = field(default_factory=HooksConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    claude: ClaudeConfig = field(default_factory=ClaudeConfig)


def build_config(raw: dict) -> ServiceConfig:
    """Build a typed ServiceConfig from WORKFLOW.md front matter."""
    cfg = ServiceConfig()

    t = raw.get("tracker", {})
    if t:
        cfg.tracker.kind = t.get("kind", cfg.tracker.kind)
        cfg.tracker.repo = _env(t.get("repo") or t.get("project_slug") or "")
        cfg.tracker.api_key = _env(t.get("api_key", ""))
        cfg.tracker.endpoint = t.get("endpoint")
        if "active_states" in t:
            cfg.tracker.active_states = [s.lower() for s in t["active_states"]]
        if "terminal_states" in t:
            cfg.tracker.terminal_states = [s.lower() for s in t["terminal_states"]]

    p = raw.get("polling", {})
    if p:
        cfg.polling.interval_ms = int(p.get("interval_ms", cfg.polling.interval_ms))

    w = raw.get("workspace", {})
    if w:
        root = _env(str(w.get("root", cfg.workspace.root)))
        if root.startswith("~"):
            import os
            root = os.path.expanduser(root)
        cfg.workspace.root = root

    h = raw.get("hooks", {})
    if h:
        cfg.hooks.after_create = h.get("after_create")
        cfg.hooks.before_run = h.get("before_run")
        cfg.hooks.after_run = h.get("after_run")
        cfg.hooks.before_remove = h.get("before_remove")
        cfg.hooks.timeout_ms = int(h.get("timeout_ms", cfg.hooks.timeout_ms))

    a = raw.get("agent", {})
    if a:
        cfg.agent.max_concurrent_agents = int(a.get("max_concurrent_agents", cfg.agent.max_concurrent_agents))
        cfg.agent.max_turns = int(a.get("max_turns", cfg.agent.max_turns))
        cfg.agent.max_retry_backoff_ms = int(a.get("max_retry_backoff_ms", cfg.agent.max_retry_backoff_ms))
        cfg.agent.max_concurrent_agents_by_state = {
            k.lower(): v for k, v in a.get("max_concurrent_agents_by_state", {}).items()
        }

    c = raw.get("claude", raw.get("codex", {}))
    if c:
        if "command" in c:
            cfg.claude.command = c["command"]
        if "args" in c:
            cfg.claude.args = c["args"]
        if "allowed_tools" in c:
            cfg.claude.allowed_tools = c["allowed_tools"]
        if "approval_policy" in c:
            cfg.claude.approval_policy = c["approval_policy"]
        if "turn_timeout_ms" in c:
            cfg.claude.turn_timeout_ms = int(c["turn_timeout_ms"])
        if "stall_timeout_ms" in c:
            cfg.claude.stall_timeout_ms = int(c["stall_timeout_ms"])

    return cfg
