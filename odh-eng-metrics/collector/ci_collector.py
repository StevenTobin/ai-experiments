"""Collect CI build data from the OpenShift CI Observability stack (VictoriaMetrics).

Queries VictoriaMetrics for build-level data (duration, pass/fail) and stores
per-build results in the local SQLite store.  The CI Observability stack
(openshift-ci-observability) must be running and have scraped the target repo.

Also collects step-level data (which steps failed, durations) and failure
messages from VictoriaLogs for enriched engineering intelligence.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import httpx

from store.db import Store

log = logging.getLogger(__name__)

INFRA_STEP_PATTERNS = [
    "ipi-install", "ipi-deprovision", "baremetalds",
    "gather-", "lease-", "cluster-pool", "clusterclaim", "cucushift-pre",
    "hypershift-install", "openshift-cluster-bot-rbac",
    "projectdirectoryimagebuild", "inputimagetag",
    "importrelease", "bundlesource",
]

DEFAULT_INGEST_WAIT = 180  # seconds to wait for scraper to ingest data
POLL_INTERVAL = 10         # seconds between checks


def _epoch(lookback_days: int) -> int:
    return int((datetime.now(timezone.utc) - timedelta(days=lookback_days)).timestamp())


def _promql_query(client: httpx.Client, vm_url: str, query: str) -> list[dict]:
    """Run a PromQL instant query, return the result vector."""
    resp = client.get(
        f"{vm_url}/api/v1/query",
        params={"query": query},
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json().get("data", {})
    if data.get("resultType") != "vector":
        return []
    return data.get("result", [])


def _label_values(client: httpx.Client, vm_url: str, label: str,
                  selector: str, start: int, end: int) -> set[str]:
    """Query /api/v1/label/<label>/values with a matcher and time range."""
    resp = client.get(
        f"{vm_url}/api/v1/label/{label}/values",
        params={"match[]": selector, "start": start, "end": end},
        timeout=30,
    )
    resp.raise_for_status()
    return set(resp.json().get("data", []))


def _vm_has_any_data(client: httpx.Client, vm_url: str) -> bool:
    """Check whether VictoriaMetrics has any ingested time series."""
    try:
        resp = client.get(
            f"{vm_url}/api/v1/label/__name__/values",
            timeout=10,
        )
        resp.raise_for_status()
        return len(resp.json().get("data", [])) > 0
    except Exception:
        return False


def _wait_for_data(client: httpx.Client, vm_url: str, query: str,
                   max_wait: int) -> list[dict]:
    """Poll a PromQL query until results appear or timeout."""
    waited = 0
    while waited < max_wait:
        remaining = max_wait - waited
        log.info("Waiting for scraper to ingest data... (%ds elapsed, %ds remaining)",
                 waited, remaining)
        time.sleep(POLL_INTERVAL)
        waited += POLL_INTERVAL
        try:
            results = _promql_query(client, vm_url, query)
            if results:
                log.info("CI data available after %ds", waited)
                return results
        except Exception:
            pass
    return []


def _is_infra_step(step_name: str) -> bool:
    """Classify a step as infrastructure (provisioning, teardown, etc.)."""
    lower = step_name.lower()
    return any(pat in lower for pat in INFRA_STEP_PATTERNS)


def _promql_query_long(client: httpx.Client, vm_url: str, query: str) -> list[dict]:
    """Run a PromQL query with an extended timeout for heavy aggregations."""
    resp = client.get(
        f"{vm_url}/api/v1/query",
        params={"query": query},
        timeout=300,
    )
    resp.raise_for_status()
    data = resp.json().get("data", {})
    if data.get("resultType") != "vector":
        return []
    return data.get("result", [])


def _collect_step_data(client: httpx.Client, vm_url: str,
                       org: str, repo: str, lookback_spec: str,
                       store: Store) -> int:
    """Fetch per-build step-level data from VictoriaMetrics.

    Uses two targeted queries instead of one unfiltered scan:
    1. Failed steps only (level="Error") -- small result set
    2. Step durations aggregated by (build_id, source) -- for time breakdown
    """
    count = 0
    fail_results: list[dict] = []

    # Query 1: failed steps only (small result set)
    fail_query = (
        f'max by (build_id, source) '
        f'(last_over_time(ci_step_duration_seconds'
        f'{{org="{org}",repo="{repo}",level="Error"}}[{lookback_spec}]))'
    )
    try:
        log.info("Querying failed steps...")
        fail_results = _promql_query_long(client, vm_url, fail_query)
        log.info("Got %d failed step series", len(fail_results))
        for r in fail_results:
            metric = r.get("metric", {})
            bid = metric.get("build_id", "")
            step = metric.get("source", "")
            if not bid or not step:
                continue
            try:
                duration = float(r["value"][1])
            except (IndexError, ValueError, TypeError):
                duration = None
            store.upsert_build_step(
                build_id=bid, step_name=step, duration_seconds=duration,
                level="Error", is_infra=_is_infra_step(step),
            )
            count += 1
    except Exception:
        log.warning("Could not fetch failed step data", exc_info=True)

    # Query 2: step durations (aggregated to reduce cardinality)
    # Uses instant query (no range) so VM only returns the latest sample per series.
    dur_query = (
        f'max by (build_id, source) '
        f'(ci_step_duration_seconds'
        f'{{org="{org}",repo="{repo}"}})'
    )
    try:
        log.info("Querying step durations...")
        dur_results = _promql_query_long(client, vm_url, dur_query)
        log.info("Got %d step duration series", len(dur_results))

        # Build a set of (build_id, step) already stored from the Error query
        # to avoid overwriting failure-level entries and avoid N+1 DB lookups.
        existing_keys: set[tuple[str, str]] = set()
        for r in fail_results:
            m = r.get("metric", {})
            existing_keys.add((m.get("build_id", ""), m.get("source", "")))

        for r in dur_results:
            metric = r.get("metric", {})
            bid = metric.get("build_id", "")
            step = metric.get("source", "")
            if not bid or not step:
                continue
            if (bid, step) in existing_keys:
                continue
            try:
                duration = float(r["value"][1])
            except (IndexError, ValueError, TypeError):
                duration = None
            store.upsert_build_step(
                build_id=bid, step_name=step, duration_seconds=duration,
                level=None, is_infra=_is_infra_step(step),
            )
            count += 1
    except Exception:
        log.warning("Could not fetch step duration data", exc_info=True)

    log.info("Stored %d build step records", count)
    return count


def _collect_failure_messages(client: httpx.Client, vl_url: str,
                              org: str, repo: str, lookback_days: int,
                              store: Store) -> int:
    """Query VictoriaLogs for failure messages per build.

    Looks for JUnit step failures and ci-operator errors, groups by
    build_id and message.
    """
    query = (
        f'org:"{org}" AND repo:"{repo}" AND '
        f'(status:"failed" OR level:"error") AND '
        f'(source:"junit_step" OR source:"ci-operator") '
        f'| stats by (build_id, source, _msg) count() as cnt'
    )
    try:
        resp = client.get(
            f"{vl_url}/select/logsql/stats_query",
            params={"query": query, "time": f"{lookback_days}d"},
            timeout=120,
        )
        resp.raise_for_status()
        body = resp.json()
        results = body.get("data", {}).get("result", [])
    except httpx.ConnectError:
        log.info("VictoriaLogs not reachable at %s -- skipping failure message collection", vl_url)
        return 0
    except Exception:
        log.debug("Could not fetch failure messages from VictoriaLogs", exc_info=True)
        return 0

    count = 0
    for item in results:
        metric = item.get("metric", {})
        bid = metric.get("build_id", "")
        msg = metric.get("_msg", "")
        source = metric.get("source", "")
        value = item.get("value", [])
        cnt = int(float(value[1])) if len(value) >= 2 else 1
        if not bid or not msg:
            continue
        store.upsert_build_failure_message(
            build_id=bid,
            message=msg[:500],
            source=source,
            count=cnt,
        )
        count += 1

    log.info("Stored %d failure message records", count)
    return count


def collect_ci_builds(store: Store, cfg: dict, lookback_days: int = 365) -> int:
    """Fetch CI build data from VictoriaMetrics and store per-build results.

    Uses three lightweight API calls:
    1. PromQL aggregation for build discovery + pipeline duration
    2. Label values query for builds with JUnit step failures
    3. Label values query for all builds with JUnit data

    If VictoriaMetrics is reachable but empty (scraper just started), waits
    up to ``ingest_wait`` seconds for data to appear before giving up.

    Returns the number of builds stored.
    """
    ci_cfg = cfg.get("ci_observability", {})
    if not ci_cfg.get("enabled", True):
        log.info("CI Observability integration disabled in config")
        return 0

    vm_url = ci_cfg.get("vm_url", "http://localhost:8428")
    ingest_wait = ci_cfg.get("ingest_wait", DEFAULT_INGEST_WAIT)
    org = cfg["upstream"]["owner"]
    repo = cfg["upstream"]["repo"]

    start_epoch = _epoch(lookback_days)
    end_epoch = int(datetime.now(timezone.utc).timestamp())
    lookback_spec = f"{lookback_days}d"

    try:
        with httpx.Client() as client:
            # 1. Discover builds + pipeline duration via PromQL aggregation.
            #    ci_step_relative_end_seconds is emitted for every event with
            #    from/to timestamps; the max value per build = pipeline duration.
            query = (
                f'max by (build_id, pr_number, job_name) '
                f'(max_over_time(ci_step_relative_end_seconds'
                f'{{org="{org}",repo="{repo}"}}[{lookback_spec}]))'
            )
            results = _promql_query(client, vm_url, query)

            if not results:
                if _vm_has_any_data(client, vm_url):
                    # VM has data from some repo, but not ours.  The scraper
                    # is probably configured for a different REPO -- no point waiting.
                    log.info(
                        "VictoriaMetrics has data but none for %s/%s. "
                        "Verify the scraper .env has REPO=%s/%s.",
                        org, repo, org, repo,
                    )
                    return 0

                # VM is completely empty -- scraper likely just started.
                log.info(
                    "VictoriaMetrics is empty -- the scraper is probably still "
                    "ingesting. Will poll for up to %ds.", ingest_wait,
                )
                results = _wait_for_data(client, vm_url, query, ingest_wait)
                if not results:
                    log.info(
                        "No CI data appeared after %ds. The backfill scraper may "
                        "still be running -- re-run 'make collect' in a few minutes.",
                        ingest_wait,
                    )
                    return 0

            builds: dict[str, dict] = {}
            for r in results:
                metric = r.get("metric", {})
                bid = metric.get("build_id", "")
                pr = metric.get("pr_number", "")
                job = metric.get("job_name", "")
                if not bid or not pr:
                    continue
                try:
                    duration = float(r["value"][1])
                except (IndexError, ValueError, TypeError):
                    duration = 0.0
                builds[bid] = {
                    "pr_number": pr,
                    "job_name": job,
                    "duration": duration,
                }

            log.info("Found %d CI builds from VictoriaMetrics", len(builds))

            # 2. Get build start timestamps.
            #    MetricsQL timestamp() returns the metric's sample timestamp
            #    as a float value -- the scraper attaches the pipeline start
            #    time to each sample.
            ts_query = (
                f'max by (build_id) '
                f'(timestamp(ci_step_relative_end_seconds'
                f'{{org="{org}",repo="{repo}"}}[{lookback_spec}]))'
            )
            try:
                ts_results = _promql_query(client, vm_url, ts_query)
                for r in ts_results:
                    bid = r.get("metric", {}).get("build_id", "")
                    if bid in builds:
                        try:
                            epoch = float(r["value"][1])
                            builds[bid]["started_at"] = (
                                datetime.fromtimestamp(epoch, tz=timezone.utc)
                                .strftime("%Y-%m-%dT%H:%M:%SZ")
                            )
                        except (IndexError, ValueError, TypeError):
                            pass
                log.info("Resolved timestamps for %d builds",
                         sum(1 for b in builds.values() if b.get("started_at")))
            except Exception:
                log.warning("Could not fetch build timestamps; weekly trends "
                            "will fall back to PR merge dates", exc_info=True)

            # 3. Identify builds with JUnit step failures.
            selector_failed = (
                f'ci_junit_step_duration_seconds'
                f'{{org="{org}",repo="{repo}",status="failed"}}'
            )
            failed_bids = _label_values(
                client, vm_url, "build_id", selector_failed,
                start_epoch, end_epoch,
            )

            # 4. Identify all builds that have any JUnit data (for success vs unknown).
            selector_any = (
                f'ci_junit_step_duration_seconds'
                f'{{org="{org}",repo="{repo}"}}'
            )
            junit_bids = _label_values(
                client, vm_url, "build_id", selector_any,
                start_epoch, end_epoch,
            )

            # 5. Fetch per-build resource usage (best-effort).
            resource_data: dict[str, dict] = {}
            try:
                cpu_query = (
                    f'max by (build_id) '
                    f'(ci_test_cluster_cluster_cpu_usage_cores_sum'
                    f'{{org="{org}",repo="{repo}"}})'
                )
                for r in _promql_query(client, vm_url, cpu_query):
                    bid = r.get("metric", {}).get("build_id", "")
                    if bid:
                        resource_data.setdefault(bid, {})["peak_cpu_cores"] = float(r["value"][1])

                mem_query = (
                    f'max by (build_id) '
                    f'(ci_test_cluster_cluster_memory_usage_bytes_sum'
                    f'{{org="{org}",repo="{repo}"}})'
                )
                for r in _promql_query(client, vm_url, mem_query):
                    bid = r.get("metric", {}).get("build_id", "")
                    if bid:
                        resource_data.setdefault(bid, {})["peak_memory_bytes"] = float(r["value"][1])

                step_query = (
                    f'max by (build_id) '
                    f'(ci_step_relative_end_seconds'
                    f'{{org="{org}",repo="{repo}"}})'
                )
                for r in _promql_query(client, vm_url, step_query):
                    bid = r.get("metric", {}).get("build_id", "")
                    if bid:
                        resource_data.setdefault(bid, {})["total_step_seconds"] = float(r["value"][1])

                if resource_data:
                    log.info("Fetched resource data for %d builds", len(resource_data))
            except Exception:
                log.debug("Could not fetch resource usage metrics", exc_info=True)

            # 6. Determine result per build and store.
            count = 0
            n_fail = 0
            n_pass = 0
            for bid, info in builds.items():
                if bid in failed_bids:
                    result = "failure"
                    n_fail += 1
                elif bid in junit_bids:
                    result = "success"
                    n_pass += 1
                else:
                    result = "unknown"

                res = resource_data.get(bid, {})
                store.upsert_ci_build(
                    build_id=bid,
                    pr_number=int(info["pr_number"]),
                    job_name=info["job_name"],
                    duration_seconds=round(info["duration"], 1),
                    result=result,
                    started_at=info.get("started_at"),
                    peak_cpu_cores=res.get("peak_cpu_cores"),
                    peak_memory_bytes=res.get("peak_memory_bytes"),
                    total_step_seconds=res.get("total_step_seconds"),
                )
                count += 1

            log.info("Stored %d CI builds: %d success, %d failure, %d unknown",
                     count, n_pass, n_fail, count - n_pass - n_fail)

            if ci_cfg.get("collect_steps", True):
                log.info("Collecting step-level data...")
                _collect_step_data(client, vm_url, org, repo, lookback_spec, store)

            if ci_cfg.get("collect_failure_messages", True):
                vl_url = ci_cfg.get("vl_url", "http://localhost:9428")
                log.info("Collecting failure messages from VictoriaLogs...")
                _collect_failure_messages(client, vl_url, org, repo, lookback_days, store)

            return count

    except httpx.ConnectError:
        log.warning(
            "Cannot reach VictoriaMetrics at %s -- is the CI Observability stack running? "
            "Skipping CI data collection. Start it with 'make up' in openshift-ci-observability.",
            vm_url,
        )
        return 0
    except Exception:
        log.warning("CI build collection failed", exc_info=True)
        return 0
