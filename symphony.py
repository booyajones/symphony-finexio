#!/usr/bin/env python3
"""
Symphony - autonomous coding agent orchestrator.
Tracker-agnostic. Works with GitHub Issues (default), Linear, or any adapter.

Usage:
    python symphony.py [WORKFLOW.md path]
    python symphony.py --status    # print current status and exit
    python symphony.py --help
"""

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

# ── path setup ────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

from symphony.config import build_config
from symphony.orchestrator import Orchestrator
from symphony.tracker.base import TrackerClient
from symphony.tracker.github import GitHubIssuesClient
from symphony.workflow import WorkflowError, load_workflow
from symphony.workspace import WorkspaceManager

log = logging.getLogger("symphony")


# ── logging setup ─────────────────────────────────────────────────────

def setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "symphony.log"

    fmt = "%(asctime)s %(levelname)-8s %(name)s  %(message)s"
    datefmt = "%Y-%m-%dT%H:%M:%S"

    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding="utf-8"),
    ]
    logging.basicConfig(level=logging.INFO, format=fmt, datefmt=datefmt, handlers=handlers)


# ── tracker factory ───────────────────────────────────────────────────

def build_tracker(cfg) -> TrackerClient:
    kind = cfg.tracker.kind.lower()

    if kind == "github":
        repo = cfg.tracker.repo or None
        return GitHubIssuesClient(repo=repo or None)

    if kind == "linear":
        from symphony.tracker.linear import LinearClient
        api_key = cfg.tracker.api_key or os.environ.get("LINEAR_API_KEY", "")
        return LinearClient(api_key=api_key, project_slug=cfg.tracker.repo)

    raise ValueError(f"Unknown tracker kind: {kind!r}. Supported: github, linear")


# ── status surface ────────────────────────────────────────────────────

class StatusSurface:
    """Simple terminal status display."""

    def __init__(self):
        self._messages: list[str] = []
        self._lock = threading.Lock()

    def update(self, msg: str) -> None:
        with self._lock:
            ts = time.strftime("%H:%M:%S")
            line = f"[{ts}] {msg}"
            self._messages.append(line)
            print(line, flush=True)

    def print_summary(self, orchestrator: Orchestrator) -> None:
        summary = orchestrator.status_summary()
        print("\n── Symphony Status ──────────────────────────────")
        print(f"  running  : {len(summary['running'])}")
        print(f"  retrying : {len(summary['retry_queue'])}")
        print(f"  completed: {summary['completed']}")
        if summary["running"]:
            print("\n  Active agents:")
            for r in summary["running"]:
                print(f"    {r['identifier']:20s}  {r['state']:20s}  {r['elapsed_s']}s  (attempt {r['attempt']})")
        if summary["retry_queue"]:
            print("\n  Retry queue:")
            for r in summary["retry_queue"]:
                print(f"    {r['identifier']:20s}  retry #{r['attempt']}  in {r['due_in_s']}s")
        print("─────────────────────────────────────────────────\n")


# ── main ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="symphony",
        description="Autonomous coding agent orchestrator — tracker-agnostic, Claude-powered.",
    )
    parser.add_argument(
        "workflow",
        nargs="?",
        default="WORKFLOW.md",
        help="Path to WORKFLOW.md (default: ./WORKFLOW.md)",
    )
    parser.add_argument(
        "--logs-root",
        default="./log",
        help="Directory for structured logs (default: ./log)",
    )
    parser.add_argument(
        "--status-interval",
        type=int,
        default=60,
        help="Seconds between status summaries printed to stdout (default: 60)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Load config and validate workflow, then exit without running.",
    )
    args = parser.parse_args()

    workflow_path = Path(args.workflow).resolve()
    log_dir = Path(args.logs_root).resolve()

    setup_logging(log_dir)

    # ── load workflow ─────────────────────────────────────────────────
    try:
        workflow = load_workflow(workflow_path)
    except WorkflowError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    cfg = build_config(workflow.config)
    log.info("workflow loaded: %s", workflow_path)
    log.info("tracker: %s  repo: %s", cfg.tracker.kind, cfg.tracker.repo or "(auto)")
    log.info("active states: %s", cfg.tracker.active_states)
    log.info("max concurrent agents: %d", cfg.agent.max_concurrent_agents)
    log.info("poll interval: %dms", cfg.polling.interval_ms)

    if args.dry_run:
        print("Dry run OK. Config:")
        print(json.dumps(
            {
                "tracker": cfg.tracker.__dict__,
                "polling": cfg.polling.__dict__,
                "agent": cfg.agent.__dict__,
                "workspace_root": cfg.workspace.root,
            },
            indent=2,
            default=str,
        ))
        return

    # ── build components ──────────────────────────────────────────────
    try:
        tracker = build_tracker(cfg)
    except Exception as e:
        print(f"ERROR building tracker: {e}", file=sys.stderr)
        sys.exit(1)

    workspace_manager = WorkspaceManager(cfg.workspace, cfg.hooks)
    status = StatusSurface()
    orch = Orchestrator(cfg, tracker, workflow, workspace_manager, on_status_update=status.update)

    # ── periodic status printer ───────────────────────────────────────
    def _status_loop():
        while not stop.is_set():
            stop.wait(timeout=args.status_interval)
            if not stop.is_set():
                status.print_summary(orch)

    stop = threading.Event()
    st = threading.Thread(target=_status_loop, daemon=True)
    st.start()

    # ── signal handling ───────────────────────────────────────────────
    def _handle_signal(sig, frame):
        log.info("received signal %s, shutting down...", sig)
        stop.set()
        orch.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # ── run ───────────────────────────────────────────────────────────
    print(f"""
╔══════════════════════════════════════════════╗
║            Symphony  —  Finexio              ║
║  tracker  : {cfg.tracker.kind:<32} ║
║  repo     : {(cfg.tracker.repo or 'auto'):<32} ║
║  agents   : {cfg.agent.max_concurrent_agents:<32} ║
║  poll     : {cfg.polling.interval_ms}ms{'':<27} ║
╚══════════════════════════════════════════════╝
Press Ctrl+C to stop.
""")

    try:
        orch.run()
    finally:
        stop.set()
        tracker.close()
        log.info("symphony exited cleanly")


if __name__ == "__main__":
    main()
