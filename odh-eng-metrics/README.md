# ODH Engineering Metrics

Engineering metrics and intelligence for the
[opendatahub-operator](https://github.com/opendatahub-io/opendatahub-operator) project.
Tracks the full delivery pipeline from PR merge through downstream release,
combining [DORA metrics](https://dora.dev/guides/dora-metrics-four-keys/),
CI efficiency analysis, component-level failure intelligence, code risk scoring,
and AI agent context exports.

## What's included

- **DORA metrics** — Deployment Frequency, Lead Time, Change Failure Rate, MTTR
- **CI efficiency** — First-pass success rate, retest tax, cycle duration, per-component health
- **Engineering intelligence** — Component CI health, Jira correlation, code risk, infra vs code failures
- **Failure pattern analysis** — Error clustering, flake detection, root cause signals
- **AI agent exports** — Structured JSON context for LLM-based test fix agents
- **Reports** — Weekly digest, per-PR investigation, failure patterns

## Delivery pipeline

```
opendatahub-operator/main  →(fast-forward)→  stable  →(fast-forward)→  rhoai
                                                                          ↓
                                                            rhods-operator/main → rhoai-x.y branches
                                                                                   ↓
                                                                              Konflux builds → production
```

Actual deployments to production happen outside both repositories (via Konflux),
so we use proxy signals to approximate delivery events.

## Quick start

```bash
# Install dependencies
pip install -r requirements.txt

# Optional: set a GitHub token for the prerelease flag API call
export GITHUB_TOKEN=ghp_...

# Collect data: clones repos (first run ~60s), then parses git history (~30s)
make collect

# Print a text report
make report

# Or get JSON output
python3 cli.py report --json-output

# Launch Grafana dashboards (requires Docker)
make dashboard
# → http://localhost:3001 (admin/admin)

# Stop the dashboard
make dashboard-down
```

## Reports and AI agent integration

```bash
# Per-PR failure investigation (markdown)
make investigate PR=3346

# Weekly CI health digest
make digest

# Recurring failure pattern analysis
make failure-patterns DAYS=30

# Export structured JSON context for AI agents
make export-context              # codebase-wide health
make export-context PR=3346      # per-PR context
make export-context OUTPUT=ctx.json  # write to file
```

## Grafana dashboards

Dashboards are organized into three folders:

| Folder | Dashboards |
|--------|-----------|
| **Overview** | Engineering Metrics Summary (DORA + CI at a glance) |
| **Delivery Pipeline** | Throughput Over Time, PR Flow, Pipeline Velocity, Failure Analysis |
| **CI Intelligence** | CI Efficiency, Engineering Intelligence, CI Build Detail, AI Adoption |

## How each DORA metric is measured

### Deployment Frequency

> _How often does the team ship changes to production?_

| Signal | What it tells us | Source |
|--------|-----------------|--------|
| **Upstream releases** (`v*.*.*` tags) | How often the team cuts a formal release | `git for-each-ref refs/tags/` on bare clone |
| **PR merges to main** | How often changes enter the integration branch | `git log` on bare clone |
| **Downstream `rhoai-x.y` branches** | How often a release is staged for productization | `git for-each-ref` on downstream bare clone |

### Lead Time for Changes

> _How long from first commit to running in production?_

| Stage | Measurement | Source |
|-------|------------|--------|
| **PR cycle time** | Author date of first commit → committer date (merge time) | `git log --format=%aI,%cI` |
| **Merge → release tag** | Committer date of merge commit → creator date of earliest `v*` tag containing it | `git tag --contains` + `git for-each-ref` |

### Change Failure Rate

> _What percentage of changes cause a failure in production?_

| Failure signal | Why it indicates a failure | Source |
|---------------|--------------------------|--------|
| **Patch releases** | Hotfix release means the `.0` had a production bug | Tag patterns |
| **Reverts on main** | `Revert "..."` commit means a change was broken | `git log --grep` |
| **Cherry-picks to downstream** | Out-of-band fixes on frozen release branches | `git log --grep="cherry picked"` |

### Mean Time to Recovery (MTTR)

> _How quickly does the team recover from failures?_

| Recovery signal | Measurement | Source |
|----------------|------------|--------|
| **Patch release turnaround** | Time from `.0` to first `.1`/`.2` | Tag dates |

## Data sources

Almost everything comes from `git log` and `git for-each-ref` on local bare
clones plus VictoriaMetrics/VictoriaLogs queries for CI data.

| Data | Source |
|------|--------|
| Release tags + dates | `git for-each-ref refs/tags/` |
| PR merge data | `git log --format=...` on main |
| Revert/cherry-pick detection | `git log --grep` |
| CI build results, step failures | VictoriaMetrics (from openshift-ci-observability) |
| CI failure messages | VictoriaLogs |
| Code risk scores | `gocyclo` / hotspot analysis |

## Configuration

Edit `config.yaml` to adjust:
- Repository URLs and branch patterns
- Tag patterns for releases, EA builds, and patches
- Lookback period (default: 365 days)
- CI observability stack URLs (VictoriaMetrics, VictoriaLogs)
- Bot PR prefixes to filter (for cherry-pick detection)

## Data storage

All data is cached in `data/` (gitignored):
- `data/repos/*.git` — bare clones of both repos
- `data/eng-metrics.sqlite` — collected events and computed metrics

Run `make clean` to wipe and start fresh.
