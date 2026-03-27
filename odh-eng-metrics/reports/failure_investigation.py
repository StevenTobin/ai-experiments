"""Per-PR failure investigation report.

Generates a structured report for a specific PR (or the most-recently-failed PR)
with full context: git metadata, CI results, failure details, historical patterns,
and suggested investigation paths.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict

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


def _find_pr(store: Store, pr_number: int | None) -> dict | None:
    """Find a specific PR, or the most recently merged PR with CI failures."""
    prs = store.get_merged_prs(base_branch="main")
    if not prs:
        return None

    if pr_number is not None:
        for p in prs:
            if p["number"] == pr_number:
                return p
        return None

    builds = store.get_ci_builds()
    failed_pr_nums = {b["pr_number"] for b in builds if b["result"] == "failure"}

    for p in reversed(prs):
        if p["number"] in failed_pr_nums:
            return p
    return prs[-1] if prs else None


def generate(store: Store, pr_number: int | None = None) -> str:
    """Generate a failure investigation report for a PR.

    If pr_number is None, uses the most recently merged PR that had CI failures.
    """
    pr = _find_pr(store, pr_number)
    if pr is None:
        return f"No PR found{f' with number {pr_number}' if pr_number else ''}."

    builds = store.get_ci_builds(pr_number=pr["number"])
    all_builds = store.get_ci_builds()
    components = _parse_json_field(pr.get("changed_components"))
    jira_keys = _parse_json_field(pr.get("jira_keys"))
    changed_files = _parse_json_field(pr.get("changed_files"))

    cycles = ci_efficiency._derive_cycles(sorted(builds, key=lambda b: b["build_id"]))
    failed_cycles = [c for c in cycles if c["result"] == "failure"]

    lines: list[str] = []
    _w = lines.append

    _w(f"# CI Failure Investigation: PR #{pr['number']}")
    _w("")

    # --- Context ---
    _w("## Context")
    _w("")
    _w(f"- **Title:** {pr.get('title', 'N/A')}")
    _w(f"- **Author:** {pr.get('author', 'N/A')}")
    _w(f"- **Merged:** {pr.get('merged_at', 'N/A')}")
    _w(f"- **Components:** {', '.join(components) if components else 'unknown'}")
    _w(f"- **Jira tickets:** {', '.join(jira_keys) if jira_keys else 'none detected'}")
    _w(f"- **AI-assisted:** {'Yes' if pr.get('is_ai_assisted') else 'No'}")
    _w(f"- **Size:** +{pr.get('additions', 0)} / -{pr.get('deletions', 0)}")
    if changed_files:
        _w(f"- **Files changed:** {len(changed_files)}")
        for f in changed_files[:10]:
            _w(f"  - `{f}`")
        if len(changed_files) > 10:
            _w(f"  - ... and {len(changed_files) - 10} more")
    _w("")

    # --- CI Results ---
    _w("## CI Results")
    _w("")
    _w(f"- **Total builds:** {len(builds)}")
    _w(f"- **Test cycles:** {len(cycles)}")
    _w(f"- **Failed cycles:** {len(failed_cycles)}")
    passed = [c for c in cycles if c["result"] == "success"]
    _w(f"- **Passed cycles:** {len(passed)}")
    if cycles:
        _w(f"- **First cycle result:** {'PASS' if cycles[0]['result'] == 'success' else 'FAIL'}")
        total_wait = sum(c["duration_seconds"] for c in cycles) / 60
        _w(f"- **Total CI wait time:** {total_wait:.0f} minutes ({total_wait / 60:.1f} hours)")
    _w("")

    # --- Failure Details ---
    if failed_cycles:
        _w("## Failure Details")
        _w("")

        failed_build_ids = {b["build_id"] for b in builds if b["result"] == "failure"}

        step_data = store.get_build_steps()
        pr_step_failures = [s for s in step_data
                           if s["build_id"] in failed_build_ids and s.get("level") == "Error"]
        if pr_step_failures:
            step_counts: dict[str, int] = defaultdict(int)
            infra_count = 0
            for s in pr_step_failures:
                step_counts[s["step_name"]] += 1
                if s.get("is_infra"):
                    infra_count += 1

            _w("### Failing Steps")
            _w("")
            for step, cnt in sorted(step_counts.items(), key=lambda x: x[1], reverse=True):
                is_infra = any(s["step_name"] == step and s.get("is_infra") for s in pr_step_failures)
                tag = " (infra)" if is_infra else ""
                _w(f"- **{step}**: {cnt} failure(s){tag}")

            total_step_failures = sum(step_counts.values())
            if total_step_failures > 0:
                _w("")
                _w(f"Infrastructure vs code: {infra_count}/{total_step_failures} "
                   f"failures ({infra_count / total_step_failures * 100:.0f}%) are infrastructure-related")
            _w("")

        fail_msgs = store.get_build_failure_messages()
        pr_msgs = [m for m in fail_msgs if m["build_id"] in failed_build_ids]
        if pr_msgs:
            msg_counts: dict[str, int] = defaultdict(int)
            for m in pr_msgs:
                msg_counts[m["message"][:120]] += m.get("count", 1)

            _w("### Error Messages")
            _w("")
            for msg, cnt in sorted(msg_counts.items(), key=lambda x: x[1], reverse=True)[:5]:
                _w(f"- `{msg}` (x{cnt})")
            _w("")

        resource_builds = [b for b in builds if b["build_id"] in failed_build_ids]
        cpus = [b["peak_cpu_cores"] for b in resource_builds if b.get("peak_cpu_cores")]
        mems = [b["peak_memory_bytes"] for b in resource_builds if b.get("peak_memory_bytes")]
        if cpus or mems:
            _w("### Resource Usage During Failures")
            _w("")
            if cpus:
                _w(f"- **Peak CPU:** {max(cpus):.1f} cores (avg {sum(cpus)/len(cpus):.1f})")
            if mems:
                max_gb = max(mems) / (1024**3)
                avg_gb = sum(mems) / len(mems) / (1024**3)
                _w(f"- **Peak Memory:** {max_gb:.1f} GB (avg {avg_gb:.1f} GB)")
            _w("")

    # --- Historical Pattern ---
    _w("## Historical Pattern")
    _w("")

    if components:
        comp_builds: dict[str, list[dict]] = defaultdict(list)
        pr_components_map: dict[int, list[str]] = {}
        for p in store.get_merged_prs(base_branch="main"):
            comps = _parse_json_field(p.get("changed_components"))
            if comps:
                pr_components_map[p["number"]] = comps
        for b in all_builds:
            for comp in pr_components_map.get(b["pr_number"], []):
                comp_builds[comp].append(b)

        _w("### Component CI Health")
        _w("")
        for comp in components:
            cbuilds = comp_builds.get(comp, [])
            if not cbuilds:
                _w(f"- **{comp}**: no CI data")
                continue
            summary = ci_efficiency.compute_summary(cbuilds)
            fr = summary.get("cycle_failure_rate")
            rt = summary.get("retest_tax")
            fr_str = f"{fr * 100:.0f}%" if fr is not None else "N/A"
            rt_str = f"{rt:.1f}" if rt is not None else "N/A"
            _w(f"- **{comp}**: {fr_str} failure rate, {rt_str} retest tax, "
               f"{summary.get('total_prs_with_ci', 0)} PRs with CI data")
        _w("")

    all_fail_msgs = store.get_all_build_failure_messages()
    if pr_msgs and all_fail_msgs:
        pr_msg_texts = {m["message"][:120] for m in pr_msgs}
        matching_builds: dict[str, set[str]] = defaultdict(set)
        for m in all_fail_msgs:
            key = m["message"][:120]
            if key in pr_msg_texts:
                matching_builds[key].add(m["build_id"])

        if matching_builds:
            _w("### Same Errors in Other Builds")
            _w("")
            for msg, bids in sorted(matching_builds.items(), key=lambda x: len(x[1]), reverse=True)[:3]:
                other_count = len(bids) - len(failed_build_ids & bids)
                if other_count > 0:
                    _w(f"- `{msg}`: appeared in **{other_count} other builds** beyond this PR")
            _w("")

    reverts = store.get_reverts()
    pr_reverted = any(r.get("reverted_pr") == pr["number"] for r in reverts)
    if pr_reverted:
        _w("### ⚠ This PR Was Later Reverted")
        _w("")
        for r in reverts:
            if r.get("reverted_pr") == pr["number"]:
                _w(f"- Reverted on {r['date']} (commit `{r['sha'][:12]}`)")
        _w("")

    # --- Flakiness Assessment ---
    if len(cycles) >= 2:
        _w("## Flakiness Assessment")
        _w("")
        cycle_results = [c["result"] for c in cycles]
        alternations = sum(1 for i in range(1, len(cycle_results))
                          if cycle_results[i] != cycle_results[i-1])
        if alternations >= 2:
            _w("**Likely flaky.** CI results alternated between pass and fail across cycles "
               f"({alternations} alternations in {len(cycles)} cycles), suggesting "
               "non-deterministic failures rather than a code regression.")
        elif failed_cycles and passed:
            _w(f"**Potentially flaky.** Failed {len(failed_cycles)} time(s) but eventually passed "
               f"after {len(cycles)} cycles without code changes between retests.")
        elif not passed:
            _w("**Consistent failure.** All CI cycles failed, suggesting a genuine code issue "
               "rather than flakiness.")
        else:
            _w("**Clean pass.** CI passed without failures.")
        _w("")

    # --- Suggested Investigation ---
    _w("## Suggested Investigation")
    _w("")

    if pr_step_failures:
        infra_steps = [s for s in pr_step_failures if s.get("is_infra")]
        code_steps = [s for s in pr_step_failures if not s.get("is_infra")]
        if infra_steps and not code_steps:
            _w("1. **Infrastructure issue detected.** All failing steps are infrastructure "
               "(provisioning, scheduling). Check cluster pool availability and IPI install health.")
        elif infra_steps:
            _w("1. **Mixed infra + code failures.** Some failures are infrastructure-related. "
               "Re-run CI to separate infra flakes from code regressions.")
        else:
            _w("1. **Code failure.** Failing steps are test execution. Review the error messages above.")

    if pr_msgs:
        top_msg = max(msg_counts.items(), key=lambda x: x[1])
        _w(f"2. **Most common error:** `{top_msg[0][:100]}` — search the codebase for this "
           f"assertion or timeout and check recent changes to the affected code path.")

    if components:
        for comp in components:
            _w(f"3. **Review {comp} component** — "
               f"Check recent changes in `internal/controller/components/{comp}/`")

    if jira_keys:
        for key in jira_keys:
            _w(f"4. **Jira context:** https://redhat.atlassian.net/browse/{key}")

    _w("")
    return "\n".join(lines)
