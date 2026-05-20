"""Linear tracker adapter (matches the Symphony spec reference implementation)."""

import logging
import os
from datetime import datetime
from typing import Optional

try:
    import requests
except ImportError:
    requests = None  # type: ignore

from .base import TrackerClient, Issue

log = logging.getLogger(__name__)

LINEAR_GQL = "https://api.linear.app/graphql"


class LinearClient(TrackerClient):
    def __init__(self, api_key: Optional[str] = None, project_slug: Optional[str] = None):
        if requests is None:
            raise RuntimeError("pip install requests to use the Linear adapter")
        self.api_key = api_key or os.environ["LINEAR_API_KEY"]
        self.project_slug = project_slug
        self._session = requests.Session()
        self._session.headers["Authorization"] = self.api_key
        self._session.headers["Content-Type"] = "application/json"

    def _gql(self, query: str, variables: dict = None) -> dict:
        resp = self._session.post(
            LINEAR_GQL, json={"query": query, "variables": variables or {}}
        )
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise RuntimeError(data["errors"])
        return data["data"]

    def _parse_issue(self, node: dict) -> Issue:
        labels = [e["node"]["name"] for e in node.get("labels", {}).get("edges", [])]
        blockers = [
            {
                "id": r["node"]["id"],
                "identifier": r["node"].get("identifier"),
                "state": r["node"].get("state", {}).get("name"),
            }
            for r in node.get("relations", {}).get("edges", [])
            if r["node"].get("type") == "blocks"
        ]

        def _dt(s):
            return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None

        return Issue(
            id=node["id"],
            identifier=node.get("identifier", node["id"]),
            title=node.get("title", ""),
            description=node.get("description"),
            state=node.get("state", {}).get("name", ""),
            priority=node.get("priority"),
            labels=labels,
            url=node.get("url"),
            branch_name=node.get("branchName"),
            blocked_by=blockers,
            created_at=_dt(node.get("createdAt")),
            updated_at=_dt(node.get("updatedAt")),
        )

    _ISSUE_FIELDS = """
        id identifier title description priority url branchName
        createdAt updatedAt
        state { name }
        labels(first: 20) { edges { node { name } } }
        relations(first: 10) { edges { node { type id identifier state { name } } } }
    """

    def fetch_active_issues(self, active_states: list[str]) -> list[Issue]:
        state_filter = " ".join(f'"{s}"' for s in active_states)
        q = f"""
        query {{
          issues(filter: {{ state: {{ name: {{ in: [{state_filter}] }} }} }}, first: 100) {{
            edges {{ node {{ {self._ISSUE_FIELDS} }} }}
          }}
        }}
        """
        data = self._gql(q)
        issues = [
            self._parse_issue(e["node"])
            for e in data.get("issues", {}).get("edges", [])
        ]
        if self.project_slug:
            issues = [i for i in issues if self._in_project(i)]
        issues.sort(key=lambda i: (i.priority or 999, i.created_at or datetime.min))
        return issues

    def _in_project(self, issue: Issue) -> bool:
        return True  # TODO: filter by project_slug via team key prefix

    def fetch_issue(self, issue_id: str) -> Optional[Issue]:
        q = f"""
        query {{
          issue(id: "{issue_id}") {{ {self._ISSUE_FIELDS} }}
        }}
        """
        try:
            data = self._gql(q)
            node = data.get("issue")
            return self._parse_issue(node) if node else None
        except Exception:
            return None

    def set_issue_state(self, issue_id: str, state: str) -> None:
        # Resolve state ID
        q = f"""
        query {{
          workflowStates(filter: {{ name: {{ eq: "{state}" }} }}) {{
            nodes {{ id name }}
          }}
        }}
        """
        data = self._gql(q)
        nodes = data.get("workflowStates", {}).get("nodes", [])
        if not nodes:
            log.warning("Linear: state %r not found", state)
            return
        state_id = nodes[0]["id"]
        self._gql(
            'mutation ($id: String! $sid: String!) { issueUpdate(id: $id input: { stateId: $sid }) { success } }',
            {"id": issue_id, "sid": state_id},
        )

    def add_comment(self, issue_id: str, body: str) -> None:
        self._gql(
            'mutation ($id: String! $body: String!) { commentCreate(input: { issueId: $id body: $body }) { success } }',
            {"id": issue_id, "body": body},
        )

    def close(self) -> None:
        self._session.close()
