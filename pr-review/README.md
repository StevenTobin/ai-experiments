# PR Review Bot

Local automation that discovers unreviewed PRs labeled `team-compass` across
opendatahub-io repos, checks out each branch, runs build/lint/tests, optionally
smoke-tests on a live cluster, and produces an AI-generated review report.

## Quick Start

```bash
# Review a single PR
./pr-review.sh opendatahub-io/opendatahub-operator 3420

# Scan all repos in repos.conf
./pr-review.sh
```

## Setup

Prerequisites: `gh` (authenticated), `claude` CLI, `oc` (optional, for cluster tests).

The systemd timer runs every 4 hours automatically. To set it up:

```bash
mkdir -p ~/.config/systemd/user
ln -sf "$(pwd)/systemd/pr-review.service" ~/.config/systemd/user/
ln -sf "$(pwd)/systemd/pr-review.timer" ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now pr-review.timer
```

Check timer status:

```bash
systemctl --user status pr-review.timer
systemctl --user list-timers
journalctl --user -u pr-review.service -f  # watch logs
```

## Prompt Notification (bash-it)

Shows an `⎘ N new` indicator in your powerline prompt when unread reports exist.

```bash
# Symlink the plugin into bash-it
ln -sf "$(pwd)/bashit-pr-review.bash" ~/.bash_it/custom/pr-review.bash

# Add to your .bashrc (before the bash-it source line):
export POWERLINE_RIGHT_PROMPT="pr_review in_vim clock battery user_info"
```

Shell commands:
- `pr-review-read` — mark all reports as read (clears the indicator)
- `pr-review-ls` — list unread report files

## Configuration

- **repos.conf** — one `owner/repo` per line
- **review-prompt.md** — Claude system prompt template (edit to tune review style)
- **reports/** — generated review documents (gitignored)
- **bashit-pr-review.bash** — bash-it powerline prompt plugin

## How It Works

1. Reads repos from `repos.conf`
2. For each repo, finds open PRs with the `team-compass` label
3. Skips PRs you've already reviewed/commented on, or that have a report today
4. Classifies changes into tiers: tier1 (docs/tests), tier2 (source), tier3 (API/controllers)
5. Checks out the PR branch into `~/.cache/pr-review/<repo>/`
6. Runs lint and unit tests (always)
7. Runs full build (tier2+ only, and only if lint+tests pass)
8. If tier3 and `oc whoami` succeeds: builds operator image, deploys, creates DSC/DSCI, verifies Ready, cleans up
9. Sends the diff + all test output to Claude for AI review
10. Writes report to `reports/<repo>-PR<number>-<date>.md`

## Re-reviewing a PR

Delete the report file to force a new review:

```bash
rm reports/opendatahub-operator-PR123-2026-04-16.md
./pr-review.sh opendatahub-io/opendatahub-operator 123
```

## Manual Trigger (systemd)

```bash
systemctl --user start pr-review.service
journalctl --user -u pr-review.service --since "5 min ago"
```
