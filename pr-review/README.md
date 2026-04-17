# PR Review Bot

Local automation that discovers unreviewed PRs labeled `team-compass` across
opendatahub-io repos, runs a tiered build/lint/test pipeline, optionally
smoke-tests on a live cluster, and produces AI-generated review reports using
CodeRabbit and Claude.

## Quick Start

```bash
# Review a single PR
./pr-review.sh opendatahub-io/opendatahub-operator 3420

# Scan all repos in repos.conf
./pr-review.sh
```

## Prerequisites

Install the following tools before setup:

| Tool | Required | Install | Purpose |
|------|----------|---------|---------|
| `gh` | yes | `dnf install gh` | GitHub PR discovery and checkout |
| `claude` | yes | [claude.ai/download](https://claude.ai/download) | AI code review |
| `coderabbit` | no | [docs.coderabbit.ai](https://docs.coderabbit.ai/guides/cli) | Additional AI review (used if available) |
| `oc` | no | [mirror.openshift.com](https://mirror.openshift.com/pub/openshift-v4/clients/ocp/latest/) | Cluster smoke tests (used if logged in) |
| `jq` | yes | `dnf install jq` | JSON parsing |

Authenticate `gh` before first use:

```bash
gh auth login
# or set GITHUB_TOKEN in your environment
```

## Setup

### 1. Secrets file

The systemd service needs credentials to call GitHub. Create a systemd-compatible
env file (no `export` prefix, just `KEY=value`):

```bash
# Generate from your existing ~/.secrets.env (or create manually)
grep -v 'GCLOUD_CREDENTIALS' ~/.secrets.env | grep -E '^export ' | sed 's/^export //' > ~/.secrets.systemd.env
chmod 600 ~/.secrets.systemd.env
```

At minimum it needs `GITHUB_TOKEN`. Add any other tokens the script may use
(e.g. `CLAUDE_CODE_USE_VERTEX`, `CLOUD_ML_REGION`, `ANTHROPIC_VERTEX_PROJECT_ID`
if using Claude via Vertex).

If you rotate tokens, regenerate this file.

### 2. Systemd timer

Symlink the units and enable the timer (runs at 10:00 and 15:00 daily):

```bash
mkdir -p ~/.config/systemd/user
ln -sf "$(pwd)/systemd/pr-review.service" ~/.config/systemd/user/
ln -sf "$(pwd)/systemd/pr-review.timer" ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now pr-review.timer
```

Verify:

```bash
systemctl --user list-timers pr-review.timer
```

### 3. Prompt notification (bash-it)

Shows an `⎘ N new` indicator in your powerline prompt when unread reports exist.

```bash
# Symlink the plugin into bash-it
ln -sf "$(pwd)/bashit-pr-review.bash" ~/.bash_it/custom/pr-review.bash

# Add to your .bashrc (before the bash-it source line):
export POWERLINE_RIGHT_PROMPT="pr_review in_vim clock battery user_info"
```

Shell commands:
- `pr-review-read` — mark all reports as read (clears the prompt indicator)
- `pr-review-ls` — list unread report files

## Configuration

| File | Purpose |
|------|---------|
| `repos.conf` | Repos to watch — one `owner/repo` per line |
| `review-prompt.md` | Claude system prompt template (edit to tune review style) |
| `reports/` | Generated review documents (gitignored) |
| `bashit-pr-review.bash` | bash-it powerline prompt plugin |
| `systemd/pr-review.service` | Systemd service unit |
| `systemd/pr-review.timer` | Systemd timer unit (schedule) |

## How It Works

1. Reads repos from `repos.conf`
2. For each repo, finds open PRs with the `team-compass` label
3. Skips PRs you've already reviewed/commented on, or that have a report today
4. Classifies changes into tiers based on what files the PR touches:
   - **tier1** (docs, tests, CI, scripts) — lint + unit tests only
   - **tier2** (Go source, pkg/, Makefile) — lint + unit tests + full build
   - **tier3** (API types, controllers, CRDs, manifests) — full pipeline including cluster smoke test
5. Checks out the PR branch into `~/.cache/pr-review/<repo>/`
6. Runs the gated pipeline (each stage only if the prior stage passed)
7. Runs CodeRabbit review if the CLI is installed
8. Sends the diff, test output, and CodeRabbit findings to Claude for AI review
9. Writes report to `reports/<repo>-PR<number>-<date>.md`
10. Updates `reports/index.md` with a summary row

## Re-reviewing a PR

Delete the report file to force a new review:

```bash
rm reports/opendatahub-operator-PR123-2026-04-16.md
./pr-review.sh opendatahub-io/opendatahub-operator 123
```

## Monitoring

```bash
# Check when next run is scheduled
systemctl --user list-timers pr-review.timer

# Watch logs live
journalctl --user -u pr-review.service -f

# See past runs
journalctl --user -u pr-review.service --since "24 hours ago"

# Trigger a run now
systemctl --user start pr-review.service
```
