"""SQLite storage for collected events and computed metrics."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS releases (
    tag         TEXT PRIMARY KEY,
    published   TEXT NOT NULL,
    prerelease  INTEGER NOT NULL DEFAULT 0,
    is_patch    INTEGER NOT NULL DEFAULT 0,
    is_ea       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS merged_prs (
    repo        TEXT NOT NULL,
    number      INTEGER NOT NULL,
    title       TEXT,
    author      TEXT,
    created_at  TEXT,
    merged_at   TEXT NOT NULL,
    first_commit_at TEXT,
    base_branch TEXT,
    additions   INTEGER DEFAULT 0,
    deletions   INTEGER DEFAULT 0,
    jira_keys   TEXT,
    PRIMARY KEY (repo, number)
);

CREATE TABLE IF NOT EXISTS reverts (
    repo        TEXT NOT NULL,
    sha         TEXT PRIMARY KEY,
    date        TEXT NOT NULL,
    reverted_sha TEXT,
    message     TEXT
);

CREATE TABLE IF NOT EXISTS cherry_picks (
    repo        TEXT NOT NULL,
    pr_number   INTEGER NOT NULL,
    target_branch TEXT NOT NULL,
    title       TEXT,
    author      TEXT,
    merged_at   TEXT,
    PRIMARY KEY (repo, pr_number)
);

CREATE TABLE IF NOT EXISTS downstream_branches (
    name        TEXT PRIMARY KEY,
    first_commit_date TEXT,
    is_ea       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS branch_arrivals (
    pr_repo     TEXT NOT NULL,
    pr_number   INTEGER NOT NULL,
    branch      TEXT NOT NULL,
    arrived_at  TEXT,
    PRIMARY KEY (pr_repo, pr_number, branch)
);

CREATE TABLE IF NOT EXISTS ai_assisted_commits (
    repo        TEXT NOT NULL,
    sha         TEXT NOT NULL,
    date        TEXT NOT NULL,
    message     TEXT,
    tool        TEXT NOT NULL,
    PRIMARY KEY (repo, sha, tool)
);

CREATE TABLE IF NOT EXISTS metrics_cache (
    metric      TEXT NOT NULL,
    window      TEXT NOT NULL,
    value       TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (metric, window)
);
"""


class Store:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def upsert_release(self, tag: str, published: str, prerelease: bool, is_patch: bool, is_ea: bool) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO releases (tag, published, prerelease, is_patch, is_ea) VALUES (?, ?, ?, ?, ?)",
            (tag, published, int(prerelease), int(is_patch), int(is_ea)),
        )
        self.conn.commit()

    def upsert_pr(self, repo: str, pr: dict) -> None:
        jira_keys = json.dumps(pr.get("jira_keys", []))
        self.conn.execute(
            """INSERT OR REPLACE INTO merged_prs
               (repo, number, title, author, created_at, merged_at, first_commit_at, base_branch, additions, deletions, jira_keys)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                repo, pr["number"], pr.get("title"), pr.get("author"),
                pr.get("created_at"), pr["merged_at"], pr.get("first_commit_at"),
                pr.get("base_branch"), pr.get("additions", 0), pr.get("deletions", 0),
                jira_keys,
            ),
        )
        self.conn.commit()

    def upsert_revert(self, repo: str, sha: str, date: str, reverted_sha: str | None, message: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO reverts (repo, sha, date, reverted_sha, message) VALUES (?, ?, ?, ?, ?)",
            (repo, sha, date, reverted_sha, message),
        )
        self.conn.commit()

    def upsert_cherry_pick(self, repo: str, pr_number: int, target_branch: str,
                           title: str, author: str, merged_at: str | None) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO cherry_picks
               (repo, pr_number, target_branch, title, author, merged_at) VALUES (?, ?, ?, ?, ?, ?)""",
            (repo, pr_number, target_branch, title, author, merged_at),
        )
        self.conn.commit()

    def upsert_downstream_branch(self, name: str, first_commit_date: str | None, is_ea: bool) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO downstream_branches (name, first_commit_date, is_ea) VALUES (?, ?, ?)",
            (name, first_commit_date, int(is_ea)),
        )
        self.conn.commit()

    def upsert_branch_arrival(self, pr_repo: str, pr_number: int, branch: str, arrived_at: str | None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO branch_arrivals (pr_repo, pr_number, branch, arrived_at) VALUES (?, ?, ?, ?)",
            (pr_repo, pr_number, branch, arrived_at),
        )
        self.conn.commit()

    def upsert_ai_commit(self, repo: str, sha: str, date: str, message: str, tool: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO ai_assisted_commits (repo, sha, date, message, tool) VALUES (?, ?, ?, ?, ?)",
            (repo, sha, date, message[:200], tool),
        )
        self.conn.commit()

    def get_ai_commits(self, repo: str | None = None) -> list[dict]:
        q = "SELECT * FROM ai_assisted_commits"
        params: list[Any] = []
        if repo:
            q += " WHERE repo = ?"
            params.append(repo)
        q += " ORDER BY date"
        return [dict(r) for r in self.conn.execute(q, params).fetchall()]

    def save_metric(self, metric: str, window: str, value: Any) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO metrics_cache (metric, window, value, computed_at) VALUES (?, ?, ?, ?)",
            (metric, window, json.dumps(value), datetime.utcnow().isoformat()),
        )
        self.conn.commit()

    def get_releases(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM releases ORDER BY published").fetchall()
        return [dict(r) for r in rows]

    def get_merged_prs(self, repo: str | None = None, base_branch: str | None = None) -> list[dict]:
        q = "SELECT * FROM merged_prs WHERE 1=1"
        params: list[Any] = []
        if repo:
            q += " AND repo = ?"
            params.append(repo)
        if base_branch:
            q += " AND base_branch = ?"
            params.append(base_branch)
        q += " ORDER BY merged_at"
        rows = self.conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]

    def get_reverts(self, repo: str | None = None) -> list[dict]:
        q = "SELECT * FROM reverts"
        params: list[Any] = []
        if repo:
            q += " WHERE repo = ?"
            params.append(repo)
        q += " ORDER BY date"
        return [dict(r) for r in self.conn.execute(q, params).fetchall()]

    def get_cherry_picks(self, repo: str | None = None) -> list[dict]:
        q = "SELECT * FROM cherry_picks"
        params: list[Any] = []
        if repo:
            q += " WHERE repo = ?"
            params.append(repo)
        q += " ORDER BY merged_at"
        return [dict(r) for r in self.conn.execute(q, params).fetchall()]

    def get_downstream_branches(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM downstream_branches ORDER BY first_commit_date"
        ).fetchall()]

    def get_branch_arrivals(self, pr_repo: str, pr_number: int) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM branch_arrivals WHERE pr_repo = ? AND pr_number = ?",
            (pr_repo, pr_number),
        ).fetchall()]

    def get_metric(self, metric: str, window: str) -> Any | None:
        row = self.conn.execute(
            "SELECT value FROM metrics_cache WHERE metric = ? AND window = ?",
            (metric, window),
        ).fetchone()
        return json.loads(row["value"]) if row else None

    def close(self) -> None:
        self.conn.close()
