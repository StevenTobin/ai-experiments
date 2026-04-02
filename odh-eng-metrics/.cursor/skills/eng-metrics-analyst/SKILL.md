---
name: eng-metrics-analyst
description: >-
  Analyze engineering metrics from the odh-eng-metrics SQLite database covering
  DORA metrics, CI efficiency, PR flow, code risk, JIRA analytics, agent
  readiness, and AI adoption. Use when asked about engineering health, release
  cadence, CI pass rates, PR throughput, code hotspots, JIRA issue trends, or
  any quantitative question about the opendatahub-operator development process.
---

# Engineering Metrics Analyst

Analyze the opendatahub-operator engineering data stored in a local SQLite database. The database is populated by collectors that pull from Git history, GitHub APIs, JIRA, and CI Observability (VictoriaMetrics). You query it directly with SQL.

## Principles

- **Compute on demand**: derive metrics from raw data via SQL rather than relying on pre-computed values in `metrics_cache`. The cache may be stale.
- **Evidence-based**: every claim backed by a query result. Show the SQL.
- **Cross-reference**: the power of this dataset is joining across sources -- JIRA issues to PRs to CI builds to code risk. Use it.
- **Interpret, don't just count**: raw numbers need context. Compare to baselines, compute rates, identify trends.

## Data Sources

The database at `data/eng-metrics.sqlite` contains data from:

| Source | Tables | What |
|--------|--------|------|
| Git (upstream) | `merged_prs`, `reverts`, `releases` | PR metadata, revert detection, release tags |
| Git (downstream) | `cherry_picks`, `downstream_branches`, `branch_arrivals` | Hotfix tracking, propagation timing |
| GitHub API | `ai_assisted_commits` | AI tool adoption in commits |
| JIRA REST API | `jira_issues`, `jira_collection_issues` | Issue lifecycle, labels, components |
| CI Observability | `ci_builds`, `ci_build_steps`, `ci_test_results`, `ci_build_failure_messages` | Build results, step timing, test outcomes |
| Static analysis | `code_risk_scores` | Function-level complexity and churn |
| AgentReady tool | `agentready_assessments` | Per-repo AI automation readiness |

## Query Tool

All queries go through `.cursor/skills/eng-metrics-analyst/eng-query`. Run from the `odh-eng-metrics` directory.

```bash
python .cursor/skills/eng-metrics-analyst/eng-query status          # Health check
python .cursor/skills/eng-metrics-analyst/eng-query schema          # All table DDL
python .cursor/skills/eng-metrics-analyst/eng-query collections     # JIRA collections
python .cursor/skills/eng-metrics-analyst/eng-query sql "SELECT …"  # Ad-hoc query (JSON lines)
```

The `sql` command is your primary tool. It enforces read-only mode. Output is one JSON object per row.

**Always run `status` first** to confirm the database exists and has data.

## References

- Full table schemas and column semantics: [schema.md](schema.md)
- Metric definitions, DORA classification, and example SQL: [classification.md](classification.md)

Read these before writing queries. They document JSON column formats, primary keys, and cross-reference join patterns.

## Report Generation Skills

For structured CI health reports rendered as interactive HTML canvases, follow one of these sub-workflows:

- **Weekly CI Digest** — [ci-digest.md](ci-digest.md): week-over-week KPIs, new breakages, resolutions, component health, and a narrative "state of CI" summary. Use when asked for a weekly digest, CI summary, or quick CI status check.
- **CI Deep Analysis** — [ci-deep-analysis.md](ci-deep-analysis.md): multi-period KPI comparison (7d/30d/90d), trend charts, error clustering, manifest regression detection, code risk correlation, and prioritized recommendations. Use when asked for a deep CI analysis, trend report, or comprehensive CI health assessment.

Both skills produce dual output: an HTML canvas for interactive follow-up in Cursor, and a shareable HTML file saved to `reports/` with a timestamped filename. Read the relevant skill file and follow its query workflow and output structure.

## Investigation Workflows

### "How healthy is engineering?"

1. **Release cadence**: count stable releases, compute average gap
2. **PR throughput**: monthly merge counts, trend direction
3. **CI efficiency**: first-pass success rate, retest tax
4. **Code risk**: critical/high hotspot count, worst components
5. **Open JIRA aging**: p50/p90 age of open issues

### "Is CI getting better or worse?"

1. Monthly breakdown: `GROUP BY strftime('%Y-%m', started_at)`
2. Compare first-pass success rate across months
3. Identify top failing steps: `GROUP BY step_name WHERE level = 'Error'`
4. Check if flaky tests dominate: same test failing intermittently across PRs
5. Infrastructure vs code: `is_infra` flag on `ci_build_steps`

### "Which components need attention?"

1. Code risk: highest `avg_risk_score` by component
2. JIRA: most open bugs by component (unnest `components` JSON)
3. CI: which components' tests fail most (join `ci_test_results` to test name patterns)
4. Agent readiness: lowest `overall_score` in `agentready_assessments`
5. Cross-reference: components that are high-risk AND have many open bugs

### "How is AI adoption trending?"

1. Monthly AI-assisted commits from `ai_assisted_commits`
2. `is_ai_assisted` flag on `merged_prs` for PR-level view
3. Tool breakdown: which AI tools are being used
4. Quality signal: do AI-assisted PRs have different CI pass rates?

### Ad-hoc questions

For any question not covered above:

1. Identify which tables contain the relevant data (check schema.md)
2. Write a SQL query — the model is very good at this
3. Run via `eng-query sql "..."`
4. Interpret results in context

## Key SQL Patterns

### Unnesting JSON arrays
Many columns store JSON arrays as TEXT. Use SQLite's `json_each()`:
```sql
SELECT ji.key, je.value AS label
FROM jira_issues ji, json_each(ji.labels) je
WHERE je.value LIKE 'ai-%'
```

### Time bucketing
```sql
SELECT strftime('%Y-%m', merged_at) AS month, COUNT(*) AS prs
FROM merged_prs
WHERE base_branch = 'main'
GROUP BY month ORDER BY month
```

### First-per-group (e.g. first CI build per PR)
```sql
SELECT * FROM ci_builds
WHERE rowid IN (
  SELECT rowid FROM ci_builds b2
  WHERE b2.pr_number = ci_builds.pr_number
  ORDER BY b2.started_at LIMIT 1
)
```

### Cross-source joins
```sql
-- JIRA issues → linked PRs → CI builds
SELECT ji.key, ji.summary, mp.number, cb.result
FROM jira_issues ji
JOIN merged_prs mp ON mp.jira_keys LIKE '%' || ji.key || '%'
JOIN ci_builds cb ON cb.pr_number = mp.number
```

## Tips

- **JSON columns**: `labels`, `components`, `fix_versions`, `jira_keys`, `changed_files`, `changed_components` are all JSON TEXT. Use `json_each()` or `LIKE '%value%'` for quick filtering.
- **Date math**: SQLite uses `julianday()` for date arithmetic. Hours = `(julianday(a) - julianday(b)) * 24`.
- **Large result sets**: add `LIMIT 20` during exploration, remove for final analysis.
- **NULL handling**: `resolved IS NULL` means the issue is still open. Many columns are nullable.
- **Collection scoping**: to analyze a specific JIRA collection, join through `jira_collection_issues`.
