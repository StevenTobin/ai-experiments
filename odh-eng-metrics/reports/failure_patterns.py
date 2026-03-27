"""Recurring failure pattern analyzer.

Clusters similar error messages, computes flake rates per step and component,
identifies persistent vs transient failures, and surfaces root-cause signals
that an AI agent or human operator can act on.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from metrics import ci_efficiency
from store.db import Store

log = logging.getLogger(__name__)


def _parse_json_field(value: str | None) -> list:
    if not value:
        return []
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []


_NOISE_PATTERNS = [
    re.compile(r"\b(0x[0-9a-f]+|[0-9a-f]{8,})\b", re.IGNORECASE),
    re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[^\s]*"),
    re.compile(r"\b\d+\.\d+\.\d+\.\d+(:\d+)?\b"),
    re.compile(r"\b[a-f0-9]{12,}\b"),
    re.compile(r"namespace/[\w-]+"),
    re.compile(r"pod/[\w-]+"),
]


def _normalize_message(msg: str) -> str:
    """Collapse variable parts of error messages for clustering."""
    normalized = msg
    for pat in _NOISE_PATTERNS:
        normalized = pat.sub("*", normalized)
    normalized = re.sub(r"\*(\*)+", "*", normalized)
    return normalized.strip()[:200]


def _compute_flake_rate(builds: list[dict]) -> dict:
    """Compute flake rate: PRs that had both pass and fail cycles.

    High flake rate = failures are non-deterministic (infra or timing).
    Low flake rate = failures are consistent (real code bugs).
    """
    pr_builds: dict[int, list[dict]] = defaultdict(list)
    for b in builds:
        pr_builds[b["pr_number"]].append(b)

    total_prs = len(pr_builds)
    flaky_prs = 0
    consistent_fail_prs = 0
    clean_prs = 0

    for pr_num, blist in pr_builds.items():
        blist.sort(key=lambda x: x["build_id"])
        cycles = ci_efficiency._derive_cycles(blist)
        results = {c["result"] for c in cycles}
        if "success" in results and "failure" in results:
            flaky_prs += 1
        elif results == {"failure"}:
            consistent_fail_prs += 1
        else:
            clean_prs += 1

    return {
        "total_prs": total_prs,
        "flaky_prs": flaky_prs,
        "consistent_fail_prs": consistent_fail_prs,
        "clean_prs": clean_prs,
        "flake_rate": round(flaky_prs / total_prs, 3) if total_prs else None,
    }


def generate(store: Store, lookback_days: int = 30) -> str:
    """Generate a recurring failure pattern report."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    cutoff_str = cutoff.strftime("%Y-%m-%d")

    all_prs = store.get_merged_prs(base_branch="main")
    period_prs = [p for p in all_prs if (p.get("merged_at") or "") >= cutoff_str]
    period_pr_nums = {p["number"] for p in period_prs}

    all_builds = store.get_ci_builds()
    period_builds = [b for b in all_builds if b["pr_number"] in period_pr_nums]
    failed_builds = [b for b in period_builds if b["result"] == "failure"]
    failed_build_ids = {b["build_id"] for b in failed_builds}

    all_steps = store.get_all_build_steps()
    all_fail_msgs = store.get_all_build_failure_messages()

    pr_components: dict[int, list[str]] = {}
    for p in period_prs:
        comps = _parse_json_field(p.get("changed_components"))
        if comps:
            pr_components[p["number"]] = comps

    lines: list[str] = []
    _w = lines.append

    _w(f"# Recurring Failure Pattern Analysis")
    _w(f"**Last {lookback_days} days** (since {cutoff_str})")
    _w("")

    # --- Overall Flake Assessment ---
    flake = _compute_flake_rate(period_builds)
    _w("## Flake Assessment")
    _w("")
    _w(f"- **Total PRs with CI:** {flake['total_prs']}")
    _w(f"- **Clean (all pass):** {flake['clean_prs']}")
    _w(f"- **Flaky (mixed pass/fail):** {flake['flaky_prs']}")
    _w(f"- **Consistently failing:** {flake['consistent_fail_prs']}")
    if flake["flake_rate"] is not None:
        pct = flake["flake_rate"] * 100
        _w(f"- **Flake rate:** {pct:.0f}% of PRs experience non-deterministic failures")
        if pct > 40:
            _w("")
            _w("**High flake rate.** Most failures are transient — focus on infrastructure "
               "stability and test isolation rather than code fixes.")
        elif pct > 20:
            _w("")
            _w("**Moderate flake rate.** A mix of real failures and flakes. "
               "The error clusters below help distinguish them.")
    _w("")

    # --- Error Message Clusters ---
    period_msgs = [m for m in all_fail_msgs if m["build_id"] in failed_build_ids]
    if period_msgs:
        cluster_counts: Counter[str] = Counter()
        cluster_builds: dict[str, set[str]] = defaultdict(set)
        cluster_raw: dict[str, str] = {}

        for m in period_msgs:
            norm = _normalize_message(m["message"])
            cluster_counts[norm] += m.get("count", 1)
            cluster_builds[norm].add(m["build_id"])
            if norm not in cluster_raw:
                cluster_raw[norm] = m["message"][:200]

        _w("## Error Clusters")
        _w("")
        _w("Errors grouped by normalized pattern (timestamps, hashes, IPs collapsed):")
        _w("")

        for i, (pattern, count) in enumerate(cluster_counts.most_common(15), 1):
            n_builds = len(cluster_builds[pattern])
            raw = cluster_raw[pattern]
            _w(f"### Cluster {i}: {n_builds} builds, {count} occurrences")
            _w("")
            _w(f"```")
            _w(f"{raw}")
            _w(f"```")
            _w("")

            cluster_bids = cluster_builds[pattern]
            cluster_pr_nums = {b["pr_number"] for b in failed_builds
                              if b["build_id"] in cluster_bids}
            comps_hit = set()
            for pr_num in cluster_pr_nums:
                for comp in pr_components.get(pr_num, []):
                    comps_hit.add(comp)
            if comps_hit:
                _w(f"- **Components:** {', '.join(sorted(comps_hit))}")

            cluster_b_list = [b for b in period_builds if b["pr_number"] in cluster_pr_nums]
            if cluster_b_list:
                flake_info = _compute_flake_rate(cluster_b_list)
                if flake_info["flake_rate"] is not None:
                    label = "flaky" if flake_info["flake_rate"] > 0.5 else "consistent"
                    _w(f"- **Pattern:** {label} "
                       f"(flake rate: {flake_info['flake_rate']*100:.0f}%)")

            is_infra = any(s.get("is_infra") and s.get("level") == "Error"
                          for s in all_steps if s["build_id"] in cluster_bids)
            if is_infra:
                _w(f"- **Root cause signal:** Infrastructure (provisioning/scheduling)")
            _w("")

    # --- Step Failure Hotspots ---
    period_step_failures = [s for s in all_steps
                           if s["build_id"] in failed_build_ids and s.get("level") == "Error"]
    if period_step_failures:
        step_counter: Counter[str] = Counter()
        step_infra: dict[str, bool] = {}
        for s in period_step_failures:
            step_counter[s["step_name"]] += 1
            step_infra[s["step_name"]] = bool(s.get("is_infra"))

        _w("## Step Failure Hotspots")
        _w("")
        _w("| Step | Failures | Type | Share |")
        _w("|------|----------|------|-------|")
        total_step_fails = sum(step_counter.values())
        for step, cnt in step_counter.most_common(10):
            stype = "infra" if step_infra.get(step) else "code"
            share = cnt / total_step_fails * 100
            _w(f"| {step} | {cnt} | {stype} | {share:.0f}% |")
        _w("")

    # --- Per-Component Failure Fingerprint ---
    if pr_components:
        comp_error_patterns: dict[str, Counter[str]] = defaultdict(Counter)
        for m in period_msgs:
            bid = m["build_id"]
            pr_num = next((b["pr_number"] for b in failed_builds if b["build_id"] == bid), None)
            if pr_num is None:
                continue
            for comp in pr_components.get(pr_num, []):
                norm = _normalize_message(m["message"])
                comp_error_patterns[comp][norm] += m.get("count", 1)

        if comp_error_patterns:
            _w("## Component Failure Fingerprints")
            _w("")
            _w("Top recurring errors per component:")
            _w("")

            for comp in sorted(comp_error_patterns):
                patterns = comp_error_patterns[comp]
                _w(f"### {comp}")
                _w("")
                for pattern, cnt in patterns.most_common(3):
                    raw = cluster_raw.get(pattern, pattern)
                    _w(f"- `{raw[:100]}` (x{cnt})")
                _w("")

    # --- Recommendations ---
    _w("## Recommendations for AI Agent")
    _w("")
    _w("When fixing failures in this codebase, prioritize:")
    _w("")

    if period_step_failures:
        top_step = step_counter.most_common(1)[0]
        if step_infra.get(top_step[0]):
            _w(f"1. **Skip retesting for `{top_step[0]}` failures** — this is an infrastructure "
               f"step that failed {top_step[1]} times. Retesting is the correct action, not code changes.")
        else:
            _w(f"1. **Fix `{top_step[0]}`** — this test step failed {top_step[1]} times. "
               f"Search for the error patterns above in the test code.")

    if period_msgs:
        top_cluster = cluster_counts.most_common(1)[0]
        _w(f"2. **Address the most common error** ({cluster_counts.most_common(1)[0][1]} occurrences). "
           f"This error appeared in {len(cluster_builds[top_cluster[0]])} builds.")

    if flake["flake_rate"] and flake["flake_rate"] > 0.3:
        _w(f"3. **Flakiness is significant** ({flake['flake_rate']*100:.0f}%). "
           f"For flaky errors, add retry logic or improve test isolation "
           f"rather than changing test assertions.")

    _w(f"4. **Check infrastructure first** for any `ipi-install`, `baremetalds`, "
       f"or `openshift-cluster-bot` step failures — these are not code bugs.")

    _w("")
    _w("---")
    _w(f"*Analyzed {len(period_builds)} builds across {len(period_prs)} PRs, "
       f"{len(period_msgs)} failure messages, {len(period_step_failures)} step errors.*")
    _w("")

    return "\n".join(lines)
