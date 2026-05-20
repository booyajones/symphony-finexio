"""Workspace manager - per-issue filesystem isolation + lifecycle hooks."""

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import HooksConfig, WorkspaceConfig

log = logging.getLogger(__name__)


@dataclass
class Workspace:
    path: Path
    workspace_key: str
    created_now: bool


class WorkspaceManager:
    def __init__(self, cfg: WorkspaceConfig, hooks: HooksConfig):
        self.cfg = cfg
        self.hooks = hooks
        self.root = Path(cfg.root)
        self.root.mkdir(parents=True, exist_ok=True)

    def workspace_path(self, workspace_key: str) -> Path:
        return self.root / workspace_key

    def prepare(self, workspace_key: str) -> Workspace:
        path = self.workspace_path(workspace_key)
        created_now = not path.exists()

        if created_now:
            path.mkdir(parents=True)
            log.info("workspace created: %s", path)
            if self.hooks.after_create:
                self._run_hook("after_create", self.hooks.after_create, path)

        return Workspace(path=path, workspace_key=workspace_key, created_now=created_now)

    def before_run(self, workspace: Workspace) -> None:
        if self.hooks.before_run:
            self._run_hook("before_run", self.hooks.before_run, workspace.path)

    def after_run(self, workspace: Workspace) -> None:
        if self.hooks.after_run:
            try:
                self._run_hook("after_run", self.hooks.after_run, workspace.path)
            except Exception as e:
                log.warning("after_run hook failed (ignored): %s", e)

    def remove(self, workspace_key: str) -> None:
        path = self.workspace_path(workspace_key)
        if not path.exists():
            return
        if self.hooks.before_remove:
            try:
                self._run_hook("before_remove", self.hooks.before_remove, path)
            except Exception as e:
                log.warning("before_remove hook failed (ignored): %s", e)
        shutil.rmtree(path, ignore_errors=True)
        log.info("workspace removed: %s", path)

    def _run_hook(self, name: str, script: str, cwd: Path) -> None:
        timeout_s = self.hooks.timeout_ms / 1000
        log.debug("running hook %s in %s", name, cwd)
        result = subprocess.run(
            script,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"hook {name!r} failed (exit {result.returncode}): {result.stderr[:500]}"
            )
        if result.stdout:
            log.debug("hook %s stdout: %s", name, result.stdout[:500])
