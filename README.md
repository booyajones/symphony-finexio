<!-- VOICE-GUARD-OFF -->
# Symphony (Finexio Edition)

Autonomous coding agent orchestrator. Based on the [OpenAI Symphony spec](https://github.com/openai/symphony).

**Tracker-agnostic.** Works with GitHub Issues out of the box. Linear adapter included.
**Agent: Claude Code.** Uses `claude --print` for non-interactive agentic runs.
**Global.** One daemon, multiple repos, any tracker.

## How it works

1. You label a GitHub Issue `symphony:ready`
2. Symphony picks it up within 30 seconds
3. Creates an isolated workspace (clones the repo, installs deps)
4. Launches `claude --print` with the WORKFLOW.md prompt + issue context
5. Agent implements the fix, creates a PR, comments on the issue
6. Labels the issue `symphony:done` (or `symphony:human-review` if stuck)
7. You review and merge. That's it.

## Quick start

```bash
# Install deps
pip install -r requirements.txt

# Copy WORKFLOW.md into your repo root and customize it
cp WORKFLOW.md ~/code/my-repo/WORKFLOW.md

# Dry-run to validate config
python symphony.py ~/code/my-repo/WORKFLOW.md --dry-run

# Start the daemon
python symphony.py ~/code/my-repo/WORKFLOW.md
```

## State machine (GitHub labels)

```
                   symphony:ready
                        |
                        v
               symphony:in-progress  (set by Symphony on dispatch)
                  /           \
      symphony:done     symphony:human-review
                              |
                         (you decide)
                         symphony:ready  (re-queue)
                         symphony:cancelled
```

Symphony creates all labels automatically on first run.

## WORKFLOW.md reference

Drop a `WORKFLOW.md` in each repo root. YAML front matter = config. Markdown body = agent prompt.

Key config sections:

| Section | Purpose |
|---|---|
| `tracker` | Which tracker, which repo, which states are "active" |
| `polling` | How often to check for new issues |
| `workspace.root` | Where per-issue dirs are created |
| `hooks.after_create` | Shell script to clone + install on workspace creation |
| `hooks.before_run` | Shell script to run before each agent attempt |
| `agent.max_concurrent_agents` | How many issues to work in parallel |
| `claude.allowed_tools` | Which Claude tools the agent can use |
| `claude.approval_policy` | "never" = fully autonomous, "on-request" = safer |

## Multi-repo setup

Run one Symphony instance per repo (each gets its own WORKFLOW.md), or run multiple in parallel:

```bash
# Terminal 1
python symphony.py ~/code/finexio-portal-pro/WORKFLOW.md

# Terminal 2
python symphony.py ~/code/finexio-skills/WORKFLOW.md
```

Or wrap in a simple shell script to start all at once.

## Linear support

Set `tracker.kind: linear` and `LINEAR_API_KEY` env var. Everything else works the same.

## Requirements

- Python 3.11+
- `gh` CLI (authenticated)
- `claude` CLI (authenticated, Claude Code)
- `pyyaml`, `requests` (`pip install -r requirements.txt`)
