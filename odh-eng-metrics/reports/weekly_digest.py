"""Weekly CI health digest.

Generates a concise summary of the past week's CI activity, highlighting
regressions, persistent failures, infrastructure trends, and component risk
for quick team review or AI agent processing.
"""

from __future__ import annotations

import json
import logging
import statistics
from collections import defaultdict
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


def _fmt_pct(value: float | None) -> str:
    return f"{value * 100:.0f}%" if value is not None else "N/A"


def generate(store: Store, weeks_back: int = 1) -> str:
    """Generate a weekly CI health digest covering the last N weeks."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(weeks=weeks_back)
    cutoff_str = cutoff.strftime("%Y-%m-%d")
    prev_cutoff = cutoff - timedelta(weeks=weeks_back)
    prev_cutoff_str = prev_cutoff.strftime("%Y-%m-%d")

    all_prs = store.get_merged_prs(base_branch="main")
    week_prs = [p for p in all_prs if (p.get("merged_at") or "") >= cutoff_str]
    prev_prs = [p for p in all_prs
                if prev_cutoff_str <= (p.get("merged_at") or "") < cutoff_str]

    all_builds = store.get_ci_builds()
    week_pr_nums = {p["number"] for p in week_prs}
    prev_pr_nums = {p["number"] for p in prev_prs}
    week_builds = [b for b in all_builds if b["pr_number"] in week_pr_nums]
    prev_builds = [b for b in all_builds if b["pr_number"] in prev_pr_nums]

    week_summary = ci_efficiency.compute_summary(week_builds)
    prev_summary = ci_efficiency.compute_summary(prev_builds)

    reverts = store.get_reverts()
    week_reverts = [r for r in reverts if (r.get("date") or "") >= cutoff_str]

    all_steps = store.get_all_build_steps()
    all_fail_msgs = store.get_all_build_failure_messages()
    week_build_ids = {b["build_id"] for b in week_builds}
    failed_build_ids = {b["build_id"] for b in week_builds if b["result"] == "failure"}

    pr_components: dict[int, list[str]] = {}
    for p in week_prs:
        comps = _parse_json_field(p.get("changed_components"))
        if comps:
            pr_components[p["number"]] = comps

    lines: list[str] = []
    _w = lines.append

    period_label = f"Week of {cutoff.strftime('%b %d')} – {now.strftime('%b %d, %Y')}"
    _w(f"# Weekly CI Health Digest")
    _w(f"**{period_label}**")
    _w("")

    # --- Headlines ---
    _w("## Headlines")
    _w("")
    _w(f"- **PRs merged:** {len(week_prs)}"
       f" (prev week: {len(prev_prs)}, "
       f"{'↑' if len(week_prs) > len(prev_prs) else '↓' if len(week_prs) < len(prev_prs) else '→'}"
       f")")

    fr = week_summary.get("cycle_failure_rate")
    prev_fr = prev_summary.get("cycle_failure_rate")
    fr_delta = ""
    if fr is not None and prev_fr is not None:
        diff = (fr - prev_fr) * 100
        if abs(diff) >= 1:
            fr_delta = f" ({'↑' if diff > 0 else '↓'}{abs(diff):.0f}pp vs prev week)"
    _w(f"- **Cycle failure rate:** {_fmt_pct(fr)}{fr_delta}")

    fps = week_summary.get("first_pass_success_rate")
    _w(f"- **First-pass success:** {_fmt_pct(fps)}")

    rt = week_summary.get("retest_tax")
    _w(f"- **Retest tax:** {rt:.2f} cycles/PR" if rt else "- **Retest tax:** N/A")

    _w(f"- **Reverts this week:** {len(week_reverts)}")
    _w("")

    # --- Component Risk Watch ---
    comp_builds: dict[str, list[dict]] = defaultdict(list)
    for b in week_builds:
        for comp in pr_components.get(b["pr_number"], ["unknown"]):
            comp_builds[comp].append(b)

    if comp_builds:
        _w("## Component Risk Watch")
        _w("")
        _w("| Component | Builds | Fail% | Retest Tax | Infra Fails | Risk |")
        _w("|-----------|--------|-------|------------|-------------|------|")

        comp_risks: list[tuple[str, dict, str]] = []
        for comp, cbuilds in sorted(comp_builds.items()):
            summary = ci_efficiency.compute_summary(cbuilds)
            fail_rate = summary.get("cycle_failure_rate")

            comp_failed_bids = {b["build_id"] for b in cbuilds if b["result"] == "failure"}
            infra_fails = sum(1 for s in all_steps
                             if s["build_id"] in comp_failed_bids
                             and s.get("is_infra") and s.get("level") == "Error")

            if fail_rate is not None and fail_rate > 0.5:
                risk = "HIGH"
            elif fail_rate is not None and fail_rate > 0.3:
                risk = "MEDIUM"
            else:
                risk = "low"

            comp_risks.append((comp, summary, risk))
            rt_str = f"{summary.get('retest_tax', 0):.1f}" if summary.get("retest_tax") else "-"
            _w(f"| {comp} | {summary.get('total_job_runs', 0)} | "
               f"{_fmt_pct(fail_rate)} | {rt_str} | {infra_fails} | **{risk}** |")

        _w("")

        high_risk = [c for c, _, r in comp_risks if r == "HIGH"]
        if high_risk:
            _w(f"**Action needed:** {', '.join(high_risk)} "
               f"{'has' if len(high_risk) == 1 else 'have'} >50% CI failure rate this week.")
            _w("")

    # --- Infrastructure vs Code ---
    week_step_failures = [s for s in all_steps
                         if s["build_id"] in failed_build_ids and s.get("level") == "Error"]
    if week_step_failures:
        infra_count = sum(1 for s in week_step_failures if s.get("is_infra"))
        code_count = len(week_step_failures) - infra_count
        total = len(week_step_failures)

        _w("## Infrastructure vs Code Failures")
        _w("")
        _w(f"- **Infrastructure failures:** {infra_count} ({infra_count/total*100:.0f}%) — "
           "cluster provisioning, pod scheduling, IPI install")
        _w(f"- **Code failures:** {code_count} ({code_count/total*100:.0f}%) — "
           "test execution, compilation, lint")
        _w("")
        if infra_count > code_count:
            _w("Infrastructure is the dominant failure mode this week. "
               "Check cluster pool health and IPI quotas before investigating test code.")
        elif code_count > 0:
            _w("Code issues are the primary failure source. "
               "Review the top error messages below for actionable fixes.")
        _w("")

    # --- Top Error Messages ---
    week_msgs = [m for m in all_fail_msgs if m["build_id"] in failed_build_ids]
    if week_msgs:
        msg_counts: dict[str, int] = defaultdict(int)
        msg_builds: dict[str, set[str]] = defaultdict(set)
        for m in week_msgs:
            key = m["message"][:120]
            msg_counts[key] += m.get("count", 1)
            msg_builds[key].add(m["build_id"])

        _w("## Top Error Messages")
        _w("")
        for msg, cnt in sorted(msg_counts.items(), key=lambda x: x[1], reverse=True)[:7]:
            n_builds = len(msg_builds[msg])
            _w(f"- `{msg}` — {cnt} occurrences across {n_builds} builds")
        _w("")

    # --- Revert Watch ---
    if week_reverts:
        _w("## Revert Watch")
        _w("")
        for r in week_reverts:
            pr_num = r.get("reverted_pr")
            pr_label = f" (PR #{pr_num})" if pr_num else ""
            _w(f"- {r['date']}: `{r['sha'][:12]}`{pr_label} — {r.get('message', '')[:80]}")
        _w("")

    # --- CI Duration Trends ---
    dur = week_summary.get("cycle_duration_minutes", {})
    prev_dur = prev_summary.get("cycle_duration_minutes", {})
    if dur.get("count", 0) > 0:
        _w("## CI Duration")
        _w("")
        _w(f"- **Median cycle time:** {dur.get('p50', 0):.0f} minutes")
        _w(f"- **P90 cycle time:** {dur.get('p90', 0):.0f} minutes")
        ci_hrs = week_summary.get("ci_hours_per_pr", {})
        if ci_hrs.get("mean"):
            _w(f"- **Avg CI wait per PR:** {ci_hrs['mean']:.1f} hours")

        if prev_dur.get("p50") and dur.get("p50"):
            diff = dur["p50"] - prev_dur["p50"]
            if abs(diff) >= 5:
                direction = "slower" if diff > 0 else "faster"
                _w(f"- **Trend:** {abs(diff):.0f} min {direction} than previous week")
        _w("")

    # --- AI Adoption ---
    ai_prs = [p for p in week_prs if p.get("is_ai_assisted")]
    if ai_prs:
        _w("## AI-Assisted Commits")
        _w("")
        _w(f"- **{len(ai_prs)} of {len(week_prs)} PRs** ({len(ai_prs)/len(week_prs)*100:.0f}%) "
           f"this week had AI-assisted commits")
        ai_builds = [b for b in week_builds if b["pr_number"] in {p["number"] for p in ai_prs}]
        if ai_builds:
            ai_summary = ci_efficiency.compute_summary(ai_builds)
            ai_fr = ai_summary.get("cycle_failure_rate")
            _w(f"- **AI-PR failure rate:** {_fmt_pct(ai_fr)} (vs {_fmt_pct(fr)} overall)")
        _w("")

    # --- Release Readiness ---
    releases = store.get_releases()
    recent_releases = [r for r in releases if (r.get("published") or "") >= cutoff_str]
    cherry_picks = store.get_cherry_picks()
    week_cps = [c for c in cherry_picks if (c.get("merged_at") or "") >= cutoff_str]

    if recent_releases or week_cps:
        _w("## Release Activity")
        _w("")
        if recent_releases:
            for r in recent_releases:
                tag_type = "EA" if r.get("is_ea") else ("patch" if r.get("is_patch") else "stable")
                _w(f"- **{r['tag']}** ({tag_type}) published {r['published']}")
        if week_cps:
            _w(f"- **{len(week_cps)} cherry-picks** merged to downstream branches")
        _w("")

    _w("---")
    _w(f"*Generated {now.strftime('%Y-%m-%d %H:%M UTC')} from {len(all_builds)} total CI builds "
       f"across {len(all_prs)} PRs.*")
    _w("")

    return "\n".join(lines)
