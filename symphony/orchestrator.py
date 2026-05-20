"""
Orchestrator - the core of Symphony.

Single authoritative in-memory state. Polling loop, dispatch, retry,
reconciliation, cancellation on terminal state.
"""

import logging
import math
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .agent import AgentEvent, AgentSession
from .config import ServiceConfig
from .tracker.base import Issue, TrackerClient
from .workspace import WorkspaceManager
from .workflow import WorkflowDefinition, render_prompt

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Runtime state types
# ──────────────────────────────────────────────

@dataclass
class RunningEntry:
    issue_id: str
    issue_identifier: str
    issue_state: str
    session: AgentSession
    attempt: Optional[int]
    started_at: float


@dataclass
class RetryEntry:
    issue_id: str
    identifier: str
    attempt: int
    due_at: float
    error: Optional[str] = None
    timer: Optional[threading.Timer] = None


@dataclass
class OrchestratorState:
    running: dict[str, RunningEntry] = field(default_factory=dict)   # issue_id -> entry
    claimed: set[str] = field(default_factory=set)                    # issue IDs reserved
    retry_queue: dict[str, RetryEntry] = field(default_factory=dict)  # issue_id -> entry
    completed: set[str] = field(default_factory=set)                  # bookkeeping only


# ──────────────────────────────────────────────
# Orchestrator
# ──────────────────────────────────────────────

class Orchestrator:
    def __init__(
        self,
        cfg: ServiceConfig,
        tracker: TrackerClient,
        workflow: WorkflowDefinition,
        workspace_manager: WorkspaceManager,
        on_status_update=None,
    ):
        self.cfg = cfg
        self.tracker = tracker
        self.workflow = workflow
        self.wm = workspace_manager
        self.on_status_update = on_status_update or (lambda msg: None)

        self._state = OrchestratorState()
        self._lock = threading.RLock()
        self._stop_event = threading.Event()

    # ──────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────

    def run(self) -> None:
        """Main blocking loop."""
        log.info("symphony orchestrator started")
        self.on_status_update("Symphony started")

        try:
            while not self._stop_event.is_set():
                self._tick()
                interval_s = self.cfg.polling.interval_ms / 1000
                self._stop_event.wait(timeout=interval_s)
        finally:
            self._shutdown()

    def stop(self) -> None:
        self._stop_event.set()

    # ──────────────────────────────────────────
    # Poll tick
    # ──────────────────────────────────────────

    def _tick(self) -> None:
        try:
            issues = self.tracker.fetch_active_issues(self.cfg.tracker.active_states)
        except Exception as e:
            log.error("tracker fetch failed: %s", e)
            return

        with self._lock:
            self._reconcile(issues)
            self._dispatch(issues)

    def _reconcile(self, live_issues: list[Issue]) -> None:
        """Stop agents whose issues moved to a terminal state."""
        live_ids = {i.id for i in live_issues}
        terminal_lower = {s.lower() for s in self.cfg.tracker.terminal_states}

        for issue_id, entry in list(self._state.running.items()):
            # Check if the issue is still active
            live = next((i for i in live_issues if i.id == issue_id), None)
            if live is None:
                # Might have moved to terminal - confirm
                refreshed = self.tracker.fetch_issue(issue_id)
                if refreshed and refreshed.state.lower() in terminal_lower:
                    log.info("[%s] terminal state detected, stopping agent", entry.issue_identifier)
                    self._stop_agent(issue_id, "issue reached terminal state")
            elif live.state.lower() in terminal_lower:
                log.info("[%s] terminal state %r, stopping agent", entry.issue_identifier, live.state)
                self._stop_agent(issue_id, f"issue state: {live.state}")

    def _dispatch(self, issues: list[Issue]) -> None:
        """Claim and dispatch eligible issues."""
        max_agents = self.cfg.agent.max_concurrent_agents
        active_count = len(self._state.running)

        for issue in issues:
            if active_count >= max_agents:
                break

            if issue.id in self._state.claimed:
                continue

            if issue.state.lower() in {s.lower() for s in self.cfg.tracker.terminal_states}:
                continue

            # Per-state concurrency limit
            state_key = issue.state.lower()
            state_limit = self.cfg.agent.max_concurrent_agents_by_state.get(state_key)
            if state_limit is not None:
                state_count = sum(
                    1 for e in self._state.running.values()
                    if e.issue_state.lower() == state_key
                )
                if state_count >= state_limit:
                    continue

            self._claim_and_start(issue, attempt=None)
            active_count += 1

    # ──────────────────────────────────────────
    # Agent lifecycle
    # ──────────────────────────────────────────

    def _claim_and_start(self, issue: Issue, attempt: Optional[int]) -> None:
        self._state.claimed.add(issue.id)
        # Run in a thread so we don't block the poll loop
        t = threading.Thread(
            target=self._run_agent,
            args=(issue, attempt),
            daemon=True,
            name=f"agent-{issue.workspace_key}",
        )
        t.start()

    def _run_agent(self, issue: Issue, attempt: Optional[int]) -> None:
        log.info("[%s] dispatching (attempt=%s)", issue.identifier, attempt)
        self.on_status_update(f"[{issue.identifier}] starting agent")

        # Prepare workspace
        try:
            workspace = self.wm.prepare(issue.workspace_key)
            self.wm.before_run(workspace)
        except Exception as e:
            log.error("[%s] workspace preparation failed: %s", issue.identifier, e)
            self._schedule_retry(issue, attempt, str(e))
            return

        # Build prompt
        try:
            prompt = render_prompt(self.workflow.prompt_template, issue, attempt)
        except Exception as e:
            log.error("[%s] prompt render failed: %s", issue.identifier, e)
            self._release(issue.id)
            return

        # Launch agent
        session = AgentSession(
            cfg=self.cfg.claude,
            workspace_path=workspace.path,
            prompt=prompt,
            issue_identifier=issue.identifier,
            attempt=attempt,
        )

        with self._lock:
            self._state.running[issue.id] = RunningEntry(
                issue_id=issue.id,
                issue_identifier=issue.identifier,
                issue_state=issue.state,
                session=session,
                attempt=attempt,
                started_at=time.monotonic(),
            )

        session.start()

        # Drain the update queue
        final_event = None
        final_message = ""
        output_chunks: list[str] = []
        updates_q = session.updates()

        timeout_s = self.cfg.claude.turn_timeout_ms / 1000
        deadline = time.monotonic() + timeout_s

        while True:
            try:
                update = updates_q.get(timeout=1.0)
            except Exception:
                if time.monotonic() > deadline:
                    session.cancel()
                    final_event = AgentEvent.TIMEOUT
                    final_message = "overall turn timeout"
                    break
                continue

            if update.event == AgentEvent.OUTPUT:
                output_chunks.append(update.message)
                # Print live output
                print(f"  [{issue.identifier}] {update.message}")
            elif update.event in (AgentEvent.DONE, AgentEvent.ERROR,
                                  AgentEvent.TIMEOUT, AgentEvent.CANCELLED):
                final_event = update.event
                final_message = update.message
                break

        # Post-run
        try:
            self.wm.after_run(workspace)
        except Exception:
            pass

        elapsed = time.monotonic() - (self._state.running.get(issue.id, RunningEntry(
            issue_id="", issue_identifier="", issue_state="",
            session=session, attempt=None, started_at=time.monotonic()
        )).started_at)

        with self._lock:
            self._state.running.pop(issue.id, None)

        self._handle_agent_result(issue, attempt, final_event, final_message, output_chunks, elapsed)

    def _handle_agent_result(
        self,
        issue: Issue,
        attempt: Optional[int],
        event: Optional[AgentEvent],
        message: str,
        output: list[str],
        elapsed_s: float,
    ) -> None:
        elapsed_str = f"{elapsed_s:.0f}s"

        if event == AgentEvent.DONE:
            log.info("[%s] agent done in %s", issue.identifier, elapsed_str)
            self.on_status_update(f"[{issue.identifier}] done ({elapsed_str})")
            # Post proof-of-work comment
            summary = "\n".join(output[-30:]) if output else "(no output)"
            self.tracker.add_comment(
                issue.id,
                f"**Symphony agent completed** ({elapsed_str})\n\n```\n{summary}\n```",
            )
            self._state.completed.add(issue.id)
            self._release(issue.id)

        elif event in (AgentEvent.ERROR, AgentEvent.TIMEOUT):
            log.warning("[%s] agent %s: %s", issue.identifier, event.value, message[:200])
            self._schedule_retry(issue, attempt, message)

        elif event == AgentEvent.CANCELLED:
            log.info("[%s] agent cancelled", issue.identifier)
            self._release(issue.id)

        else:
            log.warning("[%s] unexpected event: %s", issue.identifier, event)
            self._release(issue.id)

    def _stop_agent(self, issue_id: str, reason: str) -> None:
        entry = self._state.running.get(issue_id)
        if entry:
            log.info("[%s] stopping: %s", entry.issue_identifier, reason)
            entry.session.cancel()
            # Don't remove from running here - the thread will handle it
        self._cancel_retry(issue_id)
        # Clean workspace
        issue_identifier = (entry.issue_identifier if entry
                            else issue_id)
        try:
            self.wm.remove(issue_identifier)
        except Exception as e:
            log.warning("workspace remove failed: %s", e)
        self._release(issue_id)

    def _release(self, issue_id: str) -> None:
        with self._lock:
            self._state.claimed.discard(issue_id)
            self._cancel_retry(issue_id)

    def _cancel_retry(self, issue_id: str) -> None:
        entry = self._state.retry_queue.pop(issue_id, None)
        if entry and entry.timer:
            entry.timer.cancel()

    def _schedule_retry(self, issue: Issue, attempt: Optional[int], error: str) -> None:
        next_attempt = (attempt or 0) + 1
        max_backoff_s = self.cfg.agent.max_retry_backoff_ms / 1000
        delay_s = min(math.pow(2, next_attempt) + random.uniform(0, 2), max_backoff_s)

        log.info("[%s] retry #%d in %.0fs: %s", issue.identifier, next_attempt, delay_s, error[:100])
        self.on_status_update(f"[{issue.identifier}] retry #{next_attempt} in {delay_s:.0f}s")

        def _fire():
            with self._lock:
                self._state.retry_queue.pop(issue.id, None)
                if issue.id not in self._state.claimed:
                    self._claim_and_start(issue, attempt=next_attempt)

        timer = threading.Timer(delay_s, _fire)
        entry = RetryEntry(
            issue_id=issue.id,
            identifier=issue.identifier,
            attempt=next_attempt,
            due_at=time.monotonic() + delay_s,
            error=error,
            timer=timer,
        )
        with self._lock:
            self._state.retry_queue[issue.id] = entry
        timer.start()

    def _shutdown(self) -> None:
        log.info("symphony shutting down...")
        with self._lock:
            for issue_id, entry in list(self._state.running.items()):
                entry.session.cancel()
            for entry in self._state.retry_queue.values():
                if entry.timer:
                    entry.timer.cancel()
        log.info("symphony stopped")

    # ──────────────────────────────────────────
    # Status summary (for CLI display)
    # ──────────────────────────────────────────

    def status_summary(self) -> dict:
        with self._lock:
            return {
                "running": [
                    {
                        "identifier": e.issue_identifier,
                        "state": e.issue_state,
                        "attempt": e.attempt,
                        "elapsed_s": round(time.monotonic() - e.started_at),
                    }
                    for e in self._state.running.values()
                ],
                "retry_queue": [
                    {
                        "identifier": e.identifier,
                        "attempt": e.attempt,
                        "due_in_s": round(max(0, e.due_at - time.monotonic())),
                    }
                    for e in self._state.retry_queue.values()
                ],
                "completed": len(self._state.completed),
                "claimed": len(self._state.claimed),
            }
