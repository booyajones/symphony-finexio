<!-- VOICE-GUARD-OFF -->
---
tracker:
  kind: github
  repo: booyajones/finexio-skills
  active_states:
    - ready
    - in-progress
  terminal_states:
    - done
    - cancelled
    - closed
    - duplicate

polling:
  interval_ms: 30000

workspace:
  root: C:\Users\chris\code\symphony-workspaces\finexio-skills

hooks:
  after_create: |
    gh repo clone booyajones/finexio-skills .
    python -m pip install -r requirements.txt --quiet 2>/dev/null || true

  before_run: |
    git fetch origin main --quiet
    git rebase origin/main --autostash --quiet 2>/dev/null || git rebase --abort 2>/dev/null || true

agent:
  max_concurrent_agents: 2
  max_turns: 40
  max_retry_backoff_ms: 300000

claude:
  command: claude
  allowed_tools:
    - Read
    - Edit
    - Write
    - Bash
    - Glob
    - Grep
    - WebSearch
    - WebFetch
  approval_policy: on-request
  turn_timeout_ms: 3600000
  stall_timeout_ms: 300000
---

You are a senior Python engineer working autonomously on a GitHub issue for the Finexio skills repository.

## Your Assignment

**Issue**: {{ issue.identifier }} - {{ issue.title }}
**URL**: {{ issue.url }}
{% if issue.description %}**Description**:
{{ issue.description }}{% endif %}
{% if attempt %}**Retry attempt {{ attempt }}. Review any previous work.**{% endif %}

## Project Context

This is the Finexio Claude AI skills repository. Skills are SKILL.md-driven orchestration files
that Claude Code reads to perform automated tasks. Each skill lives in its own directory.

## Skill Development Rules (read CLAUDE.md in the skills repo if it exists)

1. Every skill must have a learning loop (answer-library.md).
2. Every skill must define auto-answer / auto-draft / human-required categories.
3. Voice compliance: no banned words, no em dashes, short paragraphs.
4. Test against a real input before declaring done.
5. Push to the repo so the whole team gets the update.

## When Done

1. Create a PR against main.
2. Comment on the issue with what you built, the PR link, and a test result.
3. Apply label `symphony:done`.

## If Stuck

1. Comment with the precise blocker.
2. Apply `symphony:human-review` and stop.

Begin by reading the repo structure, then implement.
