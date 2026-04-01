#!/usr/bin/env python3
"""Generate a deep-analysis HTML report on the AI First Bug Bash with embedded charts."""

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
from matplotlib.patches import Patch

from store.db import Store

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

OUTPUT_PATH = Path("data/bug-bash-deep-analysis.html")
BUG_BASH_LABELS = [
    "ai-triaged", "ai-fixable", "ai-nonfixable",
    "ai-fully-automated", "ai-accelerated-fix",
    "ai-could-not-fix", "ai-verification-failed", "regressions-found",
]
BUG_BASH_LABELS_DISPLAY = ", ".join(f"<code>{l}</code>" for l in BUG_BASH_LABELS)
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

def chart_triage_funnel(issues, baseline_total=None):
    cls_counts = Counter(_classify_issue(i) for i in issues)
    fixable = cls_counts["fixable_pending"] + cls_counts["automated"] + cls_counts["accelerated"] + cls_counts["could_not_fix"] + cls_counts["verif_failed"]
    nonfixable = cls_counts["nonfixable"]
    new_count = sum(1 for i in issues
                    if (i.get("created") or "")[:10] > BUG_BASH_START)
    from_backlog = len(issues) - new_count

    fig, ax = plt.subplots(figsize=(8, 5.5))
    bars = []
    values = []
    colors = []

    if baseline_total is not None:
        bars.append("Bug Backlog\n(as of Mar 22)")
        values.append(baseline_total)
        colors.append("#1a252f")

    bars.append(f"AI Labelled\n({from_backlog} backlog + {new_count} new)")
    values.append(len(issues))
    colors.append("#8e44ad")

    bars += ["Fixable", "Nonfixable"]
    values += [fixable, nonfixable]
    colors += ["#2ecc71", "#e74c3c"]

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


def _chart_nonfixable_by_component_single(issues, title):
    """Render a single nonfixable-by-component horizontal bar chart."""
    comp_counts = Counter()
    for i in issues:
        for c in _components(i):
            comp_counts[c] += 1
    top = comp_counts.most_common(12)
    if not top:
        return None
    names, counts = zip(*reversed(top))
    fig, ax = plt.subplots(figsize=(9, 5))
    bars = ax.barh(names, counts, color="#e74c3c", alpha=0.85, height=0.6)
    ax.bar_label(bars, padding=4, fontsize=10)
    ax.set_xlim(0, max(counts) * 1.2)
    ax.set_title(title, fontsize=13, fontweight="bold", pad=10)
    return _fig_to_base64(fig)


def chart_nonfixable_by_component(nonfixable_issues):
    """Return dict: {total: b64, PROJ: b64, ...}."""
    by_proj: dict[str, list] = defaultdict(list)
    for i in nonfixable_issues:
        by_proj[_project_of(i)].append(i)

    result = {"total": _chart_nonfixable_by_component_single(
        nonfixable_issues, "Nonfixable Issues by Component — All Projects")}
    for proj in sorted(by_proj):
        result[proj] = _chart_nonfixable_by_component_single(
            by_proj[proj], f"Nonfixable Issues by Component — {proj}")
    return result


def _chart_nonfixable_themes_single(issues, title):
    """Render a single nonfixable-themes horizontal bar chart."""
    theme_counts = {}
    for theme_name, patterns in NONFIXABLE_THEMES.items():
        count = sum(1 for i in issues if any(re.search(p, _text_blob(i)) for p in patterns))
        if count > 0:
            theme_counts[theme_name] = count

    sorted_themes = sorted(theme_counts.items(), key=lambda x: x[1], reverse=True)
    if not sorted_themes:
        return None
    names, counts = zip(*sorted_themes)
    total = len(issues)
    pcts = [c / total * 100 for c in counts]

    fig, ax = plt.subplots(figsize=(10, 5))
    bars = ax.barh(list(reversed(names)), list(reversed(pcts)), color="#c0392b", alpha=0.8, height=0.6)
    ax.bar_label(bars, labels=[f"{p:.0f}% ({c})" for p, c in zip(reversed(pcts), reversed(counts))],
                 padding=5, fontsize=10)
    ax.set_xlim(0, max(pcts) * 1.25)
    ax.set_xlabel("% of nonfixable issues")
    ax.set_title(title, fontsize=13, fontweight="bold", pad=10)
    return _fig_to_base64(fig)


def chart_nonfixable_themes(nonfixable_issues):
    """Return dict: {total: b64, PROJ: b64, ...}."""
    by_proj: dict[str, list] = defaultdict(list)
    for i in nonfixable_issues:
        by_proj[_project_of(i)].append(i)

    result = {"total": _chart_nonfixable_themes_single(
        nonfixable_issues, "Why Are Tickets Nonfixable? — All Projects")}
    for proj in sorted(by_proj):
        result[proj] = _chart_nonfixable_themes_single(
            by_proj[proj], f"Why Are Tickets Nonfixable? — {proj}")
    return result


def _chart_fixable_vs_nonfixable_single(fixable, nonfixable, title, min_total=5):
    """Render a single fixable-vs-nonfixable horizontal bar chart."""
    fix_comps = Counter(c for i in fixable for c in _components(i))
    nonfix_comps = Counter(c for i in nonfixable for c in _components(i))
    all_comps = set(fix_comps) | set(nonfix_comps)
    data = []
    for comp in all_comps:
        fc = fix_comps.get(comp, 0)
        nc = nonfix_comps.get(comp, 0)
        total = fc + nc
        if total >= min_total:
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
    ax.set_title(title, fontsize=13, fontweight="bold", pad=10)

    for idx, d in enumerate(reversed(top)):
        ax.annotate(f"{d[3]:.0f}% nonfixable", xy=(max(d[1], d[2]) + 1, idx + 0.2),
                     fontsize=9, color="#7f8c8d", va="center")
    return _fig_to_base64(fig)


def chart_fixable_vs_nonfixable_components(fixable, nonfixable):
    """Return dict: {total: b64, PROJ: b64, ...}."""
    by_proj_fix: dict[str, list] = defaultdict(list)
    by_proj_nf: dict[str, list] = defaultdict(list)
    for i in fixable:
        by_proj_fix[_project_of(i)].append(i)
    for i in nonfixable:
        by_proj_nf[_project_of(i)].append(i)

    projects = sorted(set(by_proj_fix) | set(by_proj_nf))
    result = {"total": _chart_fixable_vs_nonfixable_single(
        fixable, nonfixable, "Fixable vs Nonfixable by Component — All Projects")}
    for proj in projects:
        result[proj] = _chart_fixable_vs_nonfixable_single(
            by_proj_fix.get(proj, []), by_proj_nf.get(proj, []),
            f"Fixable vs Nonfixable by Component — {proj}", min_total=2)
    return result


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


def chart_issue_pipeline(issues):
    """Where are all issues in the pipeline today?"""
    cls = Counter(_classify_issue(i) for i in issues)
    stages = [
        ("Fully Automated", cls["automated"], "#2ecc71"),
        ("Accelerated Fix", cls["accelerated"], "#3498db"),
        ("Could Not Fix", cls["could_not_fix"], "#e74c3c"),
        ("Verif Failed", cls["verif_failed"], "#e67e22"),
        ("Fixable (pending)", cls["fixable_pending"], "#f1c40f"),
        ("Nonfixable", cls["nonfixable"], "#c0392b"),
        ("Triaged Only", cls["triaged_only"], "#95a5a6"),
        ("Untriaged", cls["untriaged"], "#bdc3c7"),
    ]
    stages = [(s, v, c) for s, v, c in stages if v > 0]

    fig, ax = plt.subplots(figsize=(10, max(3, len(stages) * 0.7)))
    labels = [s[0] for s in stages]
    values = [s[1] for s in stages]
    colors = [s[2] for s in stages]
    bars = ax.barh(labels, values, color=colors, height=0.6)
    ax.bar_label(bars, padding=5, fontsize=11, fontweight="bold",
                 labels=[f"{v}  ({v/len(issues)*100:.0f}%)" for v in values])
    ax.set_xlim(0, max(values) * 1.4)
    ax.invert_yaxis()
    ax.set_title(f"Issue Pipeline: Where Are the {len(issues)} Issues Today?",
                 fontsize=13, fontweight="bold", pad=10)
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


def chart_automation_rate(fixable_issues, automated_issues, accelerated_issues,
                          could_not_fix_issues, verification_failed_issues):
    n_fixable = len(fixable_issues)
    n_auto = len(automated_issues)
    n_accel = len(accelerated_issues)
    n_cnf = len(could_not_fix_issues)
    n_vf = len(verification_failed_issues)
    n_pending = n_fixable - n_auto - n_accel - n_cnf - n_vf
    rate = n_auto / n_fixable * 100 if n_fixable > 0 else 0

    fig, ax = plt.subplots(figsize=(8, 3.5))
    categories = ["Fully Automated", "Accelerated Fix", "Could Not Fix",
                   "Verif Failed", "Pending"]
    values = [n_auto, n_accel, n_cnf, n_vf, max(n_pending, 0)]
    colors = ["#2ecc71", "#3498db", "#e74c3c", "#e67e22", "#95a5a6"]
    bars = ax.barh(categories, values, color=colors, height=0.55)
    ax.bar_label(bars, padding=5, fontsize=11, fontweight="bold")
    ax.set_xlim(0, max(values) * 1.3 if max(values) > 0 else 1)
    ax.set_title(f"Fixable Issue Outcomes ({n_fixable} fixable, {rate:.1f}% fully automated)",
                 fontsize=13, fontweight="bold", pad=10)
    ax.invert_yaxis()
    return _fig_to_base64(fig)


BUG_BASH_START = "2026-03-22"
BUG_BASH_END = "2026-03-29"


def _split_by_period(issues):
    """Split issues into bug-bash-week, before-bash, after-bash, and unresolved."""
    during = []
    before = []
    after = []
    unresolved = []
    for i in issues:
        resolved = i.get("resolved")
        if not resolved:
            unresolved.append(i)
        elif BUG_BASH_START <= resolved[:10] <= BUG_BASH_END:
            during.append(i)
        elif resolved[:10] < BUG_BASH_START:
            before.append(i)
        else:
            after.append(i)
    return during, before, after, unresolved


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
    during, _before, after, unresolved = _split_by_period(issues)
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
    bash_fixable = bash_oc.get("fixable", 0) or (bash_oc["automated"] + bash_oc["accelerated"] + bash_oc["could_not_fix"] + bash_oc["verification_failed"])
    bash_auto_rate = bash_oc["automated"] / bash_fixable * 100 if bash_fixable else 0
    axes[0].set_title(f"Bug Bash Week (Mar 22\u201329)\n{bash_oc['automated']}/{bash_fixable} fixable automated ({bash_auto_rate:.0f}%)", fontsize=12, fontweight="bold")

    axes[1].bar(categories, today_vals, color=colors_today, width=0.6)
    axes[1].bar_label(axes[1].containers[0], fontsize=12, fontweight="bold", padding=3)
    today_fixable = today_oc.get("fixable", 0) or (today_oc["automated"] + today_oc["accelerated"] + today_oc["could_not_fix"] + today_oc["verification_failed"])
    today_auto_rate = today_oc["automated"] / today_fixable * 100 if today_fixable else 0
    axes[1].set_title(f"Current State (Today)\n{today_oc['automated']}/{today_fixable} fixable automated ({today_auto_rate:.0f}%)", fontsize=12, fontweight="bold")

    fig.suptitle("Outcomes: Bug Bash Week vs Current State", fontsize=14, fontweight="bold", y=1.02)
    fig.tight_layout()
    return _fig_to_base64(fig)


def chart_resolution_waterfall(issues):
    """Stacked bar showing how issues moved through the pipeline over time."""
    during, before, after, unresolved = _split_by_period(issues)

    # For unresolved: count those with outcome labels vs those still pending
    unresolved_with_outcome = [i for i in unresolved if any(
        l in _labels(i) for l in OUTCOME_LABELS)]
    unresolved_pending = [i for i in unresolved if not any(
        l in _labels(i) for l in OUTCOME_LABELS)]
    unresolved_fixable_pending = [i for i in unresolved_pending if "ai-fixable" in _labels(i)]
    unresolved_nonfixable = [i for i in unresolved_pending if "ai-nonfixable" in _labels(i)]

    fig, ax = plt.subplots(figsize=(10, 5.5))
    categories = [
        f"Resolved during\nbug bash\n(Mar 22–29)",
        f"Resolved after\nbug bash\n(Mar 30+)",
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
    ax.set_xlim(0, max(values) * 1.2 if max(values) > 0 else 1)
    total = len(issues)
    ax.set_title(f"Resolution Waterfall ({total} issues)", fontsize=13, fontweight="bold", pad=10)
    ax.invert_yaxis()
    return _fig_to_base64(fig)


def chart_bash_week_daily(issues):
    """Daily breakdown during the bug bash week only."""
    bash_days = ["2026-03-22", "2026-03-23", "2026-03-24", "2026-03-25", "2026-03-26", "2026-03-27", "2026-03-28", "2026-03-29"]
    day_labels = ["Sun 22", "Mon 23", "Tue 24", "Wed 25", "Thu 26", "Fri 27", "Sat 28", "Sun 29"]

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


def _project_of(issue: dict) -> str:
    key = issue.get("key", "")
    return key.rsplit("-", 1)[0] if "-" in key else "Unknown"


def chart_project_breakdown(issues, baseline_by_proj=None):
    """Stacked horizontal bar: per-project counts by outcome category.

    When baseline_by_proj is provided, a light background bar shows the full
    backlog size for each project, with the stacked outcome bars overlaid.
    The annotation distinguishes issues from the pre-existing backlog vs new
    bugs filed during the bash.
    """
    by_proj: dict[str, Counter] = defaultdict(Counter)
    new_by_proj: dict[str, int] = Counter()
    for i in issues:
        proj = _project_of(i)
        labels = _labels(i)
        created = (i.get("created") or "")[:10]
        if created > BUG_BASH_START:
            new_by_proj[proj] += 1
        if "ai-nonfixable" in labels:
            by_proj[proj]["Nonfixable"] += 1
        elif "ai-fully-automated" in labels:
            by_proj[proj]["Fully Automated"] += 1
        elif "ai-accelerated-fix" in labels:
            by_proj[proj]["Accelerated Fix"] += 1
        elif "ai-could-not-fix" in labels:
            by_proj[proj]["Could Not Fix"] += 1
        elif "ai-verification-failed" in labels:
            by_proj[proj]["Verification Failed"] += 1
        elif "ai-fixable" in labels:
            by_proj[proj]["Awaiting Outcome"] += 1
        else:
            by_proj[proj]["Untriaged"] += 1

    if not by_proj:
        return None

    if baseline_by_proj is None:
        baseline_by_proj = {}

    projects = sorted(by_proj.keys(), key=lambda p: -sum(by_proj[p].values()))
    categories = [
        "Fully Automated", "Accelerated Fix", "Awaiting Outcome",
        "Could Not Fix", "Verification Failed", "Nonfixable", "Untriaged",
    ]
    cat_colors = {
        "Fully Automated": "#2ecc71", "Accelerated Fix": "#3498db",
        "Awaiting Outcome": "#f1c40f", "Could Not Fix": "#e74c3c",
        "Verification Failed": "#e67e22", "Nonfixable": "#c0392b",
        "Untriaged": "#95a5a6",
    }

    fig, ax = plt.subplots(figsize=(10, max(3, len(projects) * 0.8)))
    y_pos = list(range(len(projects)))

    if baseline_by_proj:
        baseline_vals = [baseline_by_proj.get(p, 0) for p in projects]
        ax.barh(y_pos, baseline_vals, color="#dfe6e9", height=0.7,
                label="Total Backlog (pre-bash)", edgecolor="#b2bec3", linewidth=0.8, zorder=1)

    left = [0] * len(projects)
    for cat in categories:
        widths = [by_proj[p].get(cat, 0) for p in projects]
        if not any(widths):
            continue
        ax.barh(y_pos, widths, left=left, color=cat_colors[cat], label=cat,
                edgecolor="white", linewidth=0.5, height=0.55, zorder=2)
        left = [l + w for l, w in zip(left, widths)]

    for j, p in enumerate(projects):
        triaged = sum(by_proj[p].values())
        bl = baseline_by_proj.get(p)
        new_count = new_by_proj.get(p, 0)
        pre_bash = triaged - new_count
        if bl:
            parts = [f"{pre_bash} of {bl} backlog"]
            if new_count:
                parts.append(f"+{new_count} new")
            ax.text(max(triaged, bl) + 2, j, " | ".join(parts),
                    va="center", fontsize=8, color="#555", zorder=3)

    ax.set_yticks(y_pos)
    ax.set_yticklabels(projects)
    ax.set_xlabel("Issues")
    ax.set_title("Bug Bash Breakdown by Project", fontsize=13, fontweight="bold", pad=10)
    ax.legend(loc="lower right", fontsize=8, framealpha=0.9)
    ax.invert_yaxis()
    return _fig_to_base64(fig)


def chart_project_automation_rate(issues):
    """Horizontal bar per project: automated / fixable = automation rate."""
    by_proj: dict[str, Counter] = defaultdict(Counter)
    for i in issues:
        by_proj[_project_of(i)][_classify_issue(i)] += 1

    if not by_proj:
        return None

    projects = sorted(by_proj.keys(), key=lambda p: -sum(by_proj[p].values()))
    fixable_counts = []
    automated_counts = []
    rates = []
    for p in projects:
        pc = by_proj[p]
        n_auto = pc["automated"]
        n_fixable = n_auto + pc["accelerated"] + pc["could_not_fix"] + pc["verif_failed"] + pc["fixable_pending"]
        fixable_counts.append(n_fixable)
        automated_counts.append(n_auto)
        rates.append(round(n_auto / n_fixable * 100, 1) if n_fixable else 0)

    fig, ax = plt.subplots(figsize=(10, max(3, len(projects) * 1.0)))
    y_pos = range(len(projects))
    bar_colors = ["#2ecc71" if r >= 10 else "#f39c12" if r >= 5 else "#e74c3c" for r in rates]
    bars = ax.barh(y_pos, rates, color=bar_colors, height=0.55, edgecolor="white", linewidth=0.5)

    ax.set_yticks(y_pos)
    ax.set_yticklabels(projects, fontsize=11)
    ax.set_xlabel("Automation Rate (fully automated / fixable) %")
    ax.set_xlim(0, max(rates) * 1.5 + 5 if rates else 10)
    ax.invert_yaxis()

    for j, (p, r) in enumerate(zip(projects, rates)):
        label = f" {r:.1f}%  ({automated_counts[j]}/{fixable_counts[j]} fixable)"
        ax.text(r + 0.3, j, label, va="center", fontsize=9, fontweight="bold")

    ax.set_title("Automation Rate by Project (fully automated / fixable)", fontsize=13, fontweight="bold", pad=10)
    return _fig_to_base64(fig)


def chart_project_dashboard(proj_issues: list[dict], project_name: str,
                            baseline_count: int | None = None) -> str | None:
    """Three-panel mini dashboard for a single project's issues."""
    if not proj_issues:
        return None

    pc = Counter(_classify_issue(i) for i in proj_issues)
    total = len(proj_issues)
    new_count = sum(1 for i in proj_issues
                    if (i.get("created") or "")[:10] > BUG_BASH_START)
    from_backlog = total - new_count
    p_auto = pc["automated"]
    p_accel = pc["accelerated"]
    p_cnf = pc["could_not_fix"]
    p_vf = pc["verif_failed"]
    p_fixable_pending = pc["fixable_pending"]
    p_nonfixable = pc["nonfixable"]
    p_triaged_only = pc["triaged_only"]
    p_untriaged = pc["untriaged"]
    p_fixable = p_auto + p_accel + p_cnf + p_vf + p_fixable_pending
    p_triaged = p_fixable + p_nonfixable + p_triaged_only
    auto_rate = p_auto / p_fixable * 100 if p_fixable > 0 else 0

    fig, axes = plt.subplots(1, 3, figsize=(16, 4.5))
    fig.suptitle(project_name, fontsize=15, fontweight="bold", y=1.02)

    # Panel 1: Triage funnel
    ax = axes[0]
    bars = []
    values = []
    colors = []
    if baseline_count is not None:
        bars.append("Bug Backlog")
        values.append(baseline_count)
        colors.append("#1a252f")

    ai_label = f"AI Labelled\n({from_backlog} backlog"
    if new_count:
        ai_label += f" + {new_count} new"
    ai_label += ")"
    bars.append(ai_label)
    values.append(total)
    colors.append("#8e44ad")

    bars += ["Fixable", "Nonfixable"]
    values += [p_fixable, p_nonfixable]
    colors += ["#2ecc71", "#e74c3c"]
    b = ax.barh(bars, values, color=colors, height=0.6)
    ax.bar_label(b, padding=4, fontsize=10, fontweight="bold")
    ax.set_xlim(0, max(values) * 1.2 if max(values) > 0 else 1)
    ax.set_title("Triage Funnel", fontsize=12, fontweight="bold")
    ax.invert_yaxis()

    # Panel 2: Outcome pie
    ax = axes[1]
    outcome_data = [
        ("Fully Automated", p_auto, "#2ecc71"),
        ("Accelerated Fix", p_accel, "#3498db"),
        ("Could Not Fix", p_cnf, "#e74c3c"),
        ("Verif Failed", p_vf, "#e67e22"),
        ("Pending", p_fixable_pending, "#95a5a6"),
    ]
    pie_labels = [d[0] for d in outcome_data if d[1] > 0]
    pie_values = [d[1] for d in outcome_data if d[1] > 0]
    pie_colors = [d[2] for d in outcome_data if d[1] > 0]

    if pie_values:
        wedges, texts, autotexts = ax.pie(
            pie_values, labels=pie_labels, colors=pie_colors,
            autopct="%1.0f%%", startangle=90, textprops={"fontsize": 9},
        )
        for at in autotexts:
            at.set_fontweight("bold")
    else:
        ax.text(0.5, 0.5, "No fixable issues", ha="center", va="center",
                fontsize=11, color="#7f8c8d", transform=ax.transAxes)
    ax.set_title(f"Fixable Breakdown ({p_fixable})", fontsize=12, fontweight="bold")

    # Panel 3: Automation rate
    ax = axes[2]
    if p_fixable > 0:
        ax.barh(["Automated", "Not Automated"], [p_auto, p_fixable - p_auto],
                color=["#2ecc71", "#bdc3c7"], height=0.5)
        ax.bar_label(ax.containers[0], padding=5, fontsize=11, fontweight="bold")
        ax.set_xlim(0, p_fixable * 1.3)
        ax.set_title(f"Automation Rate: {auto_rate:.1f}%\n({p_auto}/{p_fixable} fixable)",
                     fontsize=12, fontweight="bold")
    else:
        ax.text(0.5, 0.5, "No fixable issues", ha="center", va="center",
                fontsize=11, color="#7f8c8d", transform=ax.transAxes)
        ax.set_title("Automation Rate", fontsize=12, fontweight="bold")
        ax.set_xlim(0, 1)

    fig.tight_layout()
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
# AI Bug Automation Readiness Charts
# ---------------------------------------------------------------------------

# Key checks from ai-bug-automation-readiness (most impactful for bug-fixing outcomes)
HIGHLIGHT_ATTRIBUTES = [
    "test_ratio", "one_command_test", "ci_pr_tests", "coverage_config",
    "test_isolation", "ai_context_files", "bug_report_template",
    "code_navigability", "build_setup", "type_safety",
    "contributing_guide", "lint_in_ci",
]


def _repo_name(url: str) -> str:
    """Extract 'org/repo' from a GitHub URL."""
    parts = url.rstrip("/").split("/")
    if len(parts) >= 2:
        return f"{parts[-2]}/{parts[-1]}"
    return url


def chart_agentready_vs_outcomes(ar_data: list[dict], bug_bash_by_proj: dict) -> str | None:
    """Horizontal bar: readiness score per repository, coloured by project."""
    if not ar_data:
        return None

    seen: dict[str, dict] = {}
    for row in ar_data:
        repo = _repo_name(row["repo_url"])
        if repo not in seen or row["overall_score"] > seen[repo]["score"]:
            seen[repo] = {"score": row["overall_score"], "project": row["project"],
                          "level": row["certification_level"]}

    repos = sorted(seen.keys(), key=lambda r: -seen[r]["score"])
    scores = [seen[r]["score"] for r in repos]
    projects = [seen[r]["project"] for r in repos]
    levels = [seen[r]["level"] for r in repos]

    proj_colors = {}
    palette = ["#2980b9", "#e74c3c", "#2ecc71", "#e67e22", "#8e44ad", "#1abc9c"]
    for i, p in enumerate(sorted(set(projects))):
        proj_colors[p] = palette[i % len(palette)]
    bar_colors = [proj_colors[p] for p in projects]

    fig, ax = plt.subplots(figsize=(10, max(4, len(repos) * 0.55)))
    y_pos = range(len(repos))
    bars = ax.barh(y_pos, scores, color=bar_colors, height=0.6, edgecolor="white", linewidth=0.5)

    for j, (repo, score, proj, level) in enumerate(zip(repos, scores, projects, levels)):
        ax.text(score + 0.8, j, f" {score:.0f} ({level}) [{proj}]",
                va="center", fontsize=8, fontweight="bold")

    ax.set_yticks(list(y_pos))
    ax.set_yticklabels(repos, fontsize=9)
    ax.set_xlim(0, 110)
    ax.set_xlabel("Readiness Score (0-100)")
    ax.invert_yaxis()

    for proj, color in proj_colors.items():
        ax.barh([], [], color=color, label=proj)
    ax.legend(loc="lower right", fontsize=8, framealpha=0.9, title="JIRA Project")

    ax.set_title("Bug Automation Readiness Score by Repository", fontsize=13, fontweight="bold", pad=10)
    return _fig_to_base64(fig)


def chart_agentready_attributes(ar_data: list[dict]) -> str | None:
    """Horizontal bar chart: key attribute scores across projects."""
    if not ar_data:
        return None

    proj_scores: dict[str, dict[str, float | None]] = {}
    for row in ar_data:
        findings = json.loads(row.get("findings_json") or "[]")
        attr_map = {}
        for f in findings:
            attr_id = f.get("attribute", {}).get("id", "")
            attr_map[attr_id] = f.get("score")
        proj_scores[row["project"]] = attr_map

    attrs_to_show = [a for a in HIGHLIGHT_ATTRIBUTES
                     if any(ps.get(a) is not None for ps in proj_scores.values())]
    if not attrs_to_show:
        return None

    proj_colors = {"RHOAIENG": "#e74c3c", "AIPCC": "#e67e22", "RHAIENG": "#9b59b6", "INFERENG": "#3498db"}
    projects = sorted(proj_scores.keys())

    fig, ax = plt.subplots(figsize=(10, max(4, len(attrs_to_show) * 0.6)))
    y = range(len(attrs_to_show))
    bar_h = 0.8 / max(len(projects), 1)

    for pi, proj in enumerate(projects):
        vals = [proj_scores[proj].get(a) or 0 for a in attrs_to_show]
        positions = [yi + pi * bar_h - (len(projects) - 1) * bar_h / 2 for yi in y]
        ax.barh(positions, vals, height=bar_h, color=proj_colors.get(proj, "#95a5a6"),
                label=proj, alpha=0.85)

    labels = [a.replace("_", " ").title() for a in attrs_to_show]
    ax.set_yticks(list(y))
    ax.set_yticklabels(labels, fontsize=9)
    ax.set_xlim(0, 105)
    ax.set_xlabel("Attribute Score (0-100)")
    ax.legend(fontsize=9, loc="lower right")
    ax.set_title("Bug Automation Readiness — Key Checks by Project", fontsize=13, fontweight="bold", pad=10)
    return _fig_to_base64(fig)


# ---------------------------------------------------------------------------
# HTML Report
# ---------------------------------------------------------------------------

def _classify_issue(issue: dict) -> str:
    """Classify a single issue into one outcome bucket (unique-issue counting).

    Priority order: success labels beat failure labels for issues with both,
    since the latest state is what matters.
    """
    lbls = set(_labels(issue))
    if "ai-fully-automated" in lbls:
        return "automated"
    if "ai-accelerated-fix" in lbls:
        return "accelerated"
    if "ai-could-not-fix" in lbls:
        return "could_not_fix"
    if "ai-verification-failed" in lbls:
        return "verif_failed"
    if "ai-nonfixable" in lbls:
        return "nonfixable"
    if "ai-fixable" in lbls:
        return "fixable_pending"
    if "ai-triaged" in lbls:
        return "triaged_only"
    return "untriaged"


def generate_html(issues, charts, analysis, non_bugs=0, baseline_total=None, baseline_by_proj=None, pre_bash_excluded=0):
    # Count unique issues per classification (no double-counting)
    cls_counts = Counter(_classify_issue(i) for i in issues)
    automated = cls_counts["automated"]
    accelerated = cls_counts["accelerated"]
    could_not = cls_counts["could_not_fix"]
    verif_fail = cls_counts["verif_failed"]
    regressions = sum(1 for i in issues if "regressions-found" in _labels(i))
    nonfixable = cls_counts["nonfixable"]
    fixable = cls_counts["fixable_pending"] + automated + accelerated + could_not + verif_fail
    pending_classification = cls_counts["triaged_only"]
    triaged = fixable + nonfixable + pending_classification
    automation_rate = automated / fixable * 100 if fixable > 0 else 0
    resolved = sum(1 for i in issues if i.get("resolved"))
    open_count = len(issues) - resolved

    # Edge case counts for tooltips
    raw_nf_label = sum(1 for i in issues if "ai-nonfixable" in _labels(i))
    raw_triaged_label = sum(1 for i in issues if "ai-triaged" in _labels(i))
    missing_triaged = len(issues) - raw_triaged_label
    conflicting_nf = raw_nf_label - nonfixable

    # Breakdown: how our "Triaged by AI" total relates to a JIRA ai-triaged filter
    open_statuses = {"New", "Backlog", "Refinement", "To Do"}
    triaged_in_open = sum(1 for i in issues
                          if "ai-triaged" in _labels(i) and i.get("status") in open_statuses)
    triaged_not_open = raw_triaged_label - triaged_in_open
    bb_label_set = set(BUG_BASH_LABELS)
    no_triaged_has_bb = [i for i in issues if "ai-triaged" not in _labels(i)
                         and any(l in bb_label_set for l in _labels(i))]

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

    # Baseline: use JQL-sourced total if available, otherwise fall back to label-based count
    has_baseline = baseline_total is not None
    total_display = baseline_total if has_baseline else len(issues)
    total_new = sum(1 for i in issues if (i.get("created") or "")[:10] > BUG_BASH_START)
    from_backlog_count = len(issues) - total_new
    backlog_coverage_pct = from_backlog_count / total_display * 100 if total_display else 0
    triage_pct = len(issues) / total_display * 100 if total_display else 0
    if baseline_by_proj is None:
        baseline_by_proj = {}

    # Per-project coverage gap analysis
    proj_counts: dict[str, int] = Counter(_project_of(i) for i in issues)
    new_by_proj: dict[str, int] = Counter(
        _project_of(i) for i in issues
        if (i.get("created") or "")[:10] > BUG_BASH_START)
    coverage_rows = []
    for proj in sorted(baseline_by_proj, key=lambda p: -(baseline_by_proj[p] - (proj_counts.get(p, 0) - new_by_proj.get(p, 0)))):
        bl = baseline_by_proj[proj]
        from_bl = proj_counts.get(proj, 0) - new_by_proj.get(proj, 0)
        not_reached = bl - from_bl
        cov = from_bl / bl * 100 if bl else 0
        if not_reached > 0:
            coverage_rows.append(f"<strong>{proj}</strong>: {not_reached} not reached ({cov:.0f}% covered)")

    # Estimate current backlog size for the tooltip
    if has_baseline:
        ai_labelled_pre_bash = len(issues) - total_new
        untouched = baseline_total - ai_labelled_pre_bash - pre_bash_excluded
        resolved_during_or_after = sum(1 for i in issues
                                       if (i.get("created") or "")[:10] <= BUG_BASH_START
                                       and i.get("resolved"))
        new_resolved = sum(1 for i in issues
                           if (i.get("created") or "")[:10] > BUG_BASH_START
                           and i.get("resolved"))
        total_resolved = pre_bash_excluded + resolved_during_or_after + new_resolved
        still_open_ai = ai_labelled_pre_bash - resolved_during_or_after
        still_open_new = total_new - new_resolved
        estimated_current = untouched + still_open_ai + still_open_new
    else:
        estimated_current = None
        total_resolved = 0

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>AI First Bug Bash Deep Analysis — March 22–29, 2026</title>
<style>
    body {{ font-family: 'Segoe UI', -apple-system, sans-serif; max-width: 1000px; margin: 0 auto; padding: 20px 40px; color: #2c3e50; line-height: 1.6; }}
    h1 {{ color: #1a252f; border-bottom: 3px solid #2980b9; padding-bottom: 10px; }}
    h2 {{ color: #2980b9; margin-top: 40px; border-bottom: 1px solid #ddd; padding-bottom: 5px; }}
    h3 {{ color: #34495e; margin-top: 25px; }}
    h4 {{ color: #7f8c8d; }}
    .stat-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin: 20px 0; }}
    .stat-box {{ background: #f8f9fa; border-left: 4px solid #2980b9; padding: 15px; border-radius: 4px; cursor: help; }}
    .stat-box.green {{ border-color: #2ecc71; }}
    .stat-box.red {{ border-color: #e74c3c; }}
    .stat-box.orange {{ border-color: #e67e22; }}
    .stat-box.purple {{ border-color: #8e44ad; }}
    .stat-box .number {{ font-size: 28px; font-weight: bold; color: #2c3e50; }}
    .stat-box .label {{ font-size: 12px; color: #7f8c8d; text-transform: uppercase; }}
    table {{ border-collapse: collapse; width: 100%; margin: 15px 0; font-size: 0.9em; }}
    th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
    th {{ background: #f1f3f5; font-weight: 600; }}
    tr:nth-child(even) {{ background: #f8f9fa; }}
    .recommendation {{ background: #fff3cd; border-left: 4px solid #ffc107; padding: 12px 16px; margin: 10px 0; border-radius: 4px; }}
    .recommendation.rec-critical {{ background: #f8d7da; border-left: 4px solid #dc3545; }}
    .recommendation.rec-medium {{ background: #fff3cd; border-left: 4px solid #ffc107; }}
    .recommendation.rec-low {{ background: #d4edda; border-left: 4px solid #28a745; }}
    .severity-badge {{ display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: 0.75em; font-weight: bold; text-transform: uppercase; letter-spacing: 0.5px; margin-left: 8px; vertical-align: middle; }}
    .severity-badge.critical {{ background: #dc3545; color: white; }}
    .severity-badge.medium {{ background: #ffc107; color: #333; }}
    .severity-badge.low {{ background: #28a745; color: white; }}
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

<h1>AI First Bug Bash — Deep Analysis Report</h1>
<p style="color:#7f8c8d;font-size:0.95em;">
    RH AI Engineering &bull; March 22–29, 2026 &bull; Generated {datetime.now().strftime("%B %d, %Y %H:%M")}
</p>

<div class="toc">
<strong>Contents</strong>
<ol>
<li><a href="#exec-summary">Executive Summary</a></li>
<li><a href="#pipeline">Triage Pipeline & Outcomes</a></li>
<li><a href="#by-project">Breakdown by Project</a></li>
<li><a href="#temporal-split">Bug Bash Week vs Current State</a></li>
<li><a href="#nonfixable">Critical Question: Why Are Tickets Nonfixable?</a></li>
<li><a href="#acceleration">Critical Question: Converting Accelerated-Fix to Fully Automated</a></li>
<li><a href="#agentready">Bug Automation Readiness vs Bug Bash Outcomes</a></li>
<li><a href="#recommendations">Recommendations: Improving Prompts & Process</a></li>
<li><a href="#appendix">Appendix: Methodology</a></li>
</ol>
</div>

<!-- ============================================================ -->
<h2 id="exec-summary">1. Executive Summary</h2>

<div class="stat-grid">
    <div class="stat-box" title="{f'Backlog as of March 22: {total_display} bugs in New/Backlog/Refinement/To Do across RHOAIENG, AIPCC, RHAIENG, INFERENG. Estimated current backlog: ~{estimated_current} open ({total_resolved} resolved: {pre_bash_excluded} before bash + {resolved_during_or_after} during/after + {new_resolved} new; {total_new} new bugs filed since Mar 22). {untouched} bugs were not AI-labelled — their resolution status is unknown and assumed still open.' if has_baseline and estimated_current is not None else ('Bugs with bug bash labels only (' + str(non_bugs) + ' non-Bug issues excluded). Run make collect to fetch the full baseline count from JIRA.')}"><div class="number">{total_display}</div><div class="label">Total Issues (Backlog)</div></div>
    <div class="stat-box purple" title="{'Of the ' + str(total_display) + ' backlog bugs, ' + str(from_backlog_count) + ' (' + f'{backlog_coverage_pct:.0f}' + '%) received bug bash labels. An additional ' + str(total_new) + ' new bugs created after Mar 22 were also labelled, for ' + str(len(issues)) + ' total.' if has_baseline else str(len(issues)) + ' issues with bug bash labels.'} {raw_triaged_label} have ai-triaged; {missing_triaged} have other bug bash labels but are missing ai-triaged. See Appendix for full breakdown."><div class="number">{len(issues)}</div><div class="label">Triaged by AI</div></div>
    <div class="stat-box" title="automated ({automated}) + accelerated ({accelerated}) + could-not-fix ({could_not}) + verif-failed ({verif_fail}) + fixable-pending ({cls_counts['fixable_pending']}). Includes all issues deemed fixable at triage, regardless of outcome."><div class="number">{fixable}</div><div class="label">Fixable</div></div>
    <div class="stat-box red" title="JIRA label filter shows {raw_nf_label}, but {conflicting_nf} issue(s) also have a higher-priority outcome label and are counted there instead. See Appendix for classification rules."><div class="number">{nonfixable}</div><div class="label">Nonfixable</div></div>
</div>

<div class="stat-grid">
    <div class="stat-box green" title="Issues with ai-fully-automated label. AI fixed the issue end-to-end with no human intervention."><div class="number">{automated}</div><div class="label">Fully Automated</div></div>
    <div class="stat-box" title="Issues with ai-accelerated-fix label. AI contributed but a human was needed to finish the fix."><div class="number">{accelerated}</div><div class="label">Accelerated Fix</div></div>
    <div class="stat-box red" title="Issues with ai-could-not-fix ({could_not}) or ai-verification-failed ({verif_fail}). Deemed fixable at triage but AI failed to produce a working fix."><div class="number">{could_not + verif_fail}</div><div class="label">Could Not Fix / Verif Failed</div></div>
    <div class="stat-box green" title="fully-automated ({automated}) / fixable ({fixable}) = {automation_rate:.1f}%. Measures what % of fixable issues AI solved without any human help."><div class="number">{automation_rate:.1f}%</div><div class="label">Automation Rate<br>(automated / fixable)</div></div>
</div>

<p>Of <strong>{total_display}</strong> bugs in the backlog when the bug bash started (March 22),
<strong>{from_backlog_count}</strong> ({backlog_coverage_pct:.0f}%) received one or more bug bash labels.
An additional <strong>{total_new}</strong> new bugs created after March 22 were also triaged, bringing the total to
<strong>{len(issues)}</strong> AI-labelled bugs.
Of those, <strong>{fixable}</strong> ({fixable/len(issues)*100:.0f}%) were
deemed fixable, <strong>{nonfixable}</strong> ({nonfixable/len(issues)*100:.0f}%) were deemed nonfixable,
and <strong>{pending_classification}</strong> ({pending_classification/len(issues)*100:.0f}%) were triaged but not yet classified as fixable or nonfixable.
Of those {fixable} fixable issues, <strong>{automated}</strong> ({automation_rate:.1f}%) were fully automated
end-to-end without human intervention.
Another <strong>{accelerated}</strong> ({accelerated/fixable*100:.1f}%) required human assist (accelerated fix), while <strong>{could_not}</strong> could not be fixed
and <strong>{verif_fail}</strong> failed verification.</p>

<div class="insight">
<strong>Key Finding:</strong> The {automation_rate:.1f}% automation rate means {fixable - automated} of {fixable} fixable issues
still required human involvement. The {accelerated} accelerated-fix issues are the clearest conversion opportunity \u2014
these were fixable and AI contributed, but couldn\u2019t finish the job alone.
{f"Additionally, {pending_classification} issues ({pending_classification/len(issues)*100:.0f}%) are still pending fixable/nonfixable classification." if pending_classification else ""}
{f"""<br><strong>Coverage:</strong> AI triaged {from_backlog_count} of {total_display} backlog bugs ({backlog_coverage_pct:.0f}%) &mdash;
{total_display - from_backlog_count} bugs were not reached by AI triage.
Per-project coverage: {", ".join(coverage_rows)}.""" if has_baseline and coverage_rows else (f"<br><strong>Coverage:</strong> AI triaged {from_backlog_count} of {total_display} backlog bugs ({backlog_coverage_pct:.0f}%) &mdash; {total_display - from_backlog_count} bugs were not reached by AI triage." if has_baseline else "")}
</div>

<!-- ============================================================ -->
<h2 id="pipeline">2. Triage Pipeline & Outcomes</h2>

{img("triage_funnel")}
{img("outcome_distribution")}
{img("success_rate")}

<h3>What the data tells us</h3>
<ul>
<li><strong>{fixable/len(issues)*100:.0f}% fixable rate</strong> \u2014 AI triage classified {fixable} of {triaged} triaged issues as fixable.
The challenge is in the {nonfixable/triaged*100:.0f}% deemed nonfixable.</li>
<li><strong>{automation_rate:.1f}% automation rate</strong> \u2014 only {automated} of {fixable} fixable issues were fully automated.
{accelerated} more were accelerated (AI helped, human finished), and {could_not + verif_fail} failed.</li>
<li><strong>Blocker and Critical priorities</strong> are disproportionately nonfixable — these tend to be environment-dependent
or cross-service issues that AI lacks the context to address.</li>
</ul>

<!-- ============================================================ -->
<h2 id="by-project" class="page-break">3. Breakdown by Project</h2>

{img("project_breakdown")}
{img("project_success_rate")}
"""
    # Build per-project summary table using unique-issue classification
    proj_cls: dict[str, Counter] = defaultdict(Counter)
    for i in issues:
        proj_cls[_project_of(i)][_classify_issue(i)] += 1

    proj_rows = ""
    for proj in sorted(proj_cls, key=lambda p: -sum(proj_cls[p].values())):
        pc = proj_cls[proj]
        total = sum(pc.values())
        p_baseline = baseline_by_proj.get(proj)
        p_baseline_cell = f"{p_baseline}" if p_baseline is not None else "&mdash;"
        p_automated = pc["automated"]
        p_accelerated = pc["accelerated"]
        p_could_not = pc["could_not_fix"]
        p_verif_fail = pc["verif_failed"]
        p_fixable_pending = pc["fixable_pending"]
        p_nonfixable = pc["nonfixable"]
        p_triaged_only = pc["triaged_only"]
        p_untriaged = pc["untriaged"]
        p_fixable_all = p_automated + p_accelerated + p_could_not + p_verif_fail + p_fixable_pending
        p_triaged_all = p_fixable_all + p_nonfixable + p_triaged_only
        p_rate = round(p_automated / p_fixable_all * 100, 1) if p_fixable_all else 0
        p_accel_rate = round(p_accelerated / p_fixable_all * 100, 1) if p_fixable_all else 0
        p_coverage = f" ({total / p_baseline * 100:.0f}%)" if p_baseline else ""
        pending_note = f' <span style="color:#e67e22">({p_triaged_only} pending)</span>' if p_triaged_only > 0 else ""
        proj_rows += f"""<tr>
            <td><strong>{proj}</strong></td>
            <td>{p_baseline_cell}</td><td>{total}{p_coverage}</td><td>{p_triaged_all}{pending_note}</td>
            <td>{p_fixable_all}</td><td>{p_nonfixable}</td>
            <td>{p_automated}</td><td>{p_accelerated}</td>
            <td>{p_could_not}</td><td>{p_verif_fail}</td>
            <td><strong>{p_rate:.1f}%</strong> ({p_automated}/{p_fixable_all})</td>
            <td>{p_accel_rate:.1f}% ({p_accelerated}/{p_fixable_all})</td>
        </tr>"""

    html += f"""
<table>
<thead><tr>
    <th>Project</th><th>Backlog</th><th>AI Triaged</th><th>Classified</th><th>Fixable</th><th>Nonfixable</th>
    <th>Automated</th><th>Accelerated</th><th>Could Not Fix</th><th>Verif Failed</th>
    <th>Automation Rate</th>
    <th>Accelerated Rate</th>
</tr></thead>
<tbody>{proj_rows}</tbody>
</table>

<h3>Totals — All Projects Combined</h3>
{img("project_dashboard_totals")}
"""

    # Per-project dashboards
    project_order = charts.get("_project_order", [])
    for proj in project_order:
        html += f"""
<h3>{proj}</h3>
{img(f"project_dashboard_{proj}")}
"""

    html += """
<!-- ============================================================ -->
<h2 id="temporal-split" class="page-break">4. Bug Bash Week vs Current State</h2>
"""
    # Compute temporal split stats (only need unresolved for "Remaining Work")
    _during, _before, _after, unresolved_all = _split_by_period(issues)
    unresolved_with_outcome = sum(1 for i in unresolved_all if any(l in _labels(i) for l in OUTCOME_LABELS))
    fixable_awaiting = sum(1 for i in unresolved_all if "ai-fixable" in _labels(i) and not any(l in _labels(i) for l in OUTCOME_LABELS))

    html += f"""
<p>The bug bash ran March 22\u201329, but work has continued after. This section separates results
from the event week itself versus the current cumulative state.</p>

{img("temporal_comparison")}

<h3>Remaining Work</h3>
<ul>
<li><strong>{fixable_awaiting} fixable issues</strong> have not yet been attempted by AI — this is the immediate opportunity.</li>
<li><strong>{unresolved_with_outcome} issues</strong> have outcome labels but open JIRA status — these need JIRA hygiene (move to Done or re-open).</li>
<li><strong>{nonfixable} nonfixable issues</strong> need either process improvements (see recommendations) or manual human resolution.</li>
</ul>

<!-- ============================================================ -->
<h2 id="nonfixable" class="page-break">5. Critical Question: Why Are Tickets Nonfixable?</h2>

<p><strong>{nonfixable} issues</strong> were marked <code>ai-nonfixable</code> during triage. This section analyzes
the structural reasons and identifies what would need to change to make them fixable.</p>

{img("nonfixable_themes")}
{img("nonfixable_by_component")}
{img("fixable_vs_nonfixable")}

<h3>Per-Project Charts</h3>
"""
    for proj in charts.get("_project_order", []):
        proj_nf_themes = img(f"nonfixable_themes_{proj}")
        proj_nf_comp = img(f"nonfixable_by_component_{proj}")
        proj_fvn = img(f"fixable_vs_nonfixable_{proj}")
        if any(x for x in [proj_nf_themes, proj_nf_comp, proj_fvn]):
            html += f'<h4>{proj}</h4>\n{proj_nf_themes}\n{proj_nf_comp}\n{proj_fvn}\n'

    html += """
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

<h3>Per-Project Nonfixable Breakdown</h3>
"""
    # Per-project nonfixable analysis
    nf_by_proj: dict[str, list] = defaultdict(list)
    for i in analysis["all_nonfixable"]:
        nf_by_proj[_project_of(i)].append(i)

    html += """<table>
<thead><tr><th>Project</th><th>Nonfixable</th><th>Top Themes</th><th>Nonfixable Rate</th></tr></thead>
<tbody>"""

    fix_by_proj: dict[str, int] = Counter(_project_of(i) for i in analysis["all_fixable"])
    for proj in sorted(nf_by_proj, key=lambda p: -len(nf_by_proj[p])):
        p_nf = len(nf_by_proj[proj])
        p_fix = fix_by_proj.get(proj, 0)
        p_total = p_nf + p_fix
        p_rate = p_nf / p_total * 100 if p_total else 0

        p_themes: Counter = Counter()
        for i in nf_by_proj[proj]:
            desc = (i.get("description") or i.get("summary") or "").lower()
            if any(w in desc for w in ["ui", "frontend", "dashboard", "visual", "css", "layout"]):
                p_themes["UI/frontend"] += 1
            elif any(w in desc for w in ["cluster", "infra", "environment", "operator", "deploy", "node"]):
                p_themes["Infrastructure"] += 1
            elif any(w in desc for w in ["flak", "intermittent", "timing", "race", "timeout"]):
                p_themes["Flaky/timing"] += 1
            else:
                p_themes["Other"] += 1
        themes_str = ", ".join(f"{t} ({c})" for t, c in p_themes.most_common(3))
        html += f"<tr><td><strong>{proj}</strong></td><td>{p_nf}</td><td>{themes_str}</td><td><strong>{p_rate:.0f}%</strong></td></tr>"

    html += "</tbody></table>"

    html += f"""
<!-- ============================================================ -->
<h2 id="acceleration" class="page-break">6. Critical Question: Converting Accelerated-Fix to Fully Automated</h2>

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

    # Per-project acceleration breakdown
    accel_by_proj: dict[str, int] = Counter(_project_of(i) for i in analysis["all_accelerated"])
    auto_by_proj: dict[str, int] = Counter(_project_of(i) for i in analysis["all_automated"])
    all_accel_projs = sorted(set(accel_by_proj) | set(auto_by_proj), key=lambda p: -(accel_by_proj.get(p, 0) + auto_by_proj.get(p, 0)))

    html += """
<h3>Per-Project Automation vs Acceleration</h3>
<table>
<thead><tr><th>Project</th><th>Fully Automated</th><th>Accelerated</th><th>Automation Rate</th><th>Conversion Opportunity</th></tr></thead>
<tbody>"""
    for proj in all_accel_projs:
        p_au = auto_by_proj.get(proj, 0)
        p_ac = accel_by_proj.get(proj, 0)
        p_total = p_au + p_ac
        p_auto_rate = p_au / p_total * 100 if p_total else 0
        html += f"<tr><td><strong>{proj}</strong></td><td>{p_au}</td><td>{p_ac}</td><td><strong>{p_auto_rate:.0f}%</strong></td><td>{p_ac} issues to convert</td></tr>"
    html += "</tbody></table>"

    html += f"""
<div class="insight">
<strong>Key Finding:</strong> {"Accelerated issues average <em>longer</em> descriptions (" + f"{gap['accelerated']['avg_desc_len']:.0f} chars vs {gap['automated']['avg_desc_len']:.0f}" + " for automated), so the barrier is not lack of ticket detail. Instead, the gap is likely <em>codebase context</em> — components with high multi-attempt rates may lack sufficient architectural documentation, test patterns, or repo-level context for AI to produce a correct fix on the first try." if gap['accelerated']['avg_desc_len'] > gap['automated']['avg_desc_len'] else "Automated issues average richer descriptions (" + f"{gap['automated']['avg_desc_len']:.0f} chars vs {gap['accelerated']['avg_desc_len']:.0f}" + " for accelerated), suggesting that better ticket detail directly improves single-shot automation."} The {accelerated} accelerated-fix issues are the primary conversion opportunity.
</div>

<!-- ============================================================ -->
"""

    # Section 7: AI Bug Automation Readiness (conditional)
    ar_data = analysis.get("agentready", [])
    if ar_data:
        bb_by_proj = analysis.get("agentready_bb_by_proj", {})
        html += """
<h2 id="agentready" class="page-break">7. Bug Automation Readiness vs Bug Bash Outcomes</h2>

<p>This section correlates <a href="https://github.com/ugiordan/ai-bug-automation-readiness">AI Bug Automation Readiness</a>
scores with bug bash outcomes. The tool evaluates repos on 20 checks across 4 phases
(Understand, Navigate, Verify, Submit) that predict how well AI agents can autonomously find,
fix, and verify bugs. Verify (testing) carries 46% of the total weight.</p>
"""
        html += '<table><thead><tr><th>Project</th><th>Repo(s)</th><th>Readiness Score</th><th>Level</th><th>Automation Rate</th><th>Nonfixable Rate</th></tr></thead><tbody>'
        for row in ar_data:
            proj = row["project"]
            repo_name = row["repo_url"].rstrip("/").rsplit("/", 1)[-1].replace(".git", "")
            bb = bb_by_proj.get(proj, {})
            auto_rate = f'{bb.get("automation_rate", 0):.1f}%' if bb else "N/A"
            nf_rate = f'{bb.get("nonfixable_rate", 0):.1f}%' if bb else "N/A"
            html += f'<tr><td><strong>{proj}</strong></td><td>{repo_name}</td><td>{row["overall_score"]:.0f}/100</td><td>{row["certification_level"]}</td><td>{auto_rate}</td><td>{nf_rate}</td></tr>'
        html += '</tbody></table>'


        highlight_attrs = HIGHLIGHT_ATTRIBUTES[:8]
        per_proj_attrs: dict[str, dict[str, float]] = {}
        for row in ar_data:
            findings = json.loads(row.get("findings_json") or "[]")
            attr_scores = {}
            for f in findings:
                aid = f.get("attribute", {}).get("id", "")
                if aid in highlight_attrs:
                    attr_scores[aid] = f.get("score", 0)
            per_proj_attrs[row["project"]] = attr_scores

        if per_proj_attrs:
            html += '<h3>Key Checks by Project</h3>'
            html += '<table><thead><tr><th>Check</th>'
            projs = sorted(per_proj_attrs.keys())
            for p in projs:
                html += f'<th>{p}</th>'
            html += '</tr></thead><tbody>'
            for attr in highlight_attrs:
                nice = attr.replace("_", " ").title()
                html += f'<tr><td>{nice}</td>'
                for p in projs:
                    val = per_proj_attrs[p].get(attr)
                    html += f'<td>{val:.0f}/100</td>' if val is not None else '<td>N/A</td>'
                html += '</tr>'
            html += '</tbody></table>'

        best = max(ar_data, key=lambda r: r["overall_score"])
        worst = min(ar_data, key=lambda r: r["overall_score"])
        best_bb = bb_by_proj.get(best["project"], {})
        worst_bb = bb_by_proj.get(worst["project"], {})
        if best["project"] != worst["project"]:
            html += f"""
<div class="insight">
<strong>Key Finding:</strong> The highest-scoring repo (<strong>{best["project"]}</strong>, score {best["overall_score"]:.0f})
has an automation rate of {best_bb.get("automation_rate", 0):.1f}%, while the lowest-scoring repo
(<strong>{worst["project"]}</strong>, score {worst["overall_score"]:.0f}) has {worst_bb.get("automation_rate", 0):.1f}%.
{"This suggests a positive correlation between bug automation readiness and AI automation success." if best_bb.get("automation_rate", 0) > worst_bb.get("automation_rate", 0) else "Interestingly, a higher readiness score does not guarantee better automation &mdash; other factors like issue complexity and component breadth also play a major role."}
</div>
"""
    else:
        html += """
<h2 id="agentready" class="page-break">7. Bug Automation Readiness vs Bug Bash Outcomes</h2>
<p><em>No readiness assessment data available. Run <code>make agentready</code> to collect repo scores and enable this section.</em></p>
"""

    html += f"""
<!-- ============================================================ -->
<h2 id="recommendations" class="page-break">8. Recommendations: Improving Prompts & Process</h2>

<p>Based on the data analysis, here are specific actions to improve AI automation rates. Each is rated by impact and urgency.</p>

<h3>8.1 Making Nonfixable Issues Fixable</h3>

<div class="recommendation rec-critical">
<strong>R1: Improve Bug Report Templates</strong> <span class="severity-badge critical">Critical</span><br>
{len(theme_data.get('insufficient context / vague description', []))} issues lacked sufficient context for AI to determine a fix.
This is the lowest-effort, highest-impact change.<br>
<em>Action:</em> Enforce structured JIRA templates with required fields: reproduction steps, expected vs actual behavior,
environment details, and affected code paths. The triage prompt should reject issues that lack these.
</div>

<div class="recommendation rec-critical">
<strong>R2: Enrich Triage Prompt with Rejection Criteria</strong> <span class="severity-badge critical">Critical</span><br>
The current triage prompt asks AI to label issues as fixable/nonfixable but doesn't provide clear criteria for the decision.<br>
<em>Action:</em> Add explicit rejection criteria to the triage prompt:
<blockquote>
"Mark as ai-nonfixable if: (1) the fix requires visual verification of UI rendering, (2) the fix requires
a running cluster or specific infrastructure, (3) the issue description lacks reproduction steps, or (4) the
fix spans multiple repositories. For each nonfixable issue, add a comment explaining WHICH criterion applies
and what additional context would make it fixable."
</blockquote>
</div>

<div class="recommendation rec-medium">
<strong>R3: Add Visual Regression Tooling</strong> <span class="severity-badge medium">Medium</span><br>
{len(theme_data.get('UI / visual / frontend', []))} of {nonfixable} nonfixable issues ({len(theme_data.get('UI / visual / frontend', []))/nonfixable*100:.0f}%) involve UI/frontend work.
AI cannot verify visual correctness without tooling.<br>
<em>Action:</em> Integrate screenshot comparison (e.g., Percy, Chromatic, or Playwright visual snapshots) into
the AI verification pipeline. This would allow AI to verify its own UI fixes.
</div>

<div class="recommendation rec-medium">
<strong>R4: Provide Environment-in-a-Box</strong> <span class="severity-badge medium">Medium</span><br>
{len(theme_data.get('infrastructure / cluster / environment', []))} issues require cluster/infrastructure access.
The current AI workflow operates on source code only.<br>
<em>Action:</em> Create ephemeral test environments (e.g., Kind clusters with ODH pre-installed) that the AI agent
can provision and test against. High effort, high impact.
</div>

<h3>8.2 Converting Accelerated-Fix to Fully Automated</h3>

<div class="recommendation rec-critical">
<strong>R5: Pre-load Component Architecture Context</strong> <span class="severity-badge critical">Critical</span><br>
Components with 100% multi-attempt rates need richer context in the AI session.<br>
<em>Action:</em> For each component, create an <code>AI_CONTEXT.md</code> file in the repo root with:
architecture overview, key abstractions, common bug patterns, test strategy, and links to related services.
The fix prompt should explicitly reference this file. The <code>architecture-context</code> repo is a good start
but needs component-specific depth.
</div>

<div class="recommendation rec-critical">
<strong>R6: Structured Fix Prompt with Verification Steps</strong> <span class="severity-badge critical">Critical</span><br>
The current workflow generates fix files but doesn't prescribe verification strategy.<br>
<em>Action:</em> Update the fix prompt to require a verification plan before coding:
<blockquote>
"Before writing any code: (1) identify the root cause from the issue description and codebase, (2) list
the specific test(s) that should pass after the fix, (3) if no existing test covers this, write a failing
test first. Only then implement the fix. Run the tests and verify they pass."
</blockquote>
</div>

<div class="recommendation rec-medium">
<strong>R7: Feedback Loop from Failures</strong> <span class="severity-badge medium">Medium</span><br>
{could_not} issues were marked "could not fix" and {verif_fail} had verification failures, but these outcomes
don't feed back into future attempts.<br>
<em>Action:</em> For each "could not fix" and "verification failed" outcome, require a structured comment on the JIRA:
what the AI tried, why it failed, and what context was missing. These comments become training data for
improving prompts and identifying systematic gaps.
</div>

<div class="recommendation rec-low">
<strong>R8: Batch Size Optimization</strong> <span class="severity-badge low">Low</span><br>
The current guidance recommends batching ~20 issues per triage prompt. For fix attempts, single-issue
focus produces better results.<br>
<em>Action:</em> Keep batch triage (20 at a time) for classification, but switch to <strong>single-issue fix sessions</strong>
where the AI agent gets one issue, the full repo context, and architectural docs.
</div>

<h3>8.3 Process Improvements</h3>

<div class="recommendation rec-medium">
<strong>R9: Two-Phase Triage</strong> <span class="severity-badge medium">Medium</span><br>
The current single-pass triage misses nuance. {sum(1 for i in issues if 'ai-initiallymarkedfixable' in _labels(i))} issues were
initially marked fixable then reclassified.<br>
<em>Action:</em> Phase 1: AI triage with the current prompt. Phase 2: Human review of AI's nonfixable
classifications with a focus on "what context would make this fixable?" — then re-triage with enhanced context.
</div>

<div class="recommendation rec-low">
<strong>R10: Model Selection Matters</strong> <span class="severity-badge low">Low</span><br>
The ambient guidance recommends Opus 4.6, which is a strong choice for reasoning.
However, different models may perform better for different issue types.<br>
<em>Action:</em> Track which model was used per issue in future bug bashes.
For UI-heavy components (Dashboard), multimodal models that can process screenshots may outperform text-only models.
</div>

<h3>8.4 Prompt Template Improvements</h3>

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

<!-- ============================================================ -->
<h2 id="appendix" class="page-break">Appendix: Methodology</h2>

<h3>Data Source</h3>
<p>All data is sourced from JIRA (Atlassian Cloud) via the JIRA REST API v3, collected using the
<code>odh-eng-metrics</code> tooling. Issues are stored locally in a SQLite database and refreshed
on each <code>make collect</code> run.</p>

<table>
<tbody>
<tr><td><strong>Projects</strong></td><td>RHOAIENG, AIPCC, RHAIENG, INFERENG</td></tr>
<tr><td><strong>Issue type filter</strong></td><td><code>issuetype = Bug</code> &mdash; {len(issues)} bugs of {len(issues) + non_bugs} total issues</td></tr>
<tr><td><strong>Pre-bash exclusion</strong></td><td>Bugs resolved before {BUG_BASH_START} are excluded (triaged retroactively but already closed)</td></tr>
<tr><td><strong>Collection JQL</strong></td><td>Issues matching any of the bug bash labels: {BUG_BASH_LABELS_DISPLAY}</td></tr>
<tr><td><strong>Baseline JQL</strong></td><td>{"<code>" + "project in (RHOAIENG, AIPCC, RHAIENG, INFERENG) AND issuetype = Bug AND created &lt;= 2026-03-22 AND (status in (New, Backlog, Refinement, To Do) OR status changed from (...) after 2026-03-22)</code> &mdash; total: " + str(total_display) if has_baseline else "Not collected (run <code>make collect</code> to fetch)"}</td></tr>
<tr><td><strong>Bug bash period</strong></td><td>March 22&ndash;29, 2026</td></tr>
<tr><td><strong>Report generated</strong></td><td>{datetime.now().strftime("%B %d, %Y %H:%M")}</td></tr>
</tbody>
</table>

<h3>Total Issues (Backlog) vs Triaged by AI</h3>
<p>The <strong>Total Issues (Backlog)</strong> count ({total_display}) represents all bugs across the four projects that
were in an open status (New, Backlog, Refinement, or To Do) on the day the bug bash started (March 22, 2026),
regardless of whether they had bug bash labels. This count is derived from a separate baseline JQL query
run during <code>make collect</code>.</p>
<p>The <strong>Triaged by AI</strong> count ({len(issues)}) comprises <strong>{from_backlog_count}</strong> bugs from the
original backlog that received one or more bug bash labels ({backlog_coverage_pct:.0f}% of {total_display}), plus
<strong>{total_new}</strong> new bugs created after March 22 that were also labelled. The remaining
{total_display - from_backlog_count} backlog bugs were not reached by AI triage.</p>

<h4>Bug Bash Labels</h4>
<p>This report collects issues that have <strong>any</strong> of the following labels:
{BUG_BASH_LABELS_DISPLAY}. An issue is included in our dataset if it has at least one of these labels,
even if it is missing <code>ai-triaged</code>.</p>

<h4>"Triaged by AI" Count Breakdown ({len(issues)})</h4>
<table>
<thead><tr><th>Category</th><th>Count</th><th>Explanation</th></tr></thead>
<tbody>
<tr><td>Bugs in backlog (New/Backlog/Refinement/To Do) at start of bug bash</td>
    <td><strong>{total_display}</strong></td>
    <td>Baseline JQL count as of March 22</td></tr>
<tr><td>Backlog bugs that received bug bash labels</td>
    <td><strong>{from_backlog_count}</strong></td>
    <td>{backlog_coverage_pct:.0f}% of {total_display} backlog</td></tr>
<tr><td>New bugs created after March 22 with bug bash labels</td>
    <td><strong>{total_new}</strong></td>
    <td>These were not part of the original backlog</td></tr>
<tr><td><strong>Total issues in this report</strong></td>
    <td><strong>{len(issues)}</strong></td>
    <td>= {from_backlog_count} backlog + {total_new} new</td></tr>
</tbody>
</table>

<h4>Why This Differs from a JIRA <code>ai-triaged</code> Filter</h4>
<p>A JIRA query filtering on <code>labels in ("ai-triaged")</code> with the same projects and status filter
returns fewer results than our {len(issues)}. The differences are:</p>
<table>
<thead><tr><th>Difference</th><th>Count</th><th>Explanation</th></tr></thead>
<tbody>
<tr><td>Issues with bug bash labels but <strong>no <code>ai-triaged</code></strong></td>
    <td>{len(no_triaged_has_bb)}</td>
    <td>Have outcome or triage labels (e.g. <code>ai-fixable</code>, <code>ai-could-not-fix</code>) but <code>ai-triaged</code> was not added.
        Our collection catches these; a JIRA <code>ai-triaged</code> filter does not.</td></tr>
<tr><td><code>ai-triaged</code> bugs <strong>not in backlog status</strong></td>
    <td>{triaged_not_open}</td>
    <td>Currently in Review ({sum(1 for i in issues if 'ai-triaged' in _labels(i) and i.get('status') == 'Review')}),
        Closed ({sum(1 for i in issues if 'ai-triaged' in _labels(i) and i.get('status') == 'Closed')}),
        Resolved ({sum(1 for i in issues if 'ai-triaged' in _labels(i) and i.get('status') == 'Resolved')}),
        In Progress ({sum(1 for i in issues if 'ai-triaged' in _labels(i) and i.get('status') == 'In Progress')}),
        Testing ({sum(1 for i in issues if 'ai-triaged' in _labels(i) and i.get('status') == 'Testing')}).
        A JQL with <code>status changed from (...) after 2026-03-22</code> may miss bugs
        that left backlog status <em>before</em> March 22.</td></tr>
<tr><td><code>ai-triaged</code> bugs <strong>in backlog status</strong></td>
    <td>{triaged_in_open}</td>
    <td>These should match a JIRA <code>ai-triaged</code> filter with backlog status clause</td></tr>
</tbody>
</table>
<p><strong>Pre-bash exclusions:</strong> {pre_bash_excluded} bugs that were resolved before March 22 are excluded from
this report entirely (they were triaged retroactively but were already closed).</p>

<h3>Issue Classification</h3>
<p>Each issue is assigned to exactly <strong>one</strong> category using a priority-based classification.
When an issue has multiple (potentially conflicting) labels, the first matching rule wins:</p>

<table>
<thead><tr><th>Priority</th><th>Label</th><th>Classification</th><th>Meaning</th></tr></thead>
<tbody>
<tr><td>1</td><td><code>ai-fully-automated</code></td><td>Automated</td><td>AI fixed it end-to-end, no human needed</td></tr>
<tr><td>2</td><td><code>ai-accelerated-fix</code></td><td>Accelerated</td><td>AI contributed but a human finished the fix</td></tr>
<tr><td>3</td><td><code>ai-could-not-fix</code></td><td>Could not fix</td><td>Deemed fixable at triage, but AI failed to produce a working fix</td></tr>
<tr><td>4</td><td><code>ai-verification-failed</code></td><td>Verification failed</td><td>AI produced a fix but it didn't pass verification</td></tr>
<tr><td>5</td><td><code>ai-nonfixable</code></td><td>Nonfixable</td><td>AI triage determined this issue cannot be fixed by AI</td></tr>
<tr><td>6</td><td><code>ai-fixable</code></td><td>Fixable (pending)</td><td>Deemed fixable but no fix attempt has been made yet</td></tr>
<tr><td>7</td><td><code>ai-triaged</code></td><td>Triaged only</td><td>AI reviewed but did not classify as fixable or nonfixable</td></tr>
<tr><td>8</td><td>(none of the above)</td><td>Untriaged</td><td>Not yet processed by AI</td></tr>
</tbody>
</table>

<p>Rows 1–4 and 6 are all counted as <strong>"fixable"</strong> for the automation rate denominator — they were all
deemed fixable at triage, regardless of whether the fix attempt succeeded. The automation rate measures how many
of those fixable issues were fully automated (row 1 only).</p>

<p>When an issue has conflicting labels (e.g., both <code>ai-nonfixable</code> and <code>ai-accelerated-fix</code>),
the highest-priority label wins. There are currently {conflicting_nf} such issue(s), which is why the nonfixable count here ({nonfixable})
differs slightly from a raw JIRA label filter ({raw_nf_label}).</p>

<h3>Key Metrics</h3>
<table>
<thead><tr><th>Metric</th><th>Formula</th><th>Description</th></tr></thead>
<tbody>
<tr><td><strong>Fixable</strong></td><td>automated + accelerated + could_not_fix + verif_failed + fixable_pending</td>
<td>All issues classified into a fixable bucket (with or without an outcome)</td></tr>
<tr><td><strong>Automation Rate</strong></td><td>fully_automated / fixable &times; 100</td>
<td>Of issues deemed fixable, what percentage were solved end-to-end by AI without human intervention</td></tr>
<tr><td><strong>Accelerated Rate</strong></td><td>accelerated_fix / fixable &times; 100</td>
<td>Of issues deemed fixable, what percentage required AI + human collaboration</td></tr>
<tr><td><strong>Nonfixable Rate</strong></td><td>nonfixable / (fixable + nonfixable) &times; 100</td>
<td>Proportion of classified issues that AI could not address</td></tr>
</tbody>
</table>

<h3>Temporal Bucketing</h3>
<p>For the "Bug Bash Week vs Current State" section, issues are split by their JIRA <code>resolved</code> date:</p>
<ul>
<li><strong>Before bug bash:</strong> resolved date &lt; March 22 (pre-existing closures)</li>
<li><strong>During bug bash:</strong> resolved date between March 22–29 inclusive</li>
<li><strong>After bug bash:</strong> resolved date &ge; March 30</li>
<li><strong>Unresolved:</strong> no resolved date in JIRA (may still have outcome labels)</li>
</ul>

<h3>Nonfixable Theme Analysis</h3>
<p>Nonfixable issues are categorized into themes by keyword matching on the issue summary and description.
Keywords include terms like "UI", "frontend", "dashboard" (→ UI/frontend), "cluster", "infra", "deploy"
(→ Infrastructure), "flak", "intermittent", "timeout" (→ Flaky/timing). Issues not matching any theme
keyword are categorized as "Other". This is an approximation — manual review would yield more precise categorization.</p>

<h3>Tooling</h3>
<ul>
<li><strong>Data collection:</strong> <code>odh-eng-metrics</code> (<code>make collect</code>)</li>
<li><strong>Report generation:</strong> <code>make bug-bash-report</code> (Python + matplotlib)</li>
<li><strong>Storage:</strong> SQLite (<code>data/eng-metrics.sqlite</code>)</li>
<li><strong>JIRA API:</strong> REST API v3 with <code>nextPageToken</code> pagination</li>
</ul>

<hr style="margin-top:40px;">
<p style="color:#95a5a6;font-size:0.85em;">
    Generated from {len(issues)} AI-triaged JIRA Bug issues (of {total_display} total backlog) collected via
    <a href="https://github.com/StevenTobin/ai-experiments/tree/main/odh-eng-metrics" style="color:#7f8c8d;">odh-eng-metrics</a>.
    Data source: AI First Bug Bash March 22&ndash;29, 2026.
</p>

</body></html>"""
    return html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import sys
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else OUTPUT_PATH

    store = Store("data/eng-metrics.sqlite")
    all_issues = store.get_collection_issues("ai-bug-bash")
    if not all_issues:
        print("No issues found for ai-bug-bash collection")
        return

    bugs = [i for i in all_issues if i.get("issue_type") == "Bug"]
    non_bugs = len(all_issues) - len(bugs)
    if non_bugs:
        print(f"Filtered to Bugs only: {len(bugs)} of {len(all_issues)} ({non_bugs} non-Bug issues excluded)")

    pre_resolved = [i for i in bugs if i.get("resolved") and i["resolved"][:10] < BUG_BASH_START]
    issues = [i for i in bugs if not (i.get("resolved") and i["resolved"][:10] < BUG_BASH_START)]
    if pre_resolved:
        print(f"Excluded {len(pre_resolved)} bugs resolved before bug bash ({BUG_BASH_START}): {len(issues)} remain")

    for i in issues:
        i["_labels"] = _labels(i)

    # Build classification-based lists (each issue in exactly one bucket)
    classified: dict[str, list[dict]] = defaultdict(list)
    for i in issues:
        classified[_classify_issue(i)].append(i)

    automated = classified["automated"]
    accelerated = classified["accelerated"]
    could_not_fix = classified["could_not_fix"]
    verification_failed = classified["verif_failed"]
    nonfixable_issues = classified["nonfixable"]
    fixable = automated + accelerated + could_not_fix + verification_failed + classified["fixable_pending"]

    print(f"Generating deep analysis for {len(issues)} issues...")
    print(f"  Fixable: {len(fixable)}, Nonfixable: {len(nonfixable_issues)}")
    print(f"  Automated: {len(automated)}, Accelerated: {len(accelerated)}")
    print(f"  Could not fix: {len(could_not_fix)}, Verification failed: {len(verification_failed)}")

    # Build per-project map early (needed for baseline + charts)
    by_proj: dict[str, list[dict]] = defaultdict(list)
    for i in issues:
        by_proj[_project_of(i)].append(i)

    # Load baseline counts (total bug population at bash start, from JQL)
    baseline_total = store.get_metric("baseline_total", "ai-bug-bash")
    baseline_by_proj: dict[str, int] = {}
    if baseline_total is not None:
        print(f"Baseline total from JQL: {baseline_total}")
        for proj in by_proj:
            bv = store.get_metric("baseline_total", f"ai-bug-bash:{proj}")
            if bv is not None:
                baseline_by_proj[proj] = bv
        if baseline_by_proj:
            print(f"  Per-project baselines: {baseline_by_proj}")
    else:
        print("No baseline count found (run 'make collect' to fetch from JIRA).")

    # Generate charts
    print("Generating charts...")
    nf_by_comp = chart_nonfixable_by_component(nonfixable_issues)
    nf_themes = chart_nonfixable_themes(nonfixable_issues)
    fix_vs_nf = chart_fixable_vs_nonfixable_components(fixable, nonfixable_issues)

    charts = {
        "triage_funnel": chart_triage_funnel(issues, baseline_total=baseline_total),
        "outcome_distribution": chart_outcome_distribution(issues),
        "nonfixable_by_component": nf_by_comp["total"],
        "nonfixable_themes": nf_themes["total"],
        "fixable_vs_nonfixable": fix_vs_nf["total"],
        "accelerated_vs_automated": chart_accelerated_vs_automated(accelerated, automated),
        "success_rate": chart_automation_rate(fixable, automated, accelerated, could_not_fix, verification_failed),
        "project_breakdown": chart_project_breakdown(issues, baseline_by_proj=baseline_by_proj),
        "project_success_rate": chart_project_automation_rate(issues),
        "project_dashboard_totals": chart_project_dashboard(
            issues, "All Projects (Totals)", baseline_count=baseline_total),
        "time_to_fix": chart_time_to_fix_by_outcome(issues),
        "temporal_comparison": chart_temporal_comparison(issues),
    }

    # Per-project dashboards
    for proj, proj_issues in sorted(by_proj.items(), key=lambda x: -len(x[1])):
        charts[f"project_dashboard_{proj}"] = chart_project_dashboard(
            proj_issues, proj, baseline_count=baseline_by_proj.get(proj))
    charts["_project_order"] = sorted(by_proj.keys(), key=lambda p: -len(by_proj[p]))

    for proj in charts["_project_order"]:
        charts[f"nonfixable_by_component_{proj}"] = nf_by_comp.get(proj)
        charts[f"nonfixable_themes_{proj}"] = nf_themes.get(proj)
        charts[f"fixable_vs_nonfixable_{proj}"] = fix_vs_nf.get(proj)

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

    # Bug Automation Readiness data (if available)
    ar_data = store.get_agentready_assessments()
    if ar_data:
        print(f"Loading readiness data for {len(ar_data)} repo(s)...")
        bb_by_proj = {}
        for proj, proj_issues in by_proj.items():
            p_cls = Counter(_classify_issue(i) for i in proj_issues)
            p_fix = p_cls["automated"] + p_cls["accelerated"] + p_cls["could_not_fix"] + p_cls["verif_failed"] + p_cls["fixable_pending"]
            p_nf = p_cls["nonfixable"]
            bb_by_proj[proj] = {
                "automation_rate": p_cls["automated"] / p_fix * 100 if p_fix else 0,
                "nonfixable_rate": p_nf / (p_fix + p_nf) * 100 if (p_fix + p_nf) else 0,
                "fixable": p_fix, "nonfixable": p_nf, "automated": p_cls["automated"],
            }
        analysis["agentready"] = ar_data
        analysis["agentready_bb_by_proj"] = bb_by_proj
    else:
        print("No readiness data found (run 'make agentready' to collect).")

    # Generate HTML
    print("Generating HTML report...")
    html = generate_html(issues, charts, analysis, non_bugs=non_bugs,
                         baseline_total=baseline_total, baseline_by_proj=baseline_by_proj,
                         pre_bash_excluded=len(pre_resolved))

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    print(f"\nReport written to {output}")
    print(f"Open in browser: file://{output.resolve()}")


if __name__ == "__main__":
    main()
