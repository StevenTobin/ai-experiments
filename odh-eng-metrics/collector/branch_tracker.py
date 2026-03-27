"""Track when commits from upstream main reach other branches and tags.

For fast-forwarded branches (stable, rhoai), using the commit's own date
gives 0h lead time since it's the same commit.  Instead we find the earliest
tag reachable from each branch that contains the commit, and use that tag's
tagger/commit date as the "arrived at" timestamp.  This measures how long it
took for the branch to advance past a given commit.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from store.db import Store

log = logging.getLogger(__name__)


def _git(repo_path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", f"--git-dir={repo_path}", *args],
        capture_output=True, text=True, timeout=120,
    )
    return result.stdout.strip()


def _ref_exists(repo_path: Path, ref: str) -> bool:
    result = subprocess.run(
        ["git", f"--git-dir={repo_path}", "rev-parse", "--verify", ref],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def _resolve_ref(repo_path: Path, branch: str) -> str | None:
    for candidate in [branch, f"origin/{branch}"]:
        if _ref_exists(repo_path, candidate):
            return candidate
    return None


def _commit_in_ref(repo_path: Path, sha: str, ref: str) -> bool:
    result = subprocess.run(
        ["git", f"--git-dir={repo_path}", "merge-base", "--is-ancestor", sha, ref],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def _tags_containing(repo_path: Path, sha: str, pattern: str | None = None) -> list[tuple[str, str]]:
    """Return list of (tag_name, tag_date) for tags containing sha, oldest first."""
    args = ["tag", "--contains", sha, "--sort=creatordate",
            "--format=%(refname:short) %(creatordate:iso-strict)"]
    if pattern:
        args.extend(["-l", pattern])
    output = _git(repo_path, *args)
    if not output:
        return []
    results = []
    for line in output.split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split(" ", 1)
        if len(parts) == 2:
            results.append((parts[0], parts[1]))
    return results


def _earliest_tag_on_branch(
    repo_path: Path, sha: str, branch_ref: str, tag_pattern: str = "v*"
) -> tuple[str | None, str | None]:
    """Find the earliest tag reachable from branch_ref that contains sha."""
    if not _commit_in_ref(repo_path, sha, branch_ref):
        return None, None

    all_tags = _tags_containing(repo_path, sha, tag_pattern)
    for tag_name, tag_date in all_tags:
        if _commit_in_ref(repo_path, tag_name, branch_ref):
            return tag_name, tag_date

    return None, None


def track_pr_propagation(
    store: Store,
    upstream_path: Path,
    cfg: dict,
    limit: int = 500,
) -> int:
    """For recent upstream PRs, track when their merge commits reached stable, rhoai, and a release tag."""
    repo_name = f"{cfg['upstream']['owner']}/{cfg['upstream']['repo']}"
    prs = store.get_merged_prs(repo=repo_name, base_branch="main")
    if not prs:
        return 0

    recent_prs = prs[-limit:]
    count = 0

    # Pre-resolve branch refs once.
    branch_refs: dict[str, str] = {}
    for branch in ["stable", "rhoai"]:
        ref = _resolve_ref(upstream_path, branch)
        if ref:
            branch_refs[branch] = ref

    for i, pr in enumerate(recent_prs):
        # Use stored merge_sha if available; fall back to expensive search.
        merge_sha = pr.get("merge_sha") or _find_merge_sha(upstream_path, pr)
        if not merge_sha:
            continue

        if (i + 1) % 50 == 0:
            log.info("Branch tracking progress: %d/%d PRs", i + 1, len(recent_prs))

        for branch, ref in branch_refs.items():
            if not _commit_in_ref(upstream_path, merge_sha, ref):
                continue
            tag_name, tag_date = _earliest_tag_on_branch(
                upstream_path, merge_sha, ref,
            )
            if tag_date:
                store.upsert_branch_arrival(repo_name, pr["number"], branch, tag_date)
                count += 1

        # Direct tag arrival: the earliest tag anywhere containing this commit
        all_tags = _tags_containing(upstream_path, merge_sha, "v*")
        if all_tags:
            first_tag, first_date = all_tags[0]
            store.upsert_branch_arrival(repo_name, pr["number"], f"tag:{first_tag}", first_date)
            count += 1

    log.info("Tracked %d branch arrivals for %d PRs", count, len(recent_prs))
    return count


def _find_merge_sha(repo_path: Path, pr: dict) -> str | None:
    """Find the merge commit SHA on main that corresponds to a PR."""
    merged_at = pr.get("merged_at")
    if not merged_at:
        return None
    pr_num = pr["number"]

    main_ref = _resolve_ref(repo_path, "main")
    if not main_ref:
        return None

    # Search in a wider window around the merge date
    date_prefix = merged_at[:10]
    output = _git(
        repo_path, "log", main_ref,
        f"--since={date_prefix}", f"--until={date_prefix}T23:59:59Z",
        "--format=%H %s", "--all",
    )
    for line in output.split("\n"):
        if not line.strip():
            continue
        sha, *msg_parts = line.split(" ", 1)
        msg = msg_parts[0] if msg_parts else ""
        if f"#{pr_num}" in msg or f"#{pr_num})" in msg:
            return sha

    # Broader search: try a 3-day window
    output = _git(
        repo_path, "log", main_ref,
        f"--since={date_prefix}", "--format=%H %s",
        "-50",
    )
    for line in output.split("\n"):
        if not line.strip():
            continue
        sha, *msg_parts = line.split(" ", 1)
        msg = msg_parts[0] if msg_parts else ""
        if f"#{pr_num}" in msg or f"#{pr_num})" in msg:
            return sha

    return None
