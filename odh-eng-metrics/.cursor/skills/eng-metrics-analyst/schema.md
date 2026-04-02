# Database Schema Reference

SQLite database at `data/eng-metrics.sqlite`. All dates are ISO-8601 strings. JSON arrays are stored as TEXT (use `json_each()` to unnest).

## Core Tables

### releases
Upstream git tags (stable, EA, patch).

| Column | Type | Notes |
|--------|------|-------|
| tag | TEXT PK | e.g. `v3.5.0`, `v3.5.1-ea.1` |
| published | TEXT | ISO date |
| prerelease | INT | 1 = prerelease (EA) |
| is_patch | INT | 1 = patch release (3rd digit > 0) |
| is_ea | INT | 1 = early-access |

### merged_prs
PRs merged to upstream branches. One row per PR.

| Column | Type | Notes |
|--------|------|-------|
| repo | TEXT | `owner/repo` |
| number | INT | PR number |
| title | TEXT | |
| author | TEXT | GitHub username |
| created_at | TEXT | PR opened |
| merged_at | TEXT | Merge timestamp |
| first_commit_at | TEXT | Earliest commit in PR (for cycle time) |
| base_branch | TEXT | Target branch |
| additions | INT | Lines added |
| deletions | INT | Lines removed |
| jira_keys | TEXT | JSON array of JIRA keys, e.g. `["RHOAIENG-123"]` |
| merge_sha | TEXT | Merge commit SHA |
| is_ai_assisted | INT | 1 = AI commit markers detected |
| changed_files | TEXT | JSON array of file paths |
| changed_components | TEXT | JSON array of component names |

**PK**: (repo, number)

### reverts
Revert commits detected on main.

| Column | Type | Notes |
|--------|------|-------|
| repo | TEXT | |
| sha | TEXT PK | Revert commit SHA |
| date | TEXT | |
| reverted_sha | TEXT | Original commit that was reverted |
| message | TEXT | Commit message |
| reverted_pr | INT | PR number of the reverted change (nullable) |

### cherry_picks
Cherry-pick PRs on downstream release branches.

| Column | Type | Notes |
|--------|------|-------|
| repo | TEXT | |
| pr_number | INT | |
| target_branch | TEXT | e.g. `rhoai-2.16` |
| title | TEXT | |
| author | TEXT | |
| merged_at | TEXT | |

**PK**: (repo, pr_number)

### downstream_branches
Downstream release branches (e.g. `rhoai-2.15`, `rhoai-2.16-ea.1`).

| Column | Type | Notes |
|--------|------|-------|
| name | TEXT PK | Branch name |
| first_commit_date | TEXT | When first commit landed |
| is_ea | INT | 1 = EA branch |

### branch_arrivals
Tracks when an upstream PR's changes arrive on a downstream branch.

| Column | Type | Notes |
|--------|------|-------|
| pr_repo | TEXT | |
| pr_number | INT | |
| branch | TEXT | Target branch |
| arrived_at | TEXT | Timestamp |

**PK**: (pr_repo, pr_number, branch)

## CI Tables

### ci_builds
One row per CI build (Prow job run).

| Column | Type | Notes |
|--------|------|-------|
| build_id | TEXT PK | Prow build ID |
| pr_number | INT | |
| job_name | TEXT | e.g. `pull-ci-opendatahub-io-opendatahub-operator-main-unit` |
| duration_seconds | REAL | Total wall clock |
| result | TEXT | `success`, `failure`, `aborted` |
| started_at | TEXT | |
| peak_cpu_cores | REAL | Max CPU usage |
| peak_memory_bytes | REAL | Max memory |
| total_step_seconds | REAL | Sum of all step durations |

### ci_build_steps
Per-step breakdown within a build.

| Column | Type | Notes |
|--------|------|-------|
| build_id | TEXT | FK to ci_builds |
| step_name | TEXT | e.g. `e2e-odh`, `unit` |
| duration_seconds | REAL | |
| level | TEXT | Severity: `Error`, `Warning`, `Info` |
| is_infra | INT | 1 = infrastructure step (not test code) |

**PK**: (build_id, step_name)

### ci_build_failure_messages
Failure messages extracted from CI logs.

| Column | Type | Notes |
|--------|------|-------|
| build_id | TEXT | FK to ci_builds |
| message | TEXT | Truncated to 500 chars |
| source | TEXT | Where extracted from |
| count | INT | Occurrences in build |

**PK**: (build_id, message)

### ci_test_results
JUnit test results from CI.

| Column | Type | Notes |
|--------|------|-------|
| build_id | TEXT | FK to ci_builds |
| test_name | TEXT | Go test path |
| suite | TEXT | Test suite name |
| test_variant | TEXT | e2e variant |
| status | TEXT | `passed`, `failed`, `skipped` |
| duration_seconds | REAL | |
| is_leaf | INT | 1 = leaf test (no children) |
| failure_message | TEXT | |

**PK**: (build_id, test_name, test_variant)

## JIRA Tables

### jira_issues
Full issue metadata fetched from JIRA REST API.

| Column | Type | Notes |
|--------|------|-------|
| key | TEXT PK | e.g. `RHOAIENG-12345` |
| summary | TEXT | Issue title |
| issue_type | TEXT | `Bug`, `Story`, `Task`, etc. |
| priority | TEXT | `Blocker`, `Critical`, `Major`, `Normal`, `Minor` |
| status | TEXT | e.g. `New`, `In Progress`, `Closed` |
| status_category | TEXT | `To Do`, `In Progress`, `Done` |
| assignee | TEXT | Display name |
| components | TEXT | JSON array: `["Dashboard", "KServe"]` |
| labels | TEXT | JSON array: `["ai-triaged", "sprint-42"]` |
| fix_versions | TEXT | JSON array |
| story_points | REAL | |
| created | TEXT | |
| resolved | TEXT | Resolution date (null if open) |
| epic_key | TEXT | Parent epic |
| description | TEXT | Full description text |
| comments | TEXT | JSON array of `{"author", "body", "created"}` |
| fetched_at | TEXT | When we last pulled from JIRA |

**Querying JSON arrays**: Use `json_each()` to unnest. Example:
```sql
SELECT ji.key, je.value AS label
FROM jira_issues ji, json_each(ji.labels) je
WHERE je.value LIKE 'ai-%'
```

### jira_collection_issues
Many-to-many: which issues belong to which named collections.

| Column | Type | Notes |
|--------|------|-------|
| collection_name | TEXT | e.g. `ai-bug-bash` |
| issue_key | TEXT | FK to jira_issues.key |

**PK**: (collection_name, issue_key)

### metrics_cache
Pre-computed metric values (key-value store).

| Column | Type | Notes |
|--------|------|-------|
| metric | TEXT | Metric name |
| window | TEXT | Time window or qualifier |
| value | TEXT | JSON-encoded value |
| computed_at | TEXT | |

**PK**: (metric, window)

## Analysis Tables

### ai_assisted_commits
Commits with AI tool markers (Co-authored-by, tool tags).

| Column | Type | Notes |
|--------|------|-------|
| repo | TEXT | |
| sha | TEXT | Commit SHA |
| date | TEXT | |
| message | TEXT | First 200 chars |
| tool | TEXT | `copilot`, `cursor`, `aider`, etc. |

**PK**: (repo, sha, tool)

### code_risk_scores
Per-function code risk from static analysis (cyclomatic complexity + churn).

| Column | Type | Notes |
|--------|------|-------|
| repo | TEXT | |
| file | TEXT | File path |
| function | TEXT | Function name |
| component | TEXT | Mapped component |
| complexity | REAL | Cyclomatic complexity |
| churn_30d | INT | Commits touching this function in 30 days |
| risk_score | REAL | Combined risk (0-10 scale) |
| risk_band | TEXT | `Low`, `Medium`, `High`, `Critical` |
| analyzed_at | TEXT | |

**PK**: (repo, file, function)

### agentready_assessments
AI bug automation readiness scores per repository.

| Column | Type | Notes |
|--------|------|-------|
| repo_url | TEXT | GitHub URL |
| project | TEXT | JIRA project key |
| overall_score | REAL | 0-100 |
| certification_level | TEXT | e.g. `Gold`, `Silver`, `Bronze`, `Not Ready` |
| attributes_assessed | INT | |
| attributes_total | INT | |
| findings_json | TEXT | JSON blob with detailed findings |
| assessed_at | TEXT | |

**PK**: (repo_url, project)
