"""Collect merged PRs from git log on the bare clone -- zero API calls.

For squash merges, GitHub preserves the author date as the date of the first
commit on the branch, while the committer date is the merge time.  We exploit
this to estimate PR cycle time without any API calls.
"""

from __future__ import annotations

import logging
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from store.db import Store

log = logging.getLogger(__name__)

JIRA_RE = re.compile(r"RHOAIENG-\d+")

# GitHub merge commits look like:
#   "Merge pull request #1234 from user/branch"  (merge-commit strategy)
#   "Some title (#1234)"                          (squash-merge strategy)
PR_NUM_RE = re.compile(r"#(\d+)")


def _git(repo_path: Path, *args: str, timeout: int = 120) -> str:
    result = subprocess.run(
        ["git", f"--git-dir={repo_path}", *args],
        capture_output=True, text=True, timeout=timeout,
    )
    return result.stdout.strip()


def _resolve_ref(repo_path: Path, branch: str) -> str | None:
    for candidate in [branch, f"origin/{branch}", f"refs/heads/{branch}"]:
        r = subprocess.run(
            ["git", f"--git-dir={repo_path}", "rev-parse", "--verify", candidate],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            return candidate
    return None


def collect_prs_from_git(
    store: Store,
    repo_path: Path,
    repo_name: str,
    branch: str = "main",
    lookback_days: int = 365,
) -> int:
    """Parse merge/squash commits on a branch to extract PR data. Zero API calls."""
    ref = _resolve_ref(repo_path, branch)
    if not ref:
        log.warning("Branch %s not found in %s", branch, repo_path)
        return 0

    since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    # %aI = author date (ISO), %cI = committer date (ISO)
    # For squash merges: author date = first commit on branch, committer date = merge time
    # For merge commits: both dates are from the merge itself
    log_output = _git(
        repo_path, "log", ref,
        f"--since={since}",
        "--format=%H|%aI|%cI|%ae|%s",
    )
    if not log_output:
        return 0

    count = 0
    lines = log_output.split("\n")
    log.info("Parsing %d commits on %s/%s", len(lines), repo_name, branch)

    for line in lines:
        if not line.strip():
            continue
        parts = line.split("|", 4)
        if len(parts) < 5:
            continue
        sha, author_date, commit_date, author_email, subject = parts

        pr_match = PR_NUM_RE.search(subject)
        if not pr_match:
            continue
        pr_number = int(pr_match.group(1))

        jira_keys = sorted(set(JIRA_RE.findall(subject)))

        first_commit_at = _get_first_commit_date(repo_path, sha, author_date, commit_date)

        store.upsert_pr(repo_name, {
            "number": pr_number,
            "title": subject,
            "author": author_email.split("@")[0],
            "created_at": first_commit_at or author_date,
            "merged_at": commit_date,
            "first_commit_at": first_commit_at,
            "base_branch": branch,
            "additions": 0,
            "deletions": 0,
            "jira_keys": jira_keys,
        })
        count += 1

    log.info("Collected %d PRs from git log on %s", count, branch)
    return count


def _get_first_commit_date(
    repo_path: Path, sha: str, author_date: str, commit_date: str,
) -> str | None:
    """Estimate when work on a PR started.

    For squash merges (1 parent): GitHub preserves the author date as the
    date of the first commit on the topic branch. If author_date != commit_date
    that gives us the cycle time.

    For merge commits (2+ parents): walk from the merge-base to the branch tip
    and return the author date of the earliest commit.
    """
    parents = _git(repo_path, "rev-list", "--parents", "-1", sha)
    parent_shas = parents.split()[1:]

    if len(parent_shas) < 2:
        # Squash merge.  If author date differs from committer date, the
        # author date approximates the first commit on the topic branch.
        if author_date != commit_date:
            return author_date
        # Dates are identical -- can't distinguish, return author_date anyway
        # (will show as ~0h cycle time, which is filtered in lead_time.py)
        return author_date

    # True merge commit: walk the topic branch to find the earliest commit
    first_parent, branch_tip = parent_shas[0], parent_shas[1]
    merge_base = _git(repo_path, "merge-base", first_parent, branch_tip)
    if not merge_base:
        return author_date

    earliest = _git(
        repo_path, "log", "--reverse", "--format=%aI",
        f"{merge_base}..{branch_tip}", "--",
    )
    if earliest:
        return earliest.split("\n")[0].strip()

    return author_date
