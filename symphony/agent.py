"""Agent runner - launches Claude Code CLI as a subprocess agent.

Streams output back via a queue. The orchestrator reads the queue
and feeds updates to the status surface and tracker.
"""

import logging
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from .config import ClaudeConfig

log = logging.getLogger(__name__)


class AgentEvent(Enum):
    STARTED = "started"
    OUTPUT = "output"
    DONE = "done"
    ERROR = "error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass
class AgentUpdate:
    event: AgentEvent
    message: str = ""
    exit_code: Optional[int] = None


class AgentSession:
    """
    Runs Claude Code (`claude --print ...`) in a workspace directory.
    Non-interactive: one prompt in, streamed output back.
    """

    def __init__(
        self,
        cfg: ClaudeConfig,
        workspace_path: Path,
        prompt: str,
        issue_identifier: str,
        attempt: Optional[int] = None,
    ):
        self.cfg = cfg
        self.workspace_path = workspace_path
        self.prompt = prompt
        self.issue_identifier = issue_identifier
        self.attempt = attempt

        self._proc: Optional[subprocess.Popen] = None
        self._updates: queue.Queue[AgentUpdate] = queue.Queue()
        self._cancelled = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancelled.set()
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def updates(self) -> queue.Queue[AgentUpdate]:
        return self._updates

    def wait(self, timeout_s: float = 3600) -> bool:
        if self._thread:
            self._thread.join(timeout=timeout_s)
            return not self._thread.is_alive()
        return True

    def _run(self) -> None:
        self.started_at = time.monotonic()
        self._updates.put(AgentUpdate(AgentEvent.STARTED, f"agent started for {self.issue_identifier}"))

        try:
            cmd = self._build_command()
            log.info("[%s] launching: %s", self.issue_identifier, " ".join(cmd[:3]) + " ...")

            env = {**os.environ}
            # Ensure ANTHROPIC_API_KEY is present
            if "ANTHROPIC_API_KEY" not in env:
                log.warning("ANTHROPIC_API_KEY not set - agent may fail")

            self._proc = subprocess.Popen(
                cmd,
                cwd=str(self.workspace_path),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
                bufsize=1,
                encoding="utf-8",
            )
            # Write prompt via stdin then close so the process sees EOF
            self._proc.stdin.write(self.prompt)
            self._proc.stdin.close()

            turn_timeout_s = self.cfg.turn_timeout_ms / 1000
            stall_timeout_s = self.cfg.stall_timeout_ms / 1000
            last_output_at = time.monotonic()
            output_lines: list[str] = []

            for line in self._proc.stdout:
                if self._cancelled.is_set():
                    self._proc.terminate()
                    self._updates.put(AgentUpdate(AgentEvent.CANCELLED, "cancelled by orchestrator"))
                    return

                now = time.monotonic()
                if stall_timeout_s > 0 and (now - last_output_at) > stall_timeout_s:
                    self._proc.terminate()
                    self._updates.put(AgentUpdate(AgentEvent.TIMEOUT, "stall timeout exceeded"))
                    return

                if (now - self.started_at) > turn_timeout_s:
                    self._proc.terminate()
                    self._updates.put(AgentUpdate(AgentEvent.TIMEOUT, "turn timeout exceeded"))
                    return

                last_output_at = now
                line = line.rstrip()
                output_lines.append(line)
                self._updates.put(AgentUpdate(AgentEvent.OUTPUT, line))

            self._proc.wait()
            self.finished_at = time.monotonic()

            exit_code = self._proc.returncode
            summary = "\n".join(output_lines[-20:]) if output_lines else "(no output)"

            if exit_code == 0:
                self._updates.put(AgentUpdate(AgentEvent.DONE, summary, exit_code=0))
            else:
                self._updates.put(AgentUpdate(
                    AgentEvent.ERROR,
                    f"exit code {exit_code}\n{summary}",
                    exit_code=exit_code,
                ))

        except Exception as e:
            log.exception("[%s] agent runner exception", self.issue_identifier)
            self._updates.put(AgentUpdate(AgentEvent.ERROR, str(e)))
        finally:
            self.finished_at = self.finished_at or time.monotonic()

    def _build_command(self) -> list[str]:
        """Build the claude CLI command. Prompt is piped via stdin, not a positional arg."""
        cmd = [self.cfg.command]

        # Non-interactive single-run mode; prompt comes from stdin
        cmd += ["--print"]

        # Tool allowlist
        if self.cfg.allowed_tools:
            cmd += ["--allowedTools", ",".join(self.cfg.allowed_tools)]

        # Extra args from config (skip --print to avoid duplicate)
        for arg in self.cfg.args:
            if arg != "--print":
                cmd.append(arg)

        return cmd
