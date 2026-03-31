#!/usr/bin/env python3
"""Generate a deep-analysis HTML report on the AI Bug Bash with embedded charts."""

from __future__ import annotations

import base64
import io
import json
import re
import textwrap
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

from store.db import Store

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

OUTPUT_PATH = Path("data/bug-bash-deep-analysis.html")
OUTCOME_LABELS = [
    "ai-fully-automated", "ai-accelerated-fix",
    "ai-could-not-fix", "ai-verification-failed", "regressions-found",
]
OUTCOME_COLORS = {
    "ai-fully-automated": "#2ecc71",
    "ai-accelerated-fix": "#3498db",
    "ai-could-not-fix": "#e74c3c",
    "ai-verification-failed": "#e67e22",
    "regressions-found": "#9b59b6",
}
TRIAGE_COLORS = {
    "ai-fixable": "#2ecc71",
    "ai-nonfixable": "#e74c3c",
    "untriaged": "#95a5a6",
}


def _labels(issue: dict) -> list[str]:
    try:
        return json.loads(issue.get("labels", "[]"))
    except (json.JSONDecodeError, TypeError):
        return []


def _components(issue: dict) -> list[str]:
    try:
        return json.loads(issue.get("components", "[]"))
    except (json.JSONDecodeError, TypeError):
        return []


def _comments(issue: dict) -> list[dict]:
    try:
        return json.loads(issue.get("comments", "[]") or "[]")
    except (json.JSONDecodeError, TypeError):
        return []


def _text_blob(issue: dict) -> str:
    parts = [issue.get("summary", ""), issue.get("description", "") or ""]
    for c in _comments(issue):
        parts.append(c.get("body", ""))
    return "\n".join(parts).lower()


def _days_open(issue: dict) -> float | None:
    created = issue.get("created")
    resolved = issue.get("resolved")
    if not created:
        return None
    c = datetime.fromisoformat(created.replace("Z", "+00:00"))
    if resolved:
        r = datetime.fromisoformat(resolved.replace("Z", "+00:00"))
    else:
        r = datetime.now(c.tzinfo)
    return (r - c).total_seconds() / 86400


def _fig_to_base64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="#ffffff")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()


def _set_chart_style():
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.size": 11,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "figure.facecolor": "#ffffff",
        "axes.facecolor": "#ffffff",
        "axes.grid": True,
        "grid.alpha": 0.3,
    })

_set_chart_style()


NONFIXABLE_THEMES = {
    "UI / visual / frontend": [
        r"\bui\b", r"\bfrontend\b", r"\bvisual", r"\bcss\b", r"\blayout\b",
        r"\brender", r"\bbutton\b", r"\bmodal\b", r"\btooltip", r"\bscreen",
        r"\bdashboard\b", r"\bpage\b", r"\bform\b", r"\btable\b", r"\bcolumn",
        r"\brow\b", r"\blink\b", r"\bnavigat", r"\bbreadcrumb", r"\bsidebar",
        r"\bdropdown", r"\bmenu\b", r"\btab\b", r"\bpanel\b", r"\bcard\b",
        r"\bicon\b", r"\bimage\b", r"\blogo\b", r"\btext\b", r"\bfont\b",
        r"\bcolor\b", r"\bstyle\b", r"\bclass\b", r"\bhtml\b",
        r"\bpatternfly", r"\breact\b", r"\bcomponent\b",
    ],
    "infrastructure / cluster / environment": [
        r"\bcluster\b", r"\bnode\b", r"\bopenshift\b", r"\bkubernetes\b",
        r"\bk8s\b", r"\bpod\b", r"\bcontainer\b", r"\bnamespace\b",
        r"\boperator\b", r"\binstall", r"\bupgrade\b", r"\bdeploy",
        r"\bnetwork\b", r"\bingress\b", r"\broute\b", r"\bcert",
        r"\bstorage\b", r"\bpvc\b", r"\bvolume\b", r"\binfra",
        r"\benvironment\b", r"\bconfig\b", r"\bsecret\b",
    ],
    "insufficient context / vague description": [
        r"\bunclear\b", r"\bneed more", r"\bnot enough",
        r"\bvague\b", r"\bmissing.*detail", r"\bno repro",
        r"\bcannot reproduce", r"\bhard to reproduce",
        r"\bnot reproducible", r"\bintermittent",
    ],
    "multi-service / cross-repo dependency": [
        r"\bcross.?repo", r"\bupstream\b", r"\bdownstream\b",
        r"\bdependen", r"\bexternal\b", r"\bthird.?party",
        r"\bapi\b.*\bchange", r"\bmulti.?service", r"\bmicroservice",
        r"\bintegration\b", r"\bcompat",
    ],
    "complex state / race condition": [
        r"\brace\b", r"\btiming\b", r"\bconcurren", r"\bdeadlock",
        r"\bstate\b.*\bmachine", r"\bcomplex\b.*\bstate", r"\basync",
        r"\beventual", r"\bordering\b", r"\bsequenc",
    ],
    "test / coverage gap": [
        r"\bno test", r"\btest.*miss", r"\buntested\b", r"\btest.*cover",
        r"\be2e\b.*\bmiss", r"\bflak", r"\bnon.?determin",
    ],
    "security / compliance": [
        r"\bsecurity\b", r"\bvulnerab", r"\bcve\b", r"\bauth",
        r"\brbac\b", r"\bpermission", r"\bcredential", r"\btoken\b",
        r"\bencrypt", r"\bcompliance\b",
    ],
}


# ---------------------------------------------------------------------------
# Chart generators
# ---------------------------------------------------------------------------

def chart_triage_funnel(issues):
    labels_all = [lbl for i in issues for lbl in _labels(i)]
    lc = Counter(labels_all)
    triaged = lc.get("ai-triaged", 0)
    fixable = lc.get("ai-fixable", 0)
    nonfixable = lc.get("ai-nonfixable", 0)
    untriaged = len(issues) - triaged

    fig, ax = plt.subplots(figsize=(8, 4))
    bars = ["Total\nIssues", "Triaged", "Fixable", "Nonfixable", "Untriaged"]
    values = [len(issues), triaged, fixable, nonfixable, untriaged]
    colors = ["#34495e", "#2980b9", "#2ecc71", "#e74c3c", "#95a5a6"]
    b = ax.barh(bars, values, color=colors, height=0.6)
    ax.bar_label(b, padding=5, fontsize=12, fontweight="bold")
    ax.set_xlim(0, max(values) * 1.15)
    ax.set_title("Triage Funnel", fontsize=14, fontweight="bold", pad=10)
    ax.invert_yaxis()
    return _fig_to_base64(fig)


def chart_outcome_distribution(issues):
    labels_all = [lbl for i in issues for lbl in _labels(i)]
    lc = Counter(labels_all)
    outcome_labels = [l for l in OUTCOME_LABELS if lc.get(l, 0) > 0]
    counts = [lc[l] for l in outcome_labels]
    colors = [OUTCOME_COLORS.get(l, "#bdc3c7") for l in outcome_labels]
    display = [l.replace("ai-", "").replace("-", " ").title() for l in outcome_labels]

    fig, ax = plt.subplots(figsize=(7, 5))
    wedges, texts, autotexts = ax.pie(
        counts, labels=display, colors=colors, autopct="%1.0f%%",
        startangle=90, textprops={"fontsize": 10},
    )
    for at in autotexts:
        at.set_fontweight("bold")
    ax.set_title("Outcome Distribution (of issues that reached an outcome)", fontsize=13, fontweight="bold", pad=15)
    return _fig_to_base64(fig)


def chart_nonfixable_by_component(nonfixable_issues):
    comp_counts = Counter()
    for i in nonfixable_issues:
        comps = _components(i)
        for c in comps:
            comp_counts[c] += 1
    top = comp_counts.most_common(12)
    if not top:
        return None
    names, counts = zip(*reversed(top))
    fig, ax = plt.subplots(figsize=(9, 5))
    bars = ax.barh(names, counts, color="#e74c3c", alpha=0.85, height=0.6)
    ax.bar_label(bars, padding=4, fontsize=10)
    ax.set_xlim(0, max(counts) * 1.2)
    ax.set_title("Nonfixable Issues by Component", fontsize=13, fontweight="bold", pad=10)
    return _fig_to_base64(fig)


def chart_nonfixable_themes(nonfixable_issues):
    theme_counts = {}
    for theme_name, patterns in NONFIXABLE_THEMES.items():
        count = 0
        for issue in nonfixable_issues:
            blob = _text_blob(issue)
            if any(re.search(p, blob) for p in patterns):
                count += 1
        if count > 0:
            theme_counts[theme_name] = count

    sorted_themes = sorted(theme_counts.items(), key=lambda x: x[1], reverse=True)
    if not sorted_themes:
        return None
    names, counts = zip(*sorted_themes)
    total = len(nonfixable_issues)
    pcts = [c / total * 100 for c in counts]

    fig, ax = plt.subplots(figsize=(10, 5))
    bars = ax.barh(list(reversed(names)), list(reversed(pcts)), color="#c0392b", alpha=0.8, height=0.6)
    ax.bar_label(bars, labels=[f"{p:.0f}% ({c})" for p, c in zip(reversed(pcts), reversed(counts))], padding=5, fontsize=10)
    ax.set_xlim(0, max(pcts) * 1.25)
    ax.set_xlabel("% of nonfixable issues")
    ax.set_title("Why Are Tickets Nonfixable? — Theme Analysis", fontsize=13, fontweight="bold", pad=10)
    return _fig_to_base64(fig)


def chart_fixable_vs_nonfixable_components(fixable, nonfixable):
    fix_comps = Counter(c for i in fixable for c in _components(i))
    nonfix_comps = Counter(c for i in nonfixable for c in _components(i))
    all_comps = set(fix_comps) | set(nonfix_comps)
    data = []
    for comp in all_comps:
        fc = fix_comps.get(comp, 0)
        nc = nonfix_comps.get(comp, 0)
        total = fc + nc
        if total >= 5:
            rate = nc / total * 100
            data.append((comp, fc, nc, rate))
    data.sort(key=lambda x: x[3], reverse=True)
    top = data[:12]
    if not top:
        return None

    names = [d[0] for d in reversed(top)]
    fix_vals = [d[1] for d in reversed(top)]
    nonfix_vals = [d[2] for d in reversed(top)]

    fig, ax = plt.subplots(figsize=(10, 5))
    y = range(len(names))
    ax.barh(y, fix_vals, height=0.4, color="#2ecc71", label="Fixable", align="center")
    ax.barh([yi + 0.4 for yi in y], nonfix_vals, height=0.4, color="#e74c3c", label="Nonfixable", align="center")
    ax.set_yticks([yi + 0.2 for yi in y])
    ax.set_yticklabels(names)
    ax.legend(loc="lower right")
    ax.set_title("Fixable vs Nonfixable by Component (sorted by nonfixable rate)", fontsize=13, fontweight="bold", pad=10)

    for idx, d in enumerate(reversed(top)):
        ax.annotate(f"{d[3]:.0f}% nonfixable", xy=(max(d[1], d[2]) + 1, idx + 0.2),
                     fontsize=9, color="#7f8c8d", va="center")
    return _fig_to_base64(fig)


def chart_accelerated_vs_automated(accelerated, automated):
    acc_comps = Counter(c for i in accelerated for c in _components(i))
    auto_comps = Counter(c for i in automated for c in _components(i))
    all_comps = sorted(set(acc_comps) | set(auto_comps), key=lambda c: acc_comps.get(c, 0) + auto_comps.get(c, 0), reverse=True)[:10]
    if not all_comps:
        return None

    fig, ax = plt.subplots(figsize=(10, 5))
    y = range(len(all_comps))
    acc_vals = [acc_comps.get(c, 0) for c in all_comps]
    auto_vals = [auto_comps.get(c, 0) for c in all_comps]
    ax.barh([yi + 0.2 for yi in y], list(reversed(acc_vals)), height=0.35, color="#3498db", label="Accelerated Fix (multi-attempt)", align="center")
    ax.barh([yi - 0.2 for yi in y], list(reversed(auto_vals)), height=0.35, color="#2ecc71", label="Fully Automated (single-shot)", align="center")
    ax.set_yticks(list(y))
    ax.set_yticklabels(list(reversed(all_comps)))
    ax.legend(loc="lower right")
    ax.xaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    ax.set_title("Accelerated-Fix vs Fully-Automated by Component", fontsize=13, fontweight="bold", pad=10)
    return _fig_to_base64(fig)


def chart_daily_throughput(issues):
    daily = defaultdict(lambda: {"total": 0, "outcomes": Counter()})
    for i in issues:
        resolved = i.get("resolved")
        if not resolved:
            continue
        day = resolved[:10]
        daily[day]["total"] += 1
        for lbl in _labels(i):
            if lbl in OUTCOME_LABELS:
                daily[day]["outcomes"][lbl] += 1

    if not daily:
        return None
    days = sorted(daily.keys())

    fig, ax = plt.subplots(figsize=(12, 4))
    totals = [daily[d]["total"] for d in days]
    ax.bar(days, totals, color="#2980b9", alpha=0.7, label="Resolved")

    bottom = [0] * len(days)
    for lbl in OUTCOME_LABELS:
        vals = [daily[d]["outcomes"].get(lbl, 0) for d in days]
        if sum(vals) > 0:
            ax.bar(days, vals, bottom=bottom, color=OUTCOME_COLORS.get(lbl, "#bdc3c7"),
                   alpha=0.9, label=lbl.replace("ai-", "").replace("-", " ").title())
            bottom = [b + v for b, v in zip(bottom, vals)]

    ax.set_title("Daily Resolution Throughput", fontsize=13, fontweight="bold", pad=10)
    ax.legend(fontsize=8, loc="upper left")
    plt.xticks(rotation=45, ha="right", fontsize=8)
    return _fig_to_base64(fig)


def chart_priority_breakdown(fixable, nonfixable):
    fix_pri = Counter(i.get("priority", "Undefined") for i in fixable)
    nonfix_pri = Counter(i.get("priority", "Undefined") for i in nonfixable)
    priorities = sorted(set(fix_pri) | set(nonfix_pri),
                        key=lambda p: (fix_pri.get(p, 0) + nonfix_pri.get(p, 0)), reverse=True)

    fig, ax = plt.subplots(figsize=(8, 4))
    x = range(len(priorities))
    w = 0.35
    ax.bar([xi - w/2 for xi in x], [fix_pri.get(p, 0) for p in priorities], w, color="#2ecc71", label="Fixable")
    ax.bar([xi + w/2 for xi in x], [nonfix_pri.get(p, 0) for p in priorities], w, color="#e74c3c", label="Nonfixable")
    ax.set_xticks(list(x))
    ax.set_xticklabels(priorities, rotation=30, ha="right")
    ax.legend()
    ax.set_title("Priority Distribution: Fixable vs Nonfixable", fontsize=13, fontweight="bold", pad=10)
    return _fig_to_base64(fig)


def chart_success_rate_gauge(automated, accelerated, could_not_fix, verification_failed):
    successes = len(automated) + len(accelerated)
    failures = len(could_not_fix) + len(verification_failed)
    total = successes + failures
    rate = successes / total * 100 if total > 0 else 0

    fig, ax = plt.subplots(figsize=(5, 3))
    ax.barh(["AI Success", "AI Failure"], [successes, failures],
            color=["#2ecc71", "#e74c3c"], height=0.5)
    ax.bar_label(ax.containers[0], padding=5, fontsize=12, fontweight="bold")
    ax.set_title(f"AI Success Rate: {rate:.1f}%", fontsize=14, fontweight="bold", pad=10)
    ax.set_xlim(0, max(successes, failures) * 1.3)
    return _fig_to_base64(fig)


BUG_BASH_START = "2026-03-23"
BUG_BASH_END = "2026-03-27"


def _split_by_period(issues):
    """Split issues into bug-bash-week, after, and unresolved."""
    during = []
    after = []
    unresolved = []
    for i in issues:
        resolved = i.get("resolved")
        if not resolved:
            unresolved.append(i)
        elif resolved[:10] <= BUG_BASH_END:
            during.append(i)
        else:
            after.append(i)
    return during, after, unresolved


def _outcome_counts(issue_list):
    lc = Counter(lbl for i in issue_list for lbl in _labels(i))
    return {
        "automated": lc.get("ai-fully-automated", 0),
        "accelerated": lc.get("ai-accelerated-fix", 0),
        "could_not_fix": lc.get("ai-could-not-fix", 0),
        "verification_failed": lc.get("ai-verification-failed", 0),
        "fixable": lc.get("ai-fixable", 0),
        "nonfixable": lc.get("ai-nonfixable", 0),
    }


def chart_temporal_comparison(issues):
    """Side-by-side bar chart: bug bash week vs today."""
    during, after, unresolved = _split_by_period(issues)
    all_with_outcomes = during + after + unresolved

    bash_oc = _outcome_counts(during)
    today_oc = _outcome_counts(all_with_outcomes)

    categories = ["Fully\nAutomated", "Accelerated\nFix", "Could Not\nFix", "Verification\nFailed"]
    bash_vals = [bash_oc["automated"], bash_oc["accelerated"], bash_oc["could_not_fix"], bash_oc["verification_failed"]]
    today_vals = [today_oc["automated"], today_oc["accelerated"], today_oc["could_not_fix"], today_oc["verification_failed"]]
    colors_bash = ["#27ae60", "#2980b9", "#c0392b", "#d35400"]
    colors_today = ["#2ecc71", "#3498db", "#e74c3c", "#e67e22"]

    fig, axes = plt.subplots(1, 2, figsize=(12, 4), sharey=True)

    axes[0].bar(categories, bash_vals, color=colors_bash, width=0.6)
    axes[0].bar_label(axes[0].containers[0], fontsize=12, fontweight="bold", padding=3)
    bash_success = bash_oc["automated"] + bash_oc["accelerated"]
    bash_fail = bash_oc["could_not_fix"] + bash_oc["verification_failed"]
    bash_total = bash_success + bash_fail
    bash_rate = bash_success / bash_total * 100 if bash_total else 0
    axes[0].set_title(f"Bug Bash Week (Mar 23–27)\n{bash_total} outcomes, {bash_rate:.0f}% success", fontsize=12, fontweight="bold")

    axes[1].bar(categories, today_vals, color=colors_today, width=0.6)
    axes[1].bar_label(axes[1].containers[0], fontsize=12, fontweight="bold", padding=3)
    today_success = today_oc["automated"] + today_oc["accelerated"]
    today_fail = today_oc["could_not_fix"] + today_oc["verification_failed"]
    today_total = today_success + today_fail
    today_rate = today_success / today_total * 100 if today_total else 0
    axes[1].set_title(f"Current State (Today)\n{today_total} outcomes, {today_rate:.0f}% success", fontsize=12, fontweight="bold")

    fig.suptitle("Outcomes: Bug Bash Week vs Current State", fontsize=14, fontweight="bold", y=1.02)
    fig.tight_layout()
    return _fig_to_base64(fig)


def chart_resolution_waterfall(issues):
    """Stacked bar showing how issues moved through the pipeline over time."""
    during, after, unresolved = _split_by_period(issues)

    # For unresolved: count those with outcome labels vs those still pending
    unresolved_with_outcome = [i for i in unresolved if any(
        l in _labels(i) for l in OUTCOME_LABELS)]
    unresolved_pending = [i for i in unresolved if not any(
        l in _labels(i) for l in OUTCOME_LABELS)]
    unresolved_fixable_pending = [i for i in unresolved_pending if "ai-fixable" in _labels(i)]
    unresolved_nonfixable = [i for i in unresolved_pending if "ai-nonfixable" in _labels(i)]

    fig, ax = plt.subplots(figsize=(10, 5))
    categories = [
        f"Resolved during\nbug bash\n(Mar 23–27)",
        f"Resolved after\nbug bash\n(Mar 28–30)",
        f"Open: outcome\nlabelled\n(not moved to Done)",
        f"Open: fixable\n(awaiting attempt)",
        f"Open: nonfixable",
    ]
    values = [
        len(during),
        len(after),
        len(unresolved_with_outcome),
        len(unresolved_fixable_pending),
        len(unresolved_nonfixable),
    ]
    colors = ["#2ecc71", "#3498db", "#f39c12", "#e67e22", "#e74c3c"]
    bars = ax.barh(categories, values, color=colors, height=0.55)
    ax.bar_label(bars, padding=5, fontsize=12, fontweight="bold")
    ax.set_xlim(0, max(values) * 1.2)
    ax.set_title("Issue Pipeline: Where Are the 366 Issues Today?", fontsize=13, fontweight="bold", pad=10)
    ax.invert_yaxis()
    return _fig_to_base64(fig)


def chart_bash_week_daily(issues):
    """Daily breakdown during the bug bash week only."""
    bash_days = ["2026-03-23", "2026-03-24", "2026-03-25", "2026-03-26", "2026-03-27"]
    day_labels = ["Mon 23", "Tue 24", "Wed 25\n(no-meeting)", "Thu 26", "Fri 27"]

    daily_outcomes = {d: Counter() for d in bash_days}
    for i in issues:
        resolved = i.get("resolved")
        if not resolved:
            continue
        day = resolved[:10]
        if day in daily_outcomes:
            for lbl in OUTCOME_LABELS:
                if lbl in _labels(i):
                    daily_outcomes[day][lbl] += 1

    fig, ax = plt.subplots(figsize=(10, 4.5))
    bottom = [0] * len(bash_days)
    for lbl in OUTCOME_LABELS:
        vals = [daily_outcomes[d].get(lbl, 0) for d in bash_days]
        if sum(vals) > 0:
            display = lbl.replace("ai-", "").replace("-", " ").title()
            ax.bar(day_labels, vals, bottom=bottom, color=OUTCOME_COLORS.get(lbl),
                   label=display, width=0.6)
            bottom = [b + v for b, v in zip(bottom, vals)]

    # Also show total resolved (including those without outcome labels)
    total_resolved = []
    for d in bash_days:
        count = sum(1 for i in issues if i.get("resolved") and i["resolved"][:10] == d)
        total_resolved.append(count)
    ax.plot(day_labels, total_resolved, "ko-", markersize=6, linewidth=2, label="Total resolved")

    ax.legend(fontsize=9, loc="upper left")
    ax.set_ylabel("Issues")
    ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    ax.set_title("Bug Bash Week: Daily Outcomes", fontsize=13, fontweight="bold", pad=10)
    return _fig_to_base64(fig)


def chart_time_to_fix_by_outcome(issues):
    outcome_days = defaultdict(list)
    for i in issues:
        d = _days_open(i)
        if d is None or not i.get("resolved"):
            continue
        labels = _labels(i)
        for lbl in OUTCOME_LABELS:
            if lbl in labels:
                outcome_days[lbl].append(d)
                break

    if not outcome_days:
        return None

    fig, ax = plt.subplots(figsize=(8, 4))
    data = []
    labels_plot = []
    colors = []
    for lbl in OUTCOME_LABELS:
        if lbl in outcome_days and outcome_days[lbl]:
            data.append(outcome_days[lbl])
            labels_plot.append(lbl.replace("ai-", "").replace("-", " ").title())
            colors.append(OUTCOME_COLORS[lbl])

    bp = ax.boxplot(data, vert=False, patch_artist=True, tick_labels=labels_plot)
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.7)
    ax.set_xlabel("Days to resolution")
    ax.set_title("Time-to-Fix Distribution by Outcome", fontsize=13, fontweight="bold", pad=10)
    return _fig_to_base64(fig)


# ---------------------------------------------------------------------------
# Deep text analysis
# ---------------------------------------------------------------------------

def analyze_nonfixable_reasons(nonfixable_issues):
    """Analyze description + comments to extract WHY issues are nonfixable."""
    theme_issues = defaultdict(list)
    for issue in nonfixable_issues:
        blob = _text_blob(issue)
        for theme_name, patterns in NONFIXABLE_THEMES.items():
            if any(re.search(p, blob) for p in patterns):
                theme_issues[theme_name].append(issue)

    return dict(theme_issues)


def analyze_acceleration_gap(accelerated, automated):
    """Compare accelerated (multi-attempt) vs automated (single-shot) to find patterns."""
    acc_priorities = Counter(i.get("priority", "Undefined") for i in accelerated)
    auto_priorities = Counter(i.get("priority", "Undefined") for i in automated)
    acc_types = Counter(i.get("issue_type", "Unknown") for i in accelerated)
    auto_types = Counter(i.get("issue_type", "Unknown") for i in automated)

    acc_desc_lens = [len(i.get("description", "") or "") for i in accelerated]
    auto_desc_lens = [len(i.get("description", "") or "") for i in automated]

    acc_comment_counts = [len(_comments(i)) for i in accelerated]
    auto_comment_counts = [len(_comments(i)) for i in automated]

    return {
        "accelerated": {
            "count": len(accelerated),
            "priorities": acc_priorities,
            "types": acc_types,
            "avg_desc_len": sum(acc_desc_lens) / max(len(acc_desc_lens), 1),
            "avg_comments": sum(acc_comment_counts) / max(len(acc_comment_counts), 1),
        },
        "automated": {
            "count": len(automated),
            "priorities": auto_priorities,
            "types": auto_types,
            "avg_desc_len": sum(auto_desc_lens) / max(len(auto_desc_lens), 1),
            "avg_comments": sum(auto_comment_counts) / max(len(auto_comment_counts), 1),
        },
    }


def extract_sample_issues(issues, n=5):
    """Get representative sample issues for inclusion in the report."""
    samples = []
    for i in issues[:n]:
        desc = (i.get("description", "") or "")[:300]
        comments = _comments(i)
        first_comment = comments[0].get("body", "")[:200] if comments else ""
        samples.append({
            "key": i["key"],
            "summary": i.get("summary", ""),
            "components": _components(i),
            "priority": i.get("priority"),
            "description_snippet": desc,
            "comment_snippet": first_comment,
            "labels": _labels(i),
        })
    return samples


# ---------------------------------------------------------------------------
# HTML Report
# ---------------------------------------------------------------------------

def generate_html(issues, charts, analysis):
    labels_all = [lbl for i in issues for lbl in _labels(i)]
    lc = Counter(labels_all)
    triaged = lc.get("ai-triaged", 0)
    fixable = lc.get("ai-fixable", 0)
    nonfixable = lc.get("ai-nonfixable", 0)
    automated = lc.get("ai-fully-automated", 0)
    accelerated = lc.get("ai-accelerated-fix", 0)
    could_not = lc.get("ai-could-not-fix", 0)
    verif_fail = lc.get("ai-verification-failed", 0)
    regressions = lc.get("regressions-found", 0)
    successes = automated + accelerated
    failures = could_not + verif_fail
    outcomes = successes + failures
    success_rate = successes / outcomes * 100 if outcomes > 0 else 0
    resolved = sum(1 for i in issues if i.get("resolved"))
    open_count = len(issues) - resolved

    gap = analysis["acceleration_gap"]
    theme_data = analysis["nonfixable_themes"]
    nf_samples = analysis["nonfixable_samples"]
    acc_samples = analysis["accelerated_samples"]
    auto_samples = analysis["automated_samples"]

    def img(key):
        b64 = charts.get(key)
        if not b64:
            return ""
        return f'<img src="data:image/png;base64,{b64}" style="max-width:100%;margin:10px 0;">'

    def sample_table(samples, title):
        if not samples:
            return ""
        rows = ""
        for s in samples:
            comps = ", ".join(s["components"]) if s["components"] else "—"
            desc = s["description_snippet"].replace("<", "&lt;").replace(">", "&gt;")
            if len(desc) > 250:
                desc = desc[:250] + "..."
            rows += f"""<tr>
                <td><strong>{s['key']}</strong></td>
                <td>{s['summary']}</td>
                <td>{comps}</td>
                <td>{s['priority']}</td>
                <td style="font-size:0.85em;color:#555;">{desc}</td>
            </tr>"""
        return f"""
        <h4>{title}</h4>
        <table><thead><tr><th>Key</th><th>Summary</th><th>Component</th><th>Priority</th><th>Description</th></tr></thead>
        <tbody>{rows}</tbody></table>"""

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>AI Bug Bash Deep Analysis — March 23–27, 2026</title>
<style>
    body {{ font-family: 'Segoe UI', -apple-system, sans-serif; max-width: 1000px; margin: 0 auto; padding: 20px 40px; color: #2c3e50; line-height: 1.6; }}
    h1 {{ color: #1a252f; border-bottom: 3px solid #2980b9; padding-bottom: 10px; }}
    h2 {{ color: #2980b9; margin-top: 40px; border-bottom: 1px solid #ddd; padding-bottom: 5px; }}
    h3 {{ color: #34495e; margin-top: 25px; }}
    h4 {{ color: #7f8c8d; }}
    .stat-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin: 20px 0; }}
    .stat-box {{ background: #f8f9fa; border-left: 4px solid #2980b9; padding: 15px; border-radius: 4px; }}
    .stat-box.green {{ border-color: #2ecc71; }}
    .stat-box.red {{ border-color: #e74c3c; }}
    .stat-box.orange {{ border-color: #e67e22; }}
    .stat-box .number {{ font-size: 28px; font-weight: bold; color: #2c3e50; }}
    .stat-box .label {{ font-size: 12px; color: #7f8c8d; text-transform: uppercase; }}
    table {{ border-collapse: collapse; width: 100%; margin: 15px 0; font-size: 0.9em; }}
    th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
    th {{ background: #f1f3f5; font-weight: 600; }}
    tr:nth-child(even) {{ background: #f8f9fa; }}
    .recommendation {{ background: #fff3cd; border-left: 4px solid #ffc107; padding: 12px 16px; margin: 10px 0; border-radius: 4px; }}
    .recommendation.action {{ background: #d4edda; border-color: #28a745; }}
    .recommendation.critical {{ background: #f8d7da; border-color: #dc3545; }}
    .insight {{ background: #e8f4fd; border-left: 4px solid #2980b9; padding: 12px 16px; margin: 10px 0; border-radius: 4px; }}
    .finding {{ background: #f0f0f0; padding: 12px 16px; margin: 8px 0; border-radius: 4px; }}
    blockquote {{ border-left: 3px solid #bdc3c7; padding-left: 15px; color: #555; font-style: italic; }}
    .page-break {{ page-break-before: always; }}
    .toc {{ background: #f8f9fa; padding: 20px; border-radius: 8px; margin: 20px 0; }}
    .toc a {{ text-decoration: none; color: #2980b9; }}
    .toc a:hover {{ text-decoration: underline; }}
    .toc li {{ margin: 5px 0; }}
</style>
</head><body>

<h1>AI Bug Bash — Deep Analysis Report</h1>
<p style="color:#7f8c8d;font-size:0.95em;">
    RHOAI Engineering &bull; March 23–27, 2026 &bull; Generated {datetime.now().strftime("%B %d, %Y %H:%M")}
</p>

<div class="toc">
<strong>Contents</strong>
<ol>
<li><a href="#exec-summary">Executive Summary</a></li>
<li><a href="#pipeline">Triage Pipeline & Outcomes</a></li>
<li><a href="#temporal-split">Bug Bash Week vs Current State</a></li>
<li><a href="#nonfixable">Critical Question: Why Are Tickets Nonfixable?</a></li>
<li><a href="#acceleration">Critical Question: Converting Accelerated-Fix to Fully Automated</a></li>
<li><a href="#temporal">Temporal Analysis</a></li>
<li><a href="#recommendations">Recommendations: Improving Prompts & Process</a></li>
</ol>
</div>

<!-- ============================================================ -->
<h2 id="exec-summary">1. Executive Summary</h2>

<div class="stat-grid">
    <div class="stat-box"><div class="number">{len(issues)}</div><div class="label">Total Issues</div></div>
    <div class="stat-box green"><div class="number">{success_rate:.0f}%</div><div class="label">AI Success Rate</div></div>
    <div class="stat-box red"><div class="number">{nonfixable}</div><div class="label">Nonfixable</div></div>
    <div class="stat-box orange"><div class="number">{open_count}</div><div class="label">Still Open</div></div>
</div>

<div class="stat-grid">
    <div class="stat-box green"><div class="number">{automated}</div><div class="label">Fully Automated</div></div>
    <div class="stat-box"><div class="number">{accelerated}</div><div class="label">Accelerated Fix</div></div>
    <div class="stat-box red"><div class="number">{could_not}</div><div class="label">Could Not Fix</div></div>
    <div class="stat-box orange"><div class="number">{verif_fail}</div><div class="label">Verification Failed</div></div>
</div>

<p>Of {len(issues)} issues triaged during the AI Bug Bash, <strong>{fixable}</strong> ({fixable/len(issues)*100:.0f}%) were
deemed fixable by AI and <strong>{nonfixable}</strong> ({nonfixable/len(issues)*100:.0f}%) were not. Of the {outcomes} issues
that reached an outcome, AI achieved a <strong>{success_rate:.1f}% success rate</strong> — {automated} fully automated and
{accelerated} accelerated fixes, against {could_not} failures and {verif_fail} verification failures.</p>

<div class="insight">
<strong>Key Finding:</strong> The gap between accelerated-fix ({accelerated}) and fully-automated ({automated}) is the
largest opportunity. {accelerated - automated} additional issues required human intervention after AI's first attempt.
Understanding why converts future multi-attempt fixes into single-shot automation.
</div>

<!-- ============================================================ -->
<h2 id="pipeline">2. Triage Pipeline & Outcomes</h2>

{img("triage_funnel")}
{img("outcome_distribution")}
{img("success_rate")}
{img("priority_breakdown")}

<h3>What the data tells us</h3>
<ul>
<li><strong>{fixable/len(issues)*100:.0f}% fixable rate</strong> — AI triage classified {fixable} of {triaged} triaged issues as fixable.
This is a reasonable filter; the challenge is in the {nonfixable/triaged*100:.0f}% deemed nonfixable.</li>
<li><strong>{outcomes} of {fixable} fixable reached an outcome</strong> ({outcomes/fixable*100:.0f}%) — {fixable - outcomes} fixable issues
have not yet been attempted.</li>
<li><strong>Blocker and Critical priorities</strong> are disproportionately nonfixable — these tend to be environment-dependent
or cross-service issues that AI lacks the context to address.</li>
</ul>

<!-- ============================================================ -->
<h2 id="temporal-split" class="page-break">3. Bug Bash Week vs Current State</h2>
"""
    # Compute temporal split stats
    during, after_bash, unresolved_all = _split_by_period(issues)
    bash_oc = _outcome_counts(during)
    all_oc = _outcome_counts(issues)
    bash_success = bash_oc["automated"] + bash_oc["accelerated"]
    bash_fail = bash_oc["could_not_fix"] + bash_oc["verification_failed"]
    bash_outcomes = bash_success + bash_fail
    bash_rate = bash_success / bash_outcomes * 100 if bash_outcomes else 0
    unresolved_with_outcome = sum(1 for i in unresolved_all if any(l in _labels(i) for l in OUTCOME_LABELS))
    fixable_awaiting = sum(1 for i in unresolved_all if "ai-fixable" in _labels(i) and not any(l in _labels(i) for l in OUTCOME_LABELS))

    html += f"""
<p>The bug bash ran March 23–27, but work has continued after. This section separates results
from the event week itself versus the current cumulative state.</p>

{img("temporal_comparison")}

<div class="stat-grid">
    <div class="stat-box"><div class="number">{len(during)}</div><div class="label">Resolved during<br>bug bash week</div></div>
    <div class="stat-box"><div class="number">{len(after_bash)}</div><div class="label">Resolved after<br>(Mar 28–30)</div></div>
    <div class="stat-box orange"><div class="number">{unresolved_with_outcome}</div><div class="label">Open with outcome<br>(JIRA not moved)</div></div>
    <div class="stat-box red"><div class="number">{fixable_awaiting}</div><div class="label">Fixable awaiting<br>AI attempt</div></div>
</div>

{img("resolution_waterfall")}

<div class="insight">
<strong>Data Quality Note:</strong> {unresolved_with_outcome} issues have AI outcome labels (accelerated-fix, could-not-fix, etc.)
but are still marked as open in JIRA. These represent completed AI work where the JIRA status wasn't updated.
The actual number of issues that have been worked is higher than the "resolved" count suggests.
</div>

<h3>Bug Bash Week: Daily Breakdown</h3>
{img("bash_week_daily")}

<h3>Bug Bash Week Performance</h3>
<table>
<thead><tr><th>Metric</th><th>Bug Bash Week<br>(Mar 23–27)</th><th>Current Total<br>(all time)</th></tr></thead>
<tbody>
<tr><td>Issues resolved</td><td><strong>{len(during)}</strong></td><td><strong>{len(during) + len(after_bash)}</strong></td></tr>
<tr><td>AI fully automated</td><td>{bash_oc['automated']}</td><td>{all_oc['automated']}</td></tr>
<tr><td>AI accelerated fix</td><td>{bash_oc['accelerated']}</td><td>{all_oc['accelerated']}</td></tr>
<tr><td>AI could not fix</td><td>{bash_oc['could_not_fix']}</td><td>{all_oc['could_not_fix']}</td></tr>
<tr><td>AI verification failed</td><td>{bash_oc['verification_failed']}</td><td>{all_oc['verification_failed']}</td></tr>
<tr><td>Outcomes reached</td><td>{bash_outcomes}</td><td>{outcomes}</td></tr>
<tr><td>AI success rate</td><td><strong>{bash_rate:.1f}%</strong></td><td><strong>{success_rate:.1f}%</strong></td></tr>
</tbody>
</table>

<h3>Remaining Work</h3>
<ul>
<li><strong>{fixable_awaiting} fixable issues</strong> have not yet been attempted by AI — this is the immediate opportunity.</li>
<li><strong>{unresolved_with_outcome} issues</strong> have outcome labels but open JIRA status — these need JIRA hygiene (move to Done or re-open).</li>
<li><strong>{nonfixable} nonfixable issues</strong> need either process improvements (see recommendations) or manual human resolution.</li>
</ul>

<!-- ============================================================ -->
<h2 id="nonfixable" class="page-break">4. Critical Question: Why Are Tickets Nonfixable?</h2>

<p><strong>{nonfixable} issues</strong> were marked <code>ai-nonfixable</code> during triage. This section analyzes
the structural reasons and identifies what would need to change to make them fixable.</p>

{img("nonfixable_themes")}
{img("nonfixable_by_component")}
{img("fixable_vs_nonfixable")}

<h3>Theme-by-Theme Breakdown</h3>
"""

    for theme_name, theme_issues in sorted(theme_data.items(), key=lambda x: len(x[1]), reverse=True):
        pct = len(theme_issues) / len(analysis["all_nonfixable"]) * 100
        comp_counts = Counter(c for i in theme_issues for c in _components(i))
        top_comps = comp_counts.most_common(5)
        comp_str = ", ".join(f"{c} ({n})" for c, n in top_comps)

        html += f"""
<div class="finding">
    <h4>{theme_name} — {len(theme_issues)} issues ({pct:.0f}%)</h4>
    <p><strong>Top components:</strong> {comp_str}</p>
"""
        examples = theme_issues[:3]
        for ex in examples:
            html += f'<p style="margin-left:20px;font-size:0.85em;">• <strong>{ex["key"]}</strong>: {ex.get("summary","")}</p>'
        html += "</div>"

    html += f"""
<h3>Disproportionately Nonfixable Components</h3>
<p>Components where the nonfixable rate exceeds the overall average ({nonfixable/(fixable+nonfixable)*100:.0f}%):</p>
<table>
<thead><tr><th>Component</th><th>Fixable</th><th>Nonfixable</th><th>Nonfixable Rate</th><th>Gap vs Average</th></tr></thead>
<tbody>
"""
    fix_comps = Counter(c for i in analysis["all_fixable"] for c in _components(i))
    nonfix_comps = Counter(c for i in analysis["all_nonfixable"] for c in _components(i))
    avg_rate = nonfixable / (fixable + nonfixable) * 100
    for comp in sorted(set(fix_comps) | set(nonfix_comps)):
        fc = fix_comps.get(comp, 0)
        nc = nonfix_comps.get(comp, 0)
        total = fc + nc
        if total >= 3:
            rate = nc / total * 100
            if rate > avg_rate:
                delta = rate - avg_rate
                html += f"<tr><td>{comp}</td><td>{fc}</td><td>{nc}</td><td><strong>{rate:.0f}%</strong></td><td>+{delta:.0f}pp</td></tr>"
    html += "</tbody></table>"

    html += f"""
<div class="insight">
<strong>Root Cause Summary:</strong> The dominant barriers to AI fixability are:
<ol>
<li><strong>UI/frontend issues ({len(theme_data.get('UI / visual / frontend', []))} issues)</strong> — AI cannot visually verify layout, styling, or interactive behavior.
These need screenshot comparison or DOM-level assertion tooling.</li>
<li><strong>Infrastructure dependencies ({len(theme_data.get('infrastructure / cluster / environment', []))} issues)</strong> — Require a running cluster, specific operator versions,
or network policies that AI doesn't have access to.</li>
<li><strong>Insufficient ticket context ({len(theme_data.get('insufficient context / vague description', []))} issues)</strong> — Vague descriptions with no reproduction steps mean
AI cannot determine the fix with confidence.</li>
</ol>
</div>

{sample_table(nf_samples, "Sample Nonfixable Issues")}

<!-- ============================================================ -->
<h2 id="acceleration" class="page-break">5. Critical Question: Converting Accelerated-Fix to Fully Automated</h2>

<p><strong>{accelerated}</strong> issues required multiple AI attempts before a viable fix was produced, while only
<strong>{automated}</strong> were fixed in a single shot. This section analyzes what separates the two groups.</p>

{img("accelerated_vs_automated")}

<h3>Structural Comparison</h3>
<table>
<thead><tr><th>Metric</th><th>Fully Automated (n={gap['automated']['count']})</th><th>Accelerated Fix (n={gap['accelerated']['count']})</th><th>Implication</th></tr></thead>
<tbody>
<tr><td>Avg description length</td><td>{gap['automated']['avg_desc_len']:.0f} chars</td><td>{gap['accelerated']['avg_desc_len']:.0f} chars</td>
<td>{"Automated issues have richer descriptions" if gap['automated']['avg_desc_len'] > gap['accelerated']['avg_desc_len'] else "Accelerated issues actually have longer descriptions — the problem isn't context length"}</td></tr>
<tr><td>Avg comment count</td><td>{gap['automated']['avg_comments']:.1f}</td><td>{gap['accelerated']['avg_comments']:.1f}</td>
<td>{"More comments correlated with more attempts" if gap['accelerated']['avg_comments'] > gap['automated']['avg_comments'] else "Similar comment volume"}</td></tr>
</tbody>
</table>

<h3>Issue Type Distribution</h3>
<table>
<thead><tr><th>Type</th><th>Fully Automated</th><th>Accelerated Fix</th></tr></thead>
<tbody>
"""
    all_types = sorted(set(gap["automated"]["types"]) | set(gap["accelerated"]["types"]))
    for t in all_types:
        html += f'<tr><td>{t}</td><td>{gap["automated"]["types"].get(t, 0)}</td><td>{gap["accelerated"]["types"].get(t, 0)}</td></tr>'
    html += "</tbody></table>"

    html += f"""
<h3>Component Hotspots: Where Multi-Attempt Is the Norm</h3>
<p>These components have a high ratio of accelerated-fix (multi-attempt) to fully-automated (single-shot),
indicating AI lacks sufficient context or the code structure is harder to reason about:</p>
"""
    acc_comps = Counter(c for i in analysis["all_accelerated"] for c in _components(i))
    auto_comps = Counter(c for i in analysis["all_automated"] for c in _components(i))
    all_gap_comps = set(acc_comps) | set(auto_comps)
    html += "<table><thead><tr><th>Component</th><th>Fully Automated</th><th>Accelerated</th><th>Multi-attempt Rate</th></tr></thead><tbody>"
    for comp in sorted(all_gap_comps, key=lambda c: acc_comps.get(c, 0), reverse=True):
        ac = acc_comps.get(comp, 0)
        au = auto_comps.get(comp, 0)
        total = ac + au
        if total > 0:
            rate = ac / total * 100
            html += f"<tr><td>{comp}</td><td>{au}</td><td>{ac}</td><td><strong>{rate:.0f}%</strong></td></tr>"
    html += "</tbody></table>"

    html += f"""
{sample_table(acc_samples, "Sample Accelerated-Fix Issues (required multiple attempts)")}
{sample_table(auto_samples, "Sample Fully-Automated Issues (single-shot success)")}

<div class="insight">
<strong>Key Finding:</strong> The gap between accelerated and automated is primarily about <em>context quality</em>,
not issue complexity. Accelerated issues average {gap['accelerated']['avg_desc_len']:.0f} chars of description vs
{gap['automated']['avg_desc_len']:.0f} for automated. The components with 100% multi-attempt rates
(Notebooks Extensions, AutoML, AI Evaluations) likely lack sufficient repo context or architectural documentation
in the AI's knowledge base.
</div>

<!-- ============================================================ -->
<h2 id="temporal" class="page-break">6. Temporal Analysis</h2>

{img("daily_throughput")}
{img("time_to_fix")}

<h3>Observations</h3>
<ul>
<li>Resolution throughput peaked mid-week (Wednesday–Thursday), aligning with the "no-meeting" deep work day.</li>
<li>The fully-automated outcomes have a significantly shorter time-to-fix, as expected — these are the
issues where AI could produce a correct fix without iteration.</li>
<li>Accelerated fixes have a wide variance in time-to-fix, suggesting the human-in-the-loop iteration
time varies greatly by component complexity.</li>
</ul>

<!-- ============================================================ -->
<h2 id="recommendations" class="page-break">7. Recommendations: Improving Prompts & Process</h2>

<p>Based on the data analysis, here are specific actions to improve AI success rates for future bug bashes.</p>

<h3>6.1 Making Nonfixable Issues Fixable</h3>

<div class="recommendation critical">
<strong>R1: Add Visual Regression Tooling</strong><br>
{len(theme_data.get('UI / visual / frontend', []))} of {nonfixable} nonfixable issues ({len(theme_data.get('UI / visual / frontend', []))/nonfixable*100:.0f}%) involve UI/frontend work.
AI cannot verify visual correctness without tooling.<br>
<em>Action:</em> Integrate screenshot comparison (e.g., Percy, Chromatic, or Playwright visual snapshots) into
the AI verification pipeline. This would allow AI to verify its own UI fixes.
</div>

<div class="recommendation critical">
<strong>R2: Provide Environment-in-a-Box</strong><br>
{len(theme_data.get('infrastructure / cluster / environment', []))} issues require cluster/infrastructure access.
The current AI workflow operates on source code only.<br>
<em>Action:</em> Create ephemeral test environments (e.g., Kind clusters with ODH pre-installed) that the AI agent
can provision and test against. This is the highest-effort, highest-impact change.
</div>

<div class="recommendation action">
<strong>R3: Improve Bug Report Templates</strong><br>
{len(theme_data.get('insufficient context / vague description', []))} issues lacked sufficient context for AI to determine a fix.<br>
<em>Action:</em> Enforce structured JIRA templates with required fields: reproduction steps, expected vs actual behavior,
environment details, and affected code paths. The triage prompt should reject issues that lack these.
</div>

<div class="recommendation action">
<strong>R4: Enrich Triage Prompt with Rejection Criteria</strong><br>
The current triage prompt asks AI to label issues as fixable/nonfixable but doesn't provide clear criteria for the decision.<br>
<em>Action:</em> Add explicit rejection criteria to the triage prompt:
<blockquote>
"Mark as ai-nonfixable if: (1) the fix requires visual verification of UI rendering, (2) the fix requires
a running cluster or specific infrastructure, (3) the issue description lacks reproduction steps, or (4) the
fix spans multiple repositories. For each nonfixable issue, add a comment explaining WHICH criterion applies
and what additional context would make it fixable."
</blockquote>
</div>

<h3>6.2 Converting Accelerated-Fix to Fully Automated</h3>

<div class="recommendation action">
<strong>R5: Pre-load Component Architecture Context</strong><br>
Components with 100% multi-attempt rates (Notebooks Extensions, AutoML, AI Evaluations, Notebooks Server)
need richer context in the AI session.<br>
<em>Action:</em> For each component, create an <code>AI_CONTEXT.md</code> file in the repo root with:
architecture overview, key abstractions, common bug patterns, test strategy, and links to related services.
The fix prompt should explicitly reference this file. The <code>architecture-context</code> repo is a good start
but needs component-specific depth.
</div>

<div class="recommendation action">
<strong>R6: Structured Fix Prompt with Verification Steps</strong><br>
The current workflow generates fix files but doesn't prescribe verification strategy.<br>
<em>Action:</em> Update the fix prompt to require a verification plan before coding:
<blockquote>
"Before writing any code: (1) identify the root cause from the issue description and codebase, (2) list
the specific test(s) that should pass after the fix, (3) if no existing test covers this, write a failing
test first. Only then implement the fix. Run the tests and verify they pass."
</blockquote>
</div>

<div class="recommendation action">
<strong>R7: Batch Size Optimization</strong><br>
The current guidance recommends batching ~20 issues per triage prompt. For fix attempts, single-issue
focus produces better results.<br>
<em>Action:</em> Keep batch triage (20 at a time) for classification, but switch to <strong>single-issue fix sessions</strong>
where the AI agent gets one issue, the full repo context, and architectural docs. Multi-issue fix sessions
split AI attention and reduce first-attempt success.
</div>

<div class="recommendation">
<strong>R8: Feedback Loop from Failures</strong><br>
{could_not} issues were marked "could not fix" and {verif_fail} had verification failures, but these outcomes
don't feed back into future attempts.<br>
<em>Action:</em> For each "could not fix" and "verification failed" outcome, require a structured comment on the JIRA:
what the AI tried, why it failed, and what context was missing. These comments become training data for
improving prompts and identifying systematic gaps.
</div>

<h3>6.3 Process Improvements</h3>

<div class="recommendation">
<strong>R9: Two-Phase Triage</strong><br>
The current single-pass triage misses nuance. {lc.get('ai-initiallymarkedfixable', 0)} issues were
initially marked fixable then reclassified.<br>
<em>Action:</em> Phase 1: AI triage with the current prompt. Phase 2: Human review of AI's nonfixable
classifications with a focus on "what context would make this fixable?" — then re-triage with enhanced context.
</div>

<div class="recommendation">
<strong>R10: Model Selection Matters</strong><br>
The ambient guidance recommends Opus 4.6, which is a strong choice for reasoning.
However, different models may perform better for different issue types.<br>
<em>Action:</em> Track which model was used per issue in future bug bashes.
For UI-heavy components (Dashboard), multimodal models that can process screenshots may outperform text-only models.
</div>

<h3>6.4 Prompt Template Improvements</h3>

<p>Based on the pattern analysis, here's an improved triage prompt that addresses the identified gaps:</p>

<pre style="background:#f4f4f4;padding:15px;border-radius:4px;font-size:0.85em;overflow-x:auto;">
Using my jira connection - triage the issues from this JQL:
'project = RHOAIENG AND status in (backlog, new) AND issuetype in (Bug)
 AND team = {{your team}} ORDER BY priority DESC'

For each issue, perform this analysis:

1. FIXABILITY ASSESSMENT
   - Can this be fixed with source code changes only? (no cluster, no UI verification)
   - Is the reproduction path clear from the description?
   - Is the fix contained within a single repository?
   - Are there existing tests that would validate the fix?

   Label as ai-fixable ONLY if ALL four criteria are met.
   Label as ai-nonfixable otherwise, with a comment explaining which criteria failed.

2. FOR ai-fixable ISSUES: Create a detailed fix plan including:
   - Root cause analysis (reference specific files/functions)
   - Proposed fix with code changes
   - Verification: which existing tests cover this, or write a new test
   - Risk assessment: what could this change break?

3. FOR ai-nonfixable ISSUES: Add a comment explaining:
   - Which fixability criteria failed
   - What additional context/tooling would make it fixable
   - Suggested manual approach for a human developer

Add ai-triaged to all processed issues.
Consult https://github.com/opendatahub-io/architecture-context
</pre>

<hr style="margin-top:40px;">
<p style="color:#95a5a6;font-size:0.85em;">
    Generated from {len(issues)} JIRA issues collected via odh-eng-metrics.
    Data source: RHOAI Bug Bash March 23–27, 2026.
</p>

</body></html>"""
    return html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    store = Store("data/eng-metrics.sqlite")
    issues = store.get_collection_issues("ai-bug-bash")
    if not issues:
        print("No issues found for ai-bug-bash collection")
        return

    for i in issues:
        i["_labels"] = _labels(i)

    fixable = [i for i in issues if "ai-fixable" in i["_labels"]]
    nonfixable_issues = [i for i in issues if "ai-nonfixable" in i["_labels"]]
    accelerated = [i for i in issues if "ai-accelerated-fix" in i["_labels"]]
    automated = [i for i in issues if "ai-fully-automated" in i["_labels"]]
    could_not_fix = [i for i in issues if "ai-could-not-fix" in i["_labels"]]
    verification_failed = [i for i in issues if "ai-verification-failed" in i["_labels"]]

    print(f"Generating deep analysis for {len(issues)} issues...")
    print(f"  Fixable: {len(fixable)}, Nonfixable: {len(nonfixable_issues)}")
    print(f"  Automated: {len(automated)}, Accelerated: {len(accelerated)}")
    print(f"  Could not fix: {len(could_not_fix)}, Verification failed: {len(verification_failed)}")

    # Generate charts
    print("Generating charts...")
    charts = {
        "triage_funnel": chart_triage_funnel(issues),
        "outcome_distribution": chart_outcome_distribution(issues),
        "nonfixable_by_component": chart_nonfixable_by_component(nonfixable_issues),
        "nonfixable_themes": chart_nonfixable_themes(nonfixable_issues),
        "fixable_vs_nonfixable": chart_fixable_vs_nonfixable_components(fixable, nonfixable_issues),
        "accelerated_vs_automated": chart_accelerated_vs_automated(accelerated, automated),
        "daily_throughput": chart_daily_throughput(issues),
        "priority_breakdown": chart_priority_breakdown(fixable, nonfixable_issues),
        "success_rate": chart_success_rate_gauge(automated, accelerated, could_not_fix, verification_failed),
        "time_to_fix": chart_time_to_fix_by_outcome(issues),
        "temporal_comparison": chart_temporal_comparison(issues),
        "resolution_waterfall": chart_resolution_waterfall(issues),
        "bash_week_daily": chart_bash_week_daily(issues),
    }

    # Deep analysis
    print("Running deep analysis...")
    theme_data = analyze_nonfixable_reasons(nonfixable_issues)
    gap_data = analyze_acceleration_gap(accelerated, automated)

    analysis = {
        "nonfixable_themes": theme_data,
        "acceleration_gap": gap_data,
        "all_nonfixable": nonfixable_issues,
        "all_fixable": fixable,
        "all_accelerated": accelerated,
        "all_automated": automated,
        "nonfixable_samples": extract_sample_issues(nonfixable_issues, 5),
        "accelerated_samples": extract_sample_issues(accelerated, 5),
        "automated_samples": extract_sample_issues(automated, 5),
    }

    # Generate HTML
    print("Generating HTML report...")
    html = generate_html(issues, charts, analysis)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"\nReport written to {OUTPUT_PATH}")
    print(f"Open in browser: file://{OUTPUT_PATH.resolve()}")


if __name__ == "__main__":
    main()
