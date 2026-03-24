# DORA Metrics for opendatahub-operator

Compute and visualize [DORA metrics](https://dora.dev/guides/dora-metrics-four-keys/) for the
[opendatahub-operator](https://github.com/opendatahub-io/opendatahub-operator) project, tracking
the full delivery pipeline from PR merge through downstream release.

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

## How each DORA metric is measured

### Deployment Frequency

> _How often does the team ship changes to production?_

Since production deployments happen outside the repo, we measure three
complementary proxies:

| Signal | What it tells us | Source |
|--------|-----------------|--------|
| **Upstream releases** (`v*.*.*` tags) | How often the team cuts a formal release | `git for-each-ref refs/tags/` on bare clone |
| **PR merges to main** | How often changes enter the integration branch | `git log` on bare clone |
| **Downstream `rhoai-x.y` branches** | How often a release is staged for productization | `git for-each-ref` on downstream bare clone |

The upstream release cadence is classified against DORA bands (Elite: daily,
High: weekly, Medium: monthly, Low: less than monthly).

### Lead Time for Changes

> _How long from first commit to running in production?_

We break lead time into stages, all measured from the local bare clones:

| Stage | Measurement | Source |
|-------|------------|--------|
| **PR cycle time** | Author date of first commit on branch → committer date (merge time) | `git log --format=%aI,%cI` — for squash merges, GitHub preserves the author date from the first commit |
| **PR review time** | Same as cycle time for git-only measurement (PR open date not available without API) | Same as above |
| **Merge → release tag** | Committer date of merge commit → creator date of the earliest `v*` tag containing it | `git tag --contains <sha>` + `git for-each-ref` |

For squash merges (the majority in this repo), GitHub preserves the original
author date as the date of the first commit on the topic branch, while the
committer date is set to the merge time.  This gives us cycle time without any
API calls.

Stable/rhoai branch arrival times are not reported because those branches are
fast-forwarded from main (the commit's own date is the same), and bare clones
lack reflogs to detect when a branch tip moved.

### Change Failure Rate

> _What percentage of changes cause a failure in production?_

We count distinct failure signals and divide by total changes (PRs merged to
main) per the DORA definition:

| Failure signal | Why it indicates a failure | Source |
|---------------|--------------------------|--------|
| **Patch releases** (`v*.*.1`, `v*.*.2`, ...) | A hotfix release means the previous `.0` had a production-impacting bug | `git for-each-ref refs/tags/` filtered by `v\d+\.\d+\.[1-9]\d*$` |
| **Reverts on main** | A `Revert "..."` commit means a merged change was broken enough to roll back | `git log --grep` for `^Revert "` on upstream main |
| **Cherry-picks to downstream release branches** | Commits with `(cherry picked from commit ...)` on frozen `rhoai-x.y` branches indicate production blockers requiring out-of-band fixes | `git log --grep="cherry picked from commit"` on downstream bare clone |

Cherry-picks are counted as distinct branches affected (not individual commits),
since multiple cherry-pick commits to the same branch typically represent a
single incident.

### Mean Time to Recovery (MTTR)

> _How quickly does the team recover from failures?_

| Recovery signal | Measurement | Source |
|----------------|------------|--------|
| **Patch release turnaround** | Time from the base `.0` release to the patch `.1`/`.2` release | Tag dates from `git for-each-ref` |

Revert-to-fix turnaround and cherry-pick resolution times are tracked in the
database but require additional context (e.g., Jira integration) to fully
automate.  They are reported as "pending analysis" in the output.

## Data sources

Almost everything comes from `git log` and `git for-each-ref` on local bare
clones.  The only network call is a single optional GitHub API request to fetch
the `prerelease` flag from GitHub Releases.  If that call fails (rate-limited,
no token), the tool falls back to inferring prerelease status from the EA tag
pattern.

| Data | Source | API calls |
|------|--------|-----------|
| Release tags + dates | `git for-each-ref refs/tags/` | 0 |
| PR merge data | `git log --format=...` on main | 0 |
| Revert detection | `git log --grep="^Revert"` | 0 |
| Cherry-pick detection | `git log --grep="cherry picked from commit"` | 0 |
| Commit propagation to tags | `git tag --contains` | 0 |
| Downstream branch enumeration | `git for-each-ref refs/remotes/origin/` | 0 |
| Prerelease flag (optional) | GitHub Releases API | 1 paginated call (~2 requests) |

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

# Launch Grafana dashboard (requires Docker)
make dashboard
# → http://localhost:3000 (admin/admin)

# Stop the dashboard
make dashboard-down
```

## How it works

1. **`make collect`** clones both repos as bare clones into `data/repos/`, then:
   - Reads all upstream `v*` tags and their dates from the bare clone
   - Optionally fetches prerelease flags from GitHub Releases API (1 call, gracefully degrades)
   - Enumerates downstream `rhoai-x.y` branches as delivery events
   - Parses `git log` on upstream main to extract PR merge data (number, author, dates)
   - Scans commit messages for `Revert "..."` patterns
   - Scans downstream release branches for `(cherry picked from commit ...)` markers
   - Traces commit propagation from main to release tags via `git tag --contains`

2. **`make report`** computes all four DORA metrics from the cached SQLite data.

3. **`make dashboard`** starts a Prometheus exporter + Grafana stack via Docker Compose
   with a pre-built dashboard.

## Configuration

Edit `config.yaml` to adjust:
- Repository URLs and branch patterns
- Tag patterns for releases, EA builds, and patches
- Lookback period (default: 365 days)
- Bot PR prefixes to filter (for cherry-pick detection)
- Jira integration (disabled by default)

## Data storage

All data is cached in `data/` (gitignored):
- `data/repos/*.git` -- bare clones of both repos (~30s to clone, ~1s to fetch)
- `data/dora.sqlite` -- collected events and computed metrics

Run `make clean` to wipe and start fresh.

## Limitations

- **PR cycle time for squash merges** depends on GitHub preserving the author
  date.  If a contributor amends dates before merging, the estimate will be off.
- **Stable/rhoai branch arrival** cannot be measured from bare clones (no reflogs).
  Tag arrival is used as the delivery proxy instead.
- **Cherry-pick detection** relies on `git cherry-pick -x` (which adds the
  `(cherry picked from commit ...)` trailer).  Manual cherry-picks without `-x`
  are missed.
- **MTTR** currently only covers patch release turnaround.  Revert-to-fix and
  cherry-pick resolution require Jira integration for full automation.
