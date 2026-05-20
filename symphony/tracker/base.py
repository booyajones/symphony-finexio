"""Abstract tracker interface. Implement this to support any issue tracker."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Issue:
    id: str
    identifier: str          # human-readable key, e.g. "FIN-42" or "#42"
    title: str
    description: Optional[str]
    state: str
    priority: Optional[int] = None
    labels: list[str] = field(default_factory=list)
    url: Optional[str] = None
    branch_name: Optional[str] = None
    blocked_by: list[dict] = field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def workspace_key(self) -> str:
        """Sanitized filesystem-safe identifier."""
        import re
        return re.sub(r"[^A-Za-z0-9._-]", "_", self.identifier)


class IssueState:
    """Sentinel strings - override in workflow config."""
    TODO = "todo"
    IN_PROGRESS = "in progress"
    HUMAN_REVIEW = "human review"
    DONE = "done"
    CANCELLED = "cancelled"
    CLOSED = "closed"


class TrackerClient(ABC):
    """Abstract interface all tracker adapters must implement."""

    @abstractmethod
    def fetch_active_issues(self, active_states: list[str]) -> list[Issue]:
        """Return all issues in any of the given states."""
        ...

    @abstractmethod
    def fetch_issue(self, issue_id: str) -> Optional[Issue]:
        """Return a single issue by ID, or None if not found."""
        ...

    @abstractmethod
    def set_issue_state(self, issue_id: str, state: str) -> None:
        """Transition the issue to the given state."""
        ...

    @abstractmethod
    def add_comment(self, issue_id: str, body: str) -> None:
        """Post a comment on the issue."""
        ...

    def close(self) -> None:
        """Optional cleanup."""
