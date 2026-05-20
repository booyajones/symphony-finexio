"""WORKFLOW.md parser - YAML front matter + Liquid-compatible prompt template."""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


@dataclass
class WorkflowDefinition:
    config: dict
    prompt_template: str
    source_path: Path


class WorkflowError(Exception):
    pass


def load_workflow(path: Path) -> WorkflowDefinition:
    """Parse WORKFLOW.md into a WorkflowDefinition."""
    if not path.exists():
        raise WorkflowError(f"missing_workflow_file: {path}")

    text = path.read_text(encoding="utf-8")

    # Strip leading HTML/tool comments (e.g. <!-- VOICE-GUARD-OFF -->) before parsing
    stripped = text.lstrip()
    while stripped.startswith("<!--"):
        end = stripped.find("-->")
        if end == -1:
            break
        stripped = stripped[end + 3:].lstrip()
    text = stripped

    config: dict = {}
    prompt_body = text

    if text.startswith("---"):
        parts = re.split(r"^---\s*$", text, maxsplit=2, flags=re.MULTILINE)
        if len(parts) >= 3:
            front_matter_text = parts[1]
            prompt_body = parts[2]
            if yaml is None:
                raise WorkflowError("PyYAML is required: pip install pyyaml")
            parsed = yaml.safe_load(front_matter_text)
            if not isinstance(parsed, dict):
                raise WorkflowError("workflow_front_matter_not_a_map")
            config = parsed

    return WorkflowDefinition(
        config=config,
        prompt_template=prompt_body.strip(),
        source_path=path,
    )


def render_prompt(template: str, issue, attempt: Optional[int] = None) -> str:
    """
    Render the prompt template with issue fields.
    Supports {{ variable }} syntax (Liquid-compatible subset).
    Supports {{ issue.field }} and {{ attempt }}.
    """
    context: dict[str, Any] = {
        "attempt": attempt,
        "issue": {
            "id": issue.id,
            "identifier": issue.identifier,
            "title": issue.title,
            "description": issue.description or "",
            "state": issue.state,
            "priority": issue.priority,
            "labels": issue.labels,
            "url": issue.url or "",
            "branch_name": issue.branch_name or "",
            "blocked_by": issue.blocked_by,
        },
    }

    def _resolve(match: re.Match) -> str:
        expr = match.group(1).strip()
        parts = expr.split(".")
        val: Any = context
        for part in parts:
            if isinstance(val, dict):
                if part not in val:
                    raise WorkflowError(f"template_render_error: unknown variable '{expr}'")
                val = val[part]
            else:
                raise WorkflowError(f"template_render_error: cannot traverse '{expr}'")
        if val is None:
            return ""
        return str(val)

    return re.sub(r"\{\{\s*([^}]+)\s*\}\}", _resolve, template)
