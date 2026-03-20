from kfp import dsl

@dsl.component(
    base_image="registry.access.redhat.com/ubi9/python-311:latest"
)
def analyze_issues(health_data: str, supplementary_data: str) -> str:
    """Deterministic issue analysis: extracts, categorizes, and cross-references issues from structured health data."""
    import json
    from datetime import datetime, timezone

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    def make_issue(severity, issue_id, section, summary, details=None):
        return {
            "id": issue_id,
            "severity": severity,
            "section": section,
            "summary": summary,
            "details": details or {},
            "correlated_signals": []
        }

    def pod_summary(pod):
        return {
            "name": pod.get("name", ""),
            "phase": pod.get("phase", ""),
            "containers": pod.get("containers", [])
        }

    health = json.loads(health_data)
    supplementary = json.loads(supplementary_data)

    report_json = health.get("report") or {}
    exit_code = health.get("exit_code", -1)
    health_stderr = health.get("stderr", "")

    issues = []

    # --- Health check tool failure ---
    if report_json is None or exit_code < 0:
        issues.append(make_issue(
            CRITICAL, "health-check-failed", "health_check",
            f"Health check tool failed (exit_code={exit_code})",
            details={"stderr": health_stderr[:1000]}
        ))
        # Build minimal result and return early
        by_sev = {"critical": [i for i in issues if i["severity"] == CRITICAL], "high": [], "medium": [], "low": []}
        return json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "overall_healthy": False,
            "issues_by_severity": by_sev,
            "healthy_sections": [],
            "summary": f"{len(by_sev['critical'])} critical, 0 high, 0 medium, 0 low issues found",
            "operator_version": "unknown"
        })

    # --- Nodes ---
    nodes_section = report_json.get("nodes", {})
    if nodes_section.get("error"):
        issues.append(make_issue(CRITICAL, "nodes-error", "nodes", f"Nodes section error: {nodes_section['error']}"))
    for node in (nodes_section.get("data", {}).get("nodes") or []):
        if node.get("unhealthyReason"):
            issues.append(make_issue(
                CRITICAL, f"node-unhealthy-{node.get('name', 'unknown')}", "nodes",
                f"Node '{node.get('name')}' is unhealthy: {node['unhealthyReason']}",
                details={"conditions": node.get("conditions", []), "allocatable": node.get("allocatable", "")}
            ))

    # --- Operator ---
    operator_section = report_json.get("operator", {})
    if operator_section.get("error"):
        issues.append(make_issue(CRITICAL, "operator-error", "operator", f"Operator section error: {operator_section['error']}"))
    op_data = operator_section.get("data", {})
    op_deployment = op_data.get("deployment")
    if op_deployment:
        ready = op_deployment.get("readyReplicas", 0)
        desired = op_deployment.get("replicas", 1)
        if ready < desired:
            issues.append(make_issue(
                CRITICAL, "operator-not-ready", "operator",
                f"Operator deployment has {ready}/{desired} ready replicas",
                details={"deployment": op_deployment}
            ))

    for dep_op in (op_data.get("dependentOperators") or []):
        if not dep_op.get("installed", True):
            issues.append(make_issue(
                HIGH, f"dependent-op-missing-{dep_op.get('name', 'unknown')}", "operator",
                f"Dependent operator '{dep_op.get('name')}' is not installed",
                details=dep_op
            ))
        elif dep_op.get("error"):
            issues.append(make_issue(
                HIGH, f"dependent-op-error-{dep_op.get('name', 'unknown')}", "operator",
                f"Dependent operator '{dep_op.get('name')}' has error: {dep_op['error']}",
                details=dep_op
            ))

    # --- DSC ---
    dsc_section = report_json.get("dsc", {})
    if dsc_section.get("error"):
        issues.append(make_issue(CRITICAL, "dsc-error", "dsc", f"DSC section error: {dsc_section['error']}"))
    dsc_data = dsc_section.get("data", {})
    for cond in (dsc_data.get("conditions") or []):
        if cond.get("type") == "Available" and cond.get("status") != "True":
            issues.append(make_issue(
                CRITICAL, "dsc-not-available", "dsc",
                f"DSC condition 'Available' is {cond.get('status')}: {cond.get('message', '')}",
                details=cond
            ))
        elif cond.get("type") == "Degraded" and cond.get("status") == "True":
            issues.append(make_issue(
                HIGH, "dsc-degraded", "dsc",
                f"DSC is degraded: {cond.get('message', '')}",
                details=cond
            ))

    # --- DSCI ---
    dsci_section = report_json.get("dsci", {})
    if dsci_section.get("error"):
        issues.append(make_issue(CRITICAL, "dsci-error", "dsci", f"DSCI section error: {dsci_section['error']}"))
    dsci_data = dsci_section.get("data", {})
    for cond in (dsci_data.get("conditions") or []):
        if cond.get("type") == "Available" and cond.get("status") != "True":
            issues.append(make_issue(
                CRITICAL, "dsci-not-available", "dsci",
                f"DSCI condition 'Available' is {cond.get('status')}: {cond.get('message', '')}",
                details=cond
            ))

    # --- Deployments ---
    deploy_section = report_json.get("deployments", {})
    if deploy_section.get("error"):
        issues.append(make_issue(HIGH, "deployments-error", "deployments", f"Deployments section error: {deploy_section['error']}"))
    for ns, deploys in (deploy_section.get("data", {}).get("byNamespace") or {}).items():
        for dep in (deploys or []):
            ready = dep.get("readyReplicas", 0)
            desired = dep.get("replicas", 0)
            if desired > 0 and ready < desired:
                issues.append(make_issue(
                    HIGH, f"deploy-not-ready-{ns}-{dep.get('name', 'unknown')}", "deployments",
                    f"Deployment '{ns}/{dep.get('name')}' has {ready}/{desired} ready replicas",
                    details={"namespace": ns, "name": dep.get("name"), "conditions": dep.get("conditions", [])}
                ))

    # --- Pods ---
    pods_section = report_json.get("pods", {})
    if pods_section.get("error"):
        issues.append(make_issue(HIGH, "pods-error", "pods", f"Pods section error: {pods_section['error']}"))
    for ns, pods in (pods_section.get("data", {}).get("byNamespace") or {}).items():
        for pod in (pods or []):
            phase = pod.get("phase", "")
            if phase in ("Failed", "Unknown"):
                issues.append(make_issue(
                    HIGH, f"pod-failed-{ns}-{pod.get('name', 'unknown')}", "pods",
                    f"Pod '{ns}/{pod.get('name')}' in phase '{phase}'",
                    details=pod_summary(pod)
                ))
            elif phase == "Pending":
                issues.append(make_issue(
                    MEDIUM, f"pod-pending-{ns}-{pod.get('name', 'unknown')}", "pods",
                    f"Pod '{ns}/{pod.get('name')}' is Pending",
                    details=pod_summary(pod)
                ))
            for container in (pod.get("containers") or []):
                if container.get("restartCount", 0) > 3:
                    issues.append(make_issue(
                        HIGH, f"pod-restarts-{ns}-{pod.get('name', 'unknown')}-{container.get('name', '')}", "pods",
                        f"Container '{container.get('name')}' in '{ns}/{pod.get('name')}' has {container['restartCount']} restarts",
                        details={"container": container}
                    ))
                waiting = container.get("waitingReason", "")
                if waiting in ("CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull"):
                    issues.append(make_issue(
                        HIGH, f"pod-waiting-{ns}-{pod.get('name', 'unknown')}-{container.get('name', '')}", "pods",
                        f"Container '{container.get('name')}' in '{ns}/{pod.get('name')}' is in {waiting}",
                        details={"container": container}
                    ))

    # --- Events ---
    events_section = report_json.get("events", {})
    if events_section.get("error"):
        issues.append(make_issue(MEDIUM, "events-error", "events", f"Events section error: {events_section['error']}"))
    warning_events = [evt for evt in (events_section.get("data", {}).get("events") or []) if evt.get("type") == "Warning"]
    if warning_events:
        issues.append(make_issue(
            MEDIUM, "warning-events", "events",
            f"{len(warning_events)} warning event(s) in the last 5 minutes",
            details={"events": warning_events[:20]}
        ))

    # --- Quotas ---
    quotas_section = report_json.get("quotas", {})
    if quotas_section.get("error"):
        issues.append(make_issue(MEDIUM, "quotas-error", "quotas", f"Quotas section error: {quotas_section['error']}"))
    for ns, quotas in (quotas_section.get("data", {}).get("byNamespace") or {}).items():
        for q in (quotas or []):
            if q.get("exceeded"):
                issues.append(make_issue(
                    MEDIUM, f"quota-exceeded-{ns}-{q.get('name', 'unknown')}", "quotas",
                    f"ResourceQuota '{ns}/{q.get('name')}' has exceeded resources: {', '.join(q['exceeded'])}",
                    details={"used": q.get("used", {}), "hard": q.get("hard", {})}
                ))

    # --- Supplementary: resource pressure ---
    pressure = supplementary.get("resource_pressure", {})
    for pod_info in (pressure.get("data") or []):
        for pod_issue in pod_info.get("issues", []):
            existing = any(i["id"].startswith("pod-") and pod_info["pod"] in i["id"] for i in issues)
            if not existing:
                issues.append(make_issue(
                    HIGH, f"pressure-{pod_info['namespace']}-{pod_info['pod']}", "resource_pressure",
                    f"Pod '{pod_info['namespace']}/{pod_info['pod']}': {pod_issue}",
                    details=pod_info
                ))

    # --- Supplementary: operator log errors ---
    log_section = supplementary.get("operator_logs", {})
    error_count = log_section.get("error_warning_count", 0)
    if error_count > 20:
        sample = log_section.get("data", [])[:10]
        issues.append(make_issue(
            MEDIUM, "high-log-error-rate", "operator_logs",
            f"{error_count} error/warning log lines in last 500 lines",
            details={"sample_lines": sample}
        ))

    # --- Supplementary: OLM issues ---
    olm = supplementary.get("olm_status", {})
    if olm.get("error"):
        issues.append(make_issue(LOW, "olm-error", "olm", f"Could not check OLM: {olm['error']}"))
    for sub_name, sub_info in (olm.get("data") or {}).items():
        if sub_info.get("state") and sub_info["state"] != "AtLatestKnown":
            issues.append(make_issue(
                MEDIUM, f"olm-sub-{sub_name}", "olm",
                f"OLM Subscription '{sub_name}' state is '{sub_info['state']}' (expected AtLatestKnown)",
                details=sub_info
            ))

    # --- Cross-reference correlated signals ---
    events_list = events_section.get("data", {}).get("events") or []
    log_lines = supplementary.get("operator_logs", {}).get("data", [])

    for issue in issues:
        section = issue["section"]
        if section in ("deployments", "pods", "operator"):
            details = issue.get("details", {})
            resource_name = ""
            if isinstance(details, dict):
                resource_name = details.get("name", "") or details.get("pod", "")
            if resource_name:
                related_events = [
                    {"reason": e.get("reason", ""), "message": e.get("message", "")[:200]}
                    for e in events_list
                    if resource_name.lower() in (e.get("name", "") or "").lower()
                       or resource_name.lower() in (e.get("message", "") or "").lower()
                ]
                if related_events:
                    issue["correlated_signals"].append({
                        "source": "events",
                        "detail": f"{len(related_events)} related event(s)",
                        "data": related_events[:5]
                    })
                related_logs = [line[:300] for line in log_lines if resource_name.lower() in line.lower()]
                if related_logs:
                    issue["correlated_signals"].append({
                        "source": "operator_logs",
                        "detail": f"{len(related_logs)} related log line(s)",
                        "data": related_logs[:5]
                    })

    # --- Build result ---
    by_severity = {
        CRITICAL: [i for i in issues if i["severity"] == CRITICAL],
        HIGH: [i for i in issues if i["severity"] == HIGH],
        MEDIUM: [i for i in issues if i["severity"] == MEDIUM],
        LOW: [i for i in issues if i["severity"] == LOW],
    }

    counts = {k: len(v) for k, v in by_severity.items()}
    overall_healthy = counts[CRITICAL] == 0 and counts[HIGH] == 0

    sections_with_issues = list(set(i["section"] for i in issues))
    all_sections = ["nodes", "deployments", "pods", "events", "quotas", "operator", "dsci", "dsc"]
    healthy_sections = [s for s in all_sections if s not in sections_with_issues]

    olm_data = supplementary.get("olm_status", {}).get("data", {})
    operator_info = supplementary.get("operator_deployment", {}).get("data", {})

    operator_version = "unknown"
    for sub_name, sub_info in olm_data.items():
        if sub_info.get("installedCSV"):
            operator_version = sub_info["installedCSV"]
            break
    if operator_version == "unknown":
        images = operator_info.get("images", [])
        if images:
            operator_version = images[0]

    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "overall_healthy": overall_healthy,
        "issues_by_severity": by_severity,
        "healthy_sections": healthy_sections,
        "summary": f"{counts[CRITICAL]} critical, {counts[HIGH]} high, {counts[MEDIUM]} medium, {counts[LOW]} low issues found",
        "operator_version": operator_version
    }

    return json.dumps(result)
