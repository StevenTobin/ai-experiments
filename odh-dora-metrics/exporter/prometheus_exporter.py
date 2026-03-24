"""Expose DORA metrics as a Prometheus /metrics endpoint."""

from __future__ import annotations

import logging
import time

from prometheus_client import Gauge, start_http_server

from metrics.calculator import compute_all
from store.db import Store

log = logging.getLogger(__name__)

# --- Aggregate metrics (no labels) ---

DF_RELEASE_COUNT = Gauge("dora_deployment_frequency_releases_total", "Total upstream releases")
DF_RELEASE_GAP_DAYS = Gauge("dora_deployment_frequency_release_gap_days", "Avg days between releases")
DF_PR_COUNT = Gauge("dora_deployment_frequency_pr_merges_total", "Total merged PRs to main")
DF_PR_GAP_DAYS = Gauge("dora_deployment_frequency_pr_gap_days", "Avg days between PR merges")

LT_CYCLE_P50 = Gauge("dora_lead_time_pr_cycle_p50_hours", "PR cycle time p50 (hours)")
LT_CYCLE_P90 = Gauge("dora_lead_time_pr_cycle_p90_hours", "PR cycle time p90 (hours)")
LT_REVIEW_P50 = Gauge("dora_lead_time_pr_review_p50_hours", "PR review time p50 (hours)")
LT_REVIEW_P90 = Gauge("dora_lead_time_pr_review_p90_hours", "PR review time p90 (hours)")
LT_TO_RELEASE_P50 = Gauge("dora_lead_time_to_release_p50_hours", "Merge to release p50 (hours)")
LT_TO_RELEASE_P90 = Gauge("dora_lead_time_to_release_p90_hours", "Merge to release p90 (hours)")

CFR_RATE = Gauge("dora_change_failure_rate", "Change failure rate (0-1)")
CFR_PATCH_RELEASES = Gauge("dora_change_failure_patch_releases", "Number of patch releases")
CFR_REVERTS = Gauge("dora_change_failure_reverts", "Number of reverts on main")
CFR_CHERRY_PICKS = Gauge("dora_change_failure_cherry_picks", "Human cherry-picks to frozen branches")

MTTR_PATCH_P50 = Gauge("dora_mttr_patch_turnaround_p50_hours", "Patch release turnaround p50 (hours)")
MTTR_PATCH_P90 = Gauge("dora_mttr_patch_turnaround_p90_hours", "Patch release turnaround p90 (hours)")

# --- Per-release metrics (labeled by release) ---

REL_PR_COUNT = Gauge("dora_release_pr_count", "PRs in this release", ["release"])
REL_DAYS_SINCE_PREV = Gauge("dora_release_days_since_previous", "Days since previous release", ["release"])
REL_LEAD_P50 = Gauge("dora_release_lead_time_p50_hours", "Merge-to-release lead time p50", ["release"])
REL_LEAD_P90 = Gauge("dora_release_lead_time_p90_hours", "Merge-to-release lead time p90", ["release"])
REL_CYCLE_P50 = Gauge("dora_release_cycle_time_p50_hours", "PR cycle time p50", ["release"])
REL_CYCLE_P90 = Gauge("dora_release_cycle_time_p90_hours", "PR cycle time p90", ["release"])
REL_CHERRY_PICKS = Gauge("dora_release_cherry_picks", "Cherry-picks on downstream branch", ["release"])
REL_HAS_PATCH = Gauge("dora_release_has_patch", "Release needed a patch (1/0)", ["release"])
REL_PATCH_HOURS = Gauge("dora_release_patch_turnaround_hours", "Hours from .0 to first patch", ["release"])

_PER_RELEASE_GAUGES = [
    REL_PR_COUNT, REL_DAYS_SINCE_PREV,
    REL_LEAD_P50, REL_LEAD_P90,
    REL_CYCLE_P50, REL_CYCLE_P90,
    REL_CHERRY_PICKS, REL_HAS_PATCH, REL_PATCH_HOURS,
]

# --- Throughput over time (labeled by month) ---

MONTHLY_PRS = Gauge("dora_monthly_prs_merged", "PRs merged per month", ["month"])
MONTHLY_RELEASES = Gauge("dora_monthly_releases", "Releases per month", ["month", "type"])
MONTHLY_CHERRY_PICKS = Gauge("dora_monthly_cherry_picks", "Cherry-picks per month", ["month"])
MONTHLY_REVERTS = Gauge("dora_monthly_reverts", "Reverts per month", ["month"])

_MONTHLY_GAUGES = [MONTHLY_PRS, MONTHLY_RELEASES, MONTHLY_CHERRY_PICKS, MONTHLY_REVERTS]

# --- Failure analysis (labeled by branch / month) ---

FAIL_CP_BY_BRANCH = Gauge("dora_failure_cherry_picks_by_branch", "Cherry-picks per branch", ["branch"])
FAIL_CP_MONTHLY = Gauge("dora_failure_cherry_picks_monthly", "Cherry-picks per month", ["month"])
FAIL_REVERTS_MONTHLY = Gauge("dora_failure_reverts_monthly", "Reverts per month", ["month"])

_FAILURE_GAUGES = [FAIL_CP_BY_BRANCH, FAIL_CP_MONTHLY, FAIL_REVERTS_MONTHLY]

# --- PR flow (labeled by bucket) ---

PR_TTR_BUCKET = Gauge("dora_pr_time_to_release_bucket", "PRs per time-to-release bucket", ["bucket"])
PR_CYCLE_BUCKET = Gauge("dora_pr_cycle_time_bucket", "PRs per cycle time bucket", ["bucket"])

_FLOW_GAUGES = [PR_TTR_BUCKET, PR_CYCLE_BUCKET]

# --- Pipeline velocity (labeled by release) ---

PIPE_ACCUM = Gauge("dora_pipeline_accumulation_days", "Days from first PR merge to release tag", ["release"])
PIPE_DOWNSTREAM = Gauge("dora_pipeline_downstream_days", "Days from tag to downstream branch", ["release"])

_PIPELINE_GAUGES = [PIPE_ACCUM, PIPE_DOWNSTREAM]

# --- AI adoption ---
# Windowed aggregates use a simple "window" label for Grafana dropdown filtering.
# Monthly breakdowns use a "window" label so panels filter by the same dropdown.

WINDOWS = {
    "last_month": 1,
    "last_3_months": 3,
    "last_6_months": 6,
    "last_year": 12,
    "all_time": 9999,
}

AI_WIN_TOTAL = Gauge("dora_ai_window_total", "AI-assisted commits in window", ["window"])
AI_WIN_NON_AI = Gauge("dora_ai_window_non_ai", "Non-AI-labeled commits in window", ["window"])
AI_WIN_PCT = Gauge("dora_ai_window_pct", "AI-assisted % in window", ["window"])
AI_WIN_TOOL = Gauge("dora_ai_window_by_tool", "AI-assisted commits by tool in window", ["window", "tool"])
AI_MONTHLY = Gauge("dora_ai_monthly_commits", "AI-assisted commits per month", ["month", "window"])
AI_MONTHLY_PCT = Gauge("dora_ai_monthly_pct", "AI-assisted % of PRs per month", ["month", "window"])
AI_MONTHLY_TOOL = Gauge("dora_ai_monthly_by_tool", "AI-assisted commits per tool per month", ["month", "tool", "window"])

_AI_GAUGES = [AI_WIN_TOTAL, AI_WIN_NON_AI, AI_WIN_PCT, AI_WIN_TOOL, AI_MONTHLY, AI_MONTHLY_PCT, AI_MONTHLY_TOOL]


def _update_metrics(result: dict) -> None:
    df = result["deployment_frequency"]
    DF_RELEASE_COUNT.set(df["releases"]["total"])
    if df["releases"]["avg_gap_days"] is not None:
        DF_RELEASE_GAP_DAYS.set(df["releases"]["avg_gap_days"])
    DF_PR_COUNT.set(df["pr_merges"]["total"])
    if df["pr_merges"]["avg_gap_days"] is not None:
        DF_PR_GAP_DAYS.set(df["pr_merges"]["avg_gap_days"])

    lt = result["lead_time"]
    for key, p50_gauge, p90_gauge in [
        ("pr_cycle_time_hours", LT_CYCLE_P50, LT_CYCLE_P90),
        ("pr_review_time_hours", LT_REVIEW_P50, LT_REVIEW_P90),
        ("to_release_hours", LT_TO_RELEASE_P50, LT_TO_RELEASE_P90),
    ]:
        data = lt.get(key, {})
        if data.get("p50") is not None:
            p50_gauge.set(data["p50"])
        if data.get("p90") is not None:
            p90_gauge.set(data["p90"])

    cfr = result["change_failure_rate"]
    if cfr["rate"] is not None:
        CFR_RATE.set(cfr["rate"])
    CFR_PATCH_RELEASES.set(cfr["patch_releases"])
    CFR_REVERTS.set(cfr["reverts_on_main"])
    CFR_CHERRY_PICKS.set(cfr["human_cherry_picks"])

    mt = result["mttr"]
    ptr = mt["patch_release_turnaround_hours"]
    if ptr.get("p50") is not None:
        MTTR_PATCH_P50.set(ptr["p50"])
    if ptr.get("p90") is not None:
        MTTR_PATCH_P90.set(ptr["p90"])

    _update_per_release(result.get("per_release", []))
    _update_throughput(result.get("throughput", {}))
    _update_failure_analysis(result.get("failure_analysis", {}))
    _update_pr_flow(result.get("pr_flow", {}))
    _update_pipeline_velocity(result.get("pipeline_velocity", []))
    _update_ai_adoption(result.get("ai_adoption", {}))


def _update_per_release(releases: list[dict]) -> None:
    for gauge in _PER_RELEASE_GAUGES:
        gauge._metrics.clear()

    for rel in releases:
        label = rel["label"]
        REL_PR_COUNT.labels(release=label).set(rel["pr_count"])
        REL_CHERRY_PICKS.labels(release=label).set(rel["cherry_picks"])
        REL_HAS_PATCH.labels(release=label).set(1 if rel["has_patch"] else 0)
        if rel["days_since_previous"] is not None:
            REL_DAYS_SINCE_PREV.labels(release=label).set(rel["days_since_previous"])
        if rel["lead_time_p50"] is not None:
            REL_LEAD_P50.labels(release=label).set(rel["lead_time_p50"])
        if rel["lead_time_p90"] is not None:
            REL_LEAD_P90.labels(release=label).set(rel["lead_time_p90"])
        if rel["cycle_time_p50"] is not None:
            REL_CYCLE_P50.labels(release=label).set(rel["cycle_time_p50"])
        if rel["cycle_time_p90"] is not None:
            REL_CYCLE_P90.labels(release=label).set(rel["cycle_time_p90"])
        if rel["patch_turnaround_hours"] is not None:
            REL_PATCH_HOURS.labels(release=label).set(rel["patch_turnaround_hours"])


def _update_throughput(data: dict) -> None:
    for gauge in _MONTHLY_GAUGES:
        gauge._metrics.clear()

    for m in data.get("months", []):
        month = m["month"]
        MONTHLY_PRS.labels(month=month).set(m["prs_merged"])
        MONTHLY_RELEASES.labels(month=month, type="stable").set(m["releases_stable"])
        MONTHLY_RELEASES.labels(month=month, type="ea").set(m["releases_ea"])
        MONTHLY_RELEASES.labels(month=month, type="patch").set(m["releases_patch"])
        MONTHLY_CHERRY_PICKS.labels(month=month).set(m["cherry_picks"])
        MONTHLY_REVERTS.labels(month=month).set(m["reverts"])


def _update_failure_analysis(data: dict) -> None:
    for gauge in _FAILURE_GAUGES:
        gauge._metrics.clear()

    for entry in data.get("cherry_picks_by_branch", []):
        FAIL_CP_BY_BRANCH.labels(branch=entry["branch"]).set(entry["count"])

    for entry in data.get("monthly_failures", []):
        month = entry["month"]
        FAIL_CP_MONTHLY.labels(month=month).set(entry["cherry_picks"])
        FAIL_REVERTS_MONTHLY.labels(month=month).set(entry["reverts"])


def _update_pr_flow(data: dict) -> None:
    for gauge in _FLOW_GAUGES:
        gauge._metrics.clear()

    for entry in data.get("time_to_release", []):
        PR_TTR_BUCKET.labels(bucket=entry["bucket"]).set(entry["count"])

    for entry in data.get("cycle_time", []):
        PR_CYCLE_BUCKET.labels(bucket=entry["bucket"]).set(entry["count"])


def _update_pipeline_velocity(releases: list[dict]) -> None:
    for gauge in _PIPELINE_GAUGES:
        gauge._metrics.clear()

    for rel in releases:
        label = rel["label"]
        if rel["accumulation_days"] is not None:
            PIPE_ACCUM.labels(release=label).set(rel["accumulation_days"])
        if rel["downstream_days"] is not None:
            PIPE_DOWNSTREAM.labels(release=label).set(rel["downstream_days"])


def _month_age(month_str: str) -> int:
    """Return how many months ago this month is (0=current)."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    current = now.year * 12 + now.month
    parts = month_str.split("-")
    data = int(parts[0]) * 12 + int(parts[1])
    return max(0, current - data)


def _update_ai_adoption(data: dict) -> None:
    for gauge in _AI_GAUGES:
        gauge._metrics.clear()

    months = data.get("months", [])

    for window_name, max_age in WINDOWS.items():
        filtered = [m for m in months if _month_age(m["month"]) < max_age]

        ai_total = sum(m["ai_commits"] for m in filtered)
        pr_total = sum(m["total_prs"] for m in filtered)
        pct = round(ai_total / pr_total * 100, 1) if pr_total > 0 else 0

        AI_WIN_TOTAL.labels(window=window_name).set(ai_total)
        AI_WIN_NON_AI.labels(window=window_name).set(max(0, pr_total - ai_total))
        AI_WIN_PCT.labels(window=window_name).set(pct)

        tool_totals: dict[str, int] = {}
        for m in filtered:
            for tool, count in m.get("by_tool", {}).items():
                tool_totals[tool] = tool_totals.get(tool, 0) + count
        for tool, count in tool_totals.items():
            AI_WIN_TOOL.labels(window=window_name, tool=tool).set(count)

        for m in filtered:
            month = m["month"]
            AI_MONTHLY.labels(month=month, window=window_name).set(m["ai_commits"])
            AI_MONTHLY_PCT.labels(month=month, window=window_name).set(m["ai_pct"])
            for tool, count in m.get("by_tool", {}).items():
                AI_MONTHLY_TOOL.labels(month=month, tool=tool, window=window_name).set(count)


def start_server(cfg: dict, port: int = 9090, refresh_interval: int = 3600) -> None:
    """Start the Prometheus metrics server, refreshing metrics periodically."""
    log.info("Starting Prometheus exporter on :%d", port)
    start_http_server(port)

    while True:
        try:
            store = Store(cfg["collection"]["cache_db"])
            lookback = cfg["collection"].get("lookback_days", 365)
            min_ver = cfg.get("per_release", {}).get("min_version", "3.0.0")
            result = compute_all(store, lookback_days=lookback, min_version=min_ver)
            _update_metrics(result)
            store.close()
            log.info("Metrics updated successfully")
        except Exception:
            log.exception("Failed to update metrics")
        time.sleep(refresh_interval)
