"""GitHub Issues tracker adapter.

Uses the gh CLI (already installed) so no extra auth setup needed.
Labels drive state: add a label matching an active_state to a GH issue
and Symphony picks it up.

Label convention (customize in WORKFLOW.md):
  active_states:  ["symphony:ready", "symphony:in-progress"]
  terminal_states: ["symphony:done", "symphony:cancelled"]
"""

import json
import subprocess
import logging
from datetime import datetime, timezone
from typing import Optional

from .base import TrackerClient, Issue

log = logging.getLogger(__name__)


class GitHubIssuesClient(TrackerClient):
    """
    Adapter that treats GitHub Issue labels as state machine transitions.

    repo: "owner/repo" string. If None, uses current directory's remote.
    """

    def __init__(self, repo: Optional[str] = None):
        self.repo = repo
        self._repo_flag = ["--repo", repo] if repo else []

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _gh(self, *args) -> dict | list:
        cmd = ["gh"] + list(args) + self._repo_flag
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def _gh_raw(self, *args) -> str:
        cmd = ["gh"] + list(args) + self._repo_flag
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout

    def _parse_issue(self, raw: dict) -> Issue:
        labels = [lbl["name"] if isinstance(lbl, dict) else lbl
                  for lbl in raw.get("labels", [])]

        # State = the symphony:* label if present, else "open"/"closed"
        state = raw.get("state", "open").lower()
        symphony_labels = [l for l in labels if l.startswith("symphony:")]
        if symphony_labels:
            state = symphony_labels[0].removeprefix("symphony:")

        created = raw.get("createdAt") or raw.get("created_at")
        updated = raw.get("updatedAt") or raw.get("updated_at")

        def _parse_dt(s):
            if not s:
                return None
            try:
                return datetime.fromisoformat(s.replace("Z", "+00:00"))
            except Exception:
                return None

        return Issue(
            id=str(raw.get("number", raw.get("id", ""))),
            identifier=f"#{raw.get('number', raw.get('id', ''))}",
            title=raw.get("title", ""),
            description=raw.get("body") or raw.get("description"),
            state=state,
            labels=[l for l in labels if not l.startswith("symphony:")],
            url=raw.get("url") or raw.get("html_url"),
            created_at=_parse_dt(created),
            updated_at=_parse_dt(updated),
        )

    # ------------------------------------------------------------------
    # TrackerClient interface
    # ------------------------------------------------------------------

    def fetch_active_issues(self, active_states: list[str]) -> list[Issue]:
        issues = []
        seen = set()

        for state_label in active_states:
            label = f"symphony:{state_label.lower()}"
            try:
                raw_list = self._gh(
                    "issue", "list",
                    "--label", label,
                    "--state", "open",
                    "--json", "number,title,body,labels,state,url,createdAt,updatedAt",
                    "--limit", "100",
                )
                for raw in (raw_list if isinstance(raw_list, list) else []):
                    num = str(raw.get("number"))
                    if num not in seen:
                        seen.add(num)
                        issues.append(self._parse_issue(raw))
            except subprocess.CalledProcessError as e:
                log.warning("gh issue list failed for label %s: %s", label, e.stderr)

        issues.sort(key=lambda i: i.created_at or datetime.min.replace(tzinfo=timezone.utc))
        return issues

    def fetch_issue(self, issue_id: str) -> Optional[Issue]:
        try:
            raw = self._gh(
                "issue", "view", issue_id,
                "--json", "number,title,body,labels,state,url,createdAt,updatedAt",
            )
            return self._parse_issue(raw)
        except subprocess.CalledProcessError:
            return None

    def set_issue_state(self, issue_id: str, new_state: str) -> None:
        """
        Remove all symphony:* labels then add symphony:<new_state>.
        Also close/reopen the GH issue if transitioning to terminal states.
        """
        issue = self.fetch_issue(issue_id)
        if not issue:
            return

        # Remove existing symphony labels
        all_labels_raw = self._gh(
            "issue", "view", issue_id,
            "--json", "labels",
        )
        existing = [l["name"] for l in all_labels_raw.get("labels", [])]
        to_remove = [l for l in existing if l.startswith("symphony:")]
        if to_remove:
            subprocess.run(
                ["gh", "issue", "edit", issue_id, "--remove-label", ",".join(to_remove)]
                + self._repo_flag,
                capture_output=True, check=False,
            )

        # Add new state label (ensure it exists)
        new_label = f"symphony:{new_state.lower()}"
        self._ensure_label(new_label)
        subprocess.run(
            ["gh", "issue", "edit", issue_id, "--add-label", new_label]
            + self._repo_flag,
            capture_output=True, check=False,
        )

        # Close GH issue if terminal
        terminal = {"done", "cancelled", "closed", "duplicate"}
        if new_state.lower() in terminal:
            subprocess.run(
                ["gh", "issue", "close", issue_id] + self._repo_flag,
                capture_output=True, check=False,
            )

    def add_comment(self, issue_id: str, body: str) -> None:
        subprocess.run(
            ["gh", "issue", "comment", issue_id, "--body", body] + self._repo_flag,
            capture_output=True, check=False,
        )

    def _ensure_label(self, name: str, color: str = "0075ca") -> None:
        """Create label if it doesn't exist."""
        subprocess.run(
            ["gh", "label", "create", name, "--color", color, "--force"]
            + self._repo_flag,
            capture_output=True, check=False,
        )
