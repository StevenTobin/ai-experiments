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

CREATE TABLE IF NOT EXISTS ci_builds (
    build_id    TEXT PRIMARY KEY,
    pr_number   INTEGER NOT NULL,
    job_name    TEXT NOT NULL,
    duration_seconds REAL,
    result      TEXT NOT NULL DEFAULT 'unknown',
    started_at  TEXT
);

CREATE TABLE IF NOT EXISTS code_risk_scores (
    repo        TEXT NOT NULL,
    file        TEXT NOT NULL,
    function    TEXT NOT NULL,
    component   TEXT,
    complexity  REAL,
    churn_30d   INTEGER,
    risk_score  REAL,
    risk_band   TEXT,
    analyzed_at TEXT NOT NULL,
    PRIMARY KEY (repo, file, function)
);

CREATE TABLE IF NOT EXISTS ci_build_steps (
    build_id    TEXT NOT NULL,
    step_name   TEXT NOT NULL,
    duration_seconds REAL,
    level       TEXT,
    is_infra    INTEGER DEFAULT 0,
    PRIMARY KEY (build_id, step_name)
);

CREATE TABLE IF NOT EXISTS ci_build_failure_messages (
    build_id    TEXT NOT NULL,
    message     TEXT NOT NULL,
    source      TEXT,
    count       INTEGER DEFAULT 1,
    PRIMARY KEY (build_id, message)
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
        self._migrate()

    def _migrate(self) -> None:
        """Add columns introduced after initial schema."""
        migrations = [
            ("ci_builds", "started_at", "TEXT"),
            ("merged_prs", "merge_sha", "TEXT"),
            ("merged_prs", "is_ai_assisted", "INTEGER DEFAULT 0"),
            ("merged_prs", "changed_files", "TEXT"),
            ("merged_prs", "changed_components", "TEXT"),
            ("reverts", "reverted_pr", "INTEGER"),
            ("ci_builds", "peak_cpu_cores", "REAL"),
            ("ci_builds", "peak_memory_bytes", "REAL"),
            ("ci_builds", "total_step_seconds", "REAL"),
        ]
        for table, column, col_type in migrations:
            try:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
                self.conn.commit()
            except sqlite3.OperationalError:
                pass

    def upsert_release(self, tag: str, published: str, prerelease: bool, is_patch: bool, is_ea: bool) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO releases (tag, published, prerelease, is_patch, is_ea) VALUES (?, ?, ?, ?, ?)",
            (tag, published, int(prerelease), int(is_patch), int(is_ea)),
        )
        self.conn.commit()

    def upsert_pr(self, repo: str, pr: dict) -> None:
        jira_keys = json.dumps(pr.get("jira_keys", []))
        changed_files = json.dumps(pr.get("changed_files", []))
        changed_components = json.dumps(pr.get("changed_components", []))
        self.conn.execute(
            """INSERT OR REPLACE INTO merged_prs
               (repo, number, title, author, created_at, merged_at, first_commit_at,
                base_branch, additions, deletions, jira_keys,
                merge_sha, is_ai_assisted, changed_files, changed_components)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                repo, pr["number"], pr.get("title"), pr.get("author"),
                pr.get("created_at"), pr["merged_at"], pr.get("first_commit_at"),
                pr.get("base_branch"), pr.get("additions", 0), pr.get("deletions", 0),
                jira_keys, pr.get("merge_sha"), int(pr.get("is_ai_assisted", False)),
                changed_files, changed_components,
            ),
        )
        self.conn.commit()

    def upsert_revert(self, repo: str, sha: str, date: str, reverted_sha: str | None,
                       message: str, reverted_pr: int | None = None) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO reverts
               (repo, sha, date, reverted_sha, message, reverted_pr)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (repo, sha, date, reverted_sha, message, reverted_pr),
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

    def upsert_ci_build(self, build_id: str, pr_number: int, job_name: str,
                        duration_seconds: float | None, result: str,
                        started_at: str | None = None,
                        peak_cpu_cores: float | None = None,
                        peak_memory_bytes: float | None = None,
                        total_step_seconds: float | None = None) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO ci_builds
               (build_id, pr_number, job_name, duration_seconds, result, started_at,
                peak_cpu_cores, peak_memory_bytes, total_step_seconds)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (build_id, pr_number, job_name, duration_seconds, result, started_at,
             peak_cpu_cores, peak_memory_bytes, total_step_seconds),
        )
        self.conn.commit()

    def get_ci_builds(self, pr_number: int | None = None) -> list[dict]:
        q = "SELECT * FROM ci_builds"
        params: list[Any] = []
        if pr_number is not None:
            q += " WHERE pr_number = ?"
            params.append(pr_number)
        q += " ORDER BY build_id"
        return [dict(r) for r in self.conn.execute(q, params).fetchall()]

    def get_ci_build_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) as n FROM ci_builds").fetchone()
        return row["n"] if row else 0

    def upsert_build_step(self, build_id: str, step_name: str,
                          duration_seconds: float | None, level: str | None,
                          is_infra: bool = False) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO ci_build_steps
               (build_id, step_name, duration_seconds, level, is_infra)
               VALUES (?, ?, ?, ?, ?)""",
            (build_id, step_name, duration_seconds, level, int(is_infra)),
        )
        self.conn.commit()

    def upsert_build_failure_message(self, build_id: str, message: str,
                                     source: str | None = None,
                                     count: int = 1) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO ci_build_failure_messages
               (build_id, message, source, count) VALUES (?, ?, ?, ?)""",
            (build_id, message[:500], source, count),
        )
        self.conn.commit()

    def get_build_steps(self, build_id: str | None = None,
                        level: str | None = None) -> list[dict]:
        q = "SELECT * FROM ci_build_steps WHERE 1=1"
        params: list[Any] = []
        if build_id:
            q += " AND build_id = ?"
            params.append(build_id)
        if level:
            q += " AND level = ?"
            params.append(level)
        q += " ORDER BY build_id, step_name"
        return [dict(r) for r in self.conn.execute(q, params).fetchall()]

    def get_build_failure_messages(self, build_id: str | None = None) -> list[dict]:
        q = "SELECT * FROM ci_build_failure_messages WHERE 1=1"
        params: list[Any] = []
        if build_id:
            q += " AND build_id = ?"
            params.append(build_id)
        q += " ORDER BY build_id, count DESC"
        return [dict(r) for r in self.conn.execute(q, params).fetchall()]

    def get_all_build_steps(self) -> list[dict]:
        """Get all build steps, optimized for batch processing."""
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM ci_build_steps ORDER BY build_id, step_name"
        ).fetchall()]

    def get_all_build_failure_messages(self) -> list[dict]:
        """Get all failure messages, optimized for batch processing."""
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM ci_build_failure_messages ORDER BY build_id, count DESC"
        ).fetchall()]

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

    def upsert_code_risk(self, repo: str, file: str, function: str,
                         component: str | None, complexity: float | None,
                         churn_30d: int | None, risk_score: float | None,
                         risk_band: str | None, analyzed_at: str) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO code_risk_scores
               (repo, file, function, component, complexity, churn_30d,
                risk_score, risk_band, analyzed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (repo, file, function, component, complexity, churn_30d,
             risk_score, risk_band, analyzed_at),
        )
        self.conn.commit()

    def get_code_risk_scores(self, repo: str | None = None,
                             component: str | None = None) -> list[dict]:
        q = "SELECT * FROM code_risk_scores WHERE 1=1"
        params: list[Any] = []
        if repo:
            q += " AND repo = ?"
            params.append(repo)
        if component:
            q += " AND component = ?"
            params.append(component)
        q += " ORDER BY risk_score DESC"
        return [dict(r) for r in self.conn.execute(q, params).fetchall()]

    def get_component_risk_summary(self) -> list[dict]:
        """Aggregate risk scores by component."""
        rows = self.conn.execute(
            """SELECT component,
                      COUNT(*) as total_functions,
                      SUM(CASE WHEN risk_band = 'Critical' THEN 1 ELSE 0 END) as critical,
                      SUM(CASE WHEN risk_band = 'High' THEN 1 ELSE 0 END) as high,
                      AVG(risk_score) as avg_risk
               FROM code_risk_scores
               WHERE component IS NOT NULL
               GROUP BY component
               ORDER BY avg_risk DESC"""
        ).fetchall()
        return [dict(r) for r in rows]

    def get_metric(self, metric: str, window: str) -> Any | None:
        row = self.conn.execute(
            "SELECT value FROM metrics_cache WHERE metric = ? AND window = ?",
            (metric, window),
        ).fetchone()
        return json.loads(row["value"]) if row else None

    def close(self) -> None:
        self.conn.close()
