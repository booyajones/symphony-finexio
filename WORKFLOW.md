<!-- VOICE-GUARD-OFF -->
---
# Symphony WORKFLOW.md - drop this file into any repo root.
# Symphony reads it on startup. Edit to customize behavior per repo.

tracker:
  kind: github              # "github" (default) or "linear"
  repo: ""                  # "owner/repo" - blank = auto-detect from git remote
  active_states:
    - ready                 # Issues labeled "symphony:ready" are picked up
    - in-progress           # Issues labeled "symphony:in-progress" stay tracked
  terminal_states:
    - done
    - cancelled
    - closed
    - duplicate

polling:
  interval_ms: 30000        # Check for new work every 30 seconds

workspace:
  root: ~/code/symphony-workspaces   # Each issue gets its own subdirectory here

hooks:
  after_create: |
    # Runs once when a workspace is first created for an issue.
    # Clone the repo, set up env, install deps.
    git clone git@github.com:booyajones/finexio-portal-pro.git .
    cp ~/.env .env
    npm install --silent

  before_run: |
    # Runs before each agent attempt.
    git pull --rebase --autostash
    npm run typecheck 2>/dev/null || true

  after_run: |
    # Runs after agent completes (success or failure).
    echo "agent run finished"

agent:
  max_concurrent_agents: 3   # Max parallel issues being worked on
  max_turns: 40              # Claude Code turns per issue before timeout
  max_retry_backoff_ms: 300000

  # Optional per-state concurrency caps:
  # max_concurrent_agents_by_state:
  #   ready: 2
  #   in-progress: 3

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
  approval_policy: on-request   # "never" = fully autonomous, "on-request" = safer
  turn_timeout_ms: 3600000      # 1 hour max per issue
  stall_timeout_ms: 300000      # Cancel if no output for 5 minutes
---

You are a senior software engineer working autonomously on a GitHub issue.

## Your Assignment

**Issue**: {{ issue.identifier }} - {{ issue.title }}
**URL**: {{ issue.url }}
**Labels**: {{ issue.labels }}
{% if attempt %}**Retry attempt**: {{ attempt }}{% endif %}

## Issue Description

{{ issue.description }}

## Working Rules

1. Read CLAUDE.md first for project-specific instructions and quality standards.
2. Follow the GSD workflow if .planning/ exists in this repo.
3. Write clean, typed, tested code. No half-finished implementations.
4. Run existing tests before and after your changes. Fix anything that breaks.
5. Create a PR with a clear title and description when done.
6. Add a comment on the GitHub issue with:
   - What you did
   - PR link
   - Test results
   - Any open questions for human review
7. Apply the label "symphony:done" to the issue when the PR is ready for review.
   Apply "symphony:human-review" if you need a human decision before proceeding.

## Quality Gates (required)

- TypeScript: no type errors (npm run typecheck)
- Python: no import errors, passes existing pytest suite
- No secrets or credentials in committed code
- No em dashes in any user-facing text
- All Finexio-facing content follows voice rules (no banned words)

## If you are stuck

If you encounter a blocker you cannot resolve autonomously:
1. Document exactly what is blocked and why in a GitHub comment
2. Apply the label "symphony:human-review"
3. Stop working

Begin by reading CLAUDE.md, then understand the issue, then implement the solution.
