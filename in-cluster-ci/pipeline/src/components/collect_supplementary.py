from kfp import dsl

@dsl.component(
    base_image="registry.access.redhat.com/ubi9/python-311:latest",
    packages_to_install=["kubernetes"]
)
def collect_supplementary_data() -> str:
    """Collects data the health check tool does not cover: operator logs, OLM status, DSC conditions, resource pressure."""
    from kubernetes import client, config
    import json

    try:
        config.load_incluster_config()
    except Exception as e:
        return json.dumps({"error": f"Failed to load in-cluster config: {str(e)}"})

    v1 = client.CoreV1Api()
    custom_api = client.CustomObjectsApi()
    apps_v1 = client.AppsV1Api()

    report = {}

    # 1. Operator error/warning logs (filtered)
    logs_section = {"data": [], "error": ""}
    try:
        pods = v1.list_namespaced_pod(
            namespace="opendatahub-operator-system",
            label_selector="name=opendatahub-operator"
        )
        if not pods.items:
            logs_section["error"] = "No operator pods found"
        else:
            pod_name = pods.items[0].metadata.name
            raw_logs = v1.read_namespaced_pod_log(
                name=pod_name,
                namespace="opendatahub-operator-system",
                tail_lines=500
            )
            error_lines = []
            for line in raw_logs.splitlines():
                lower = line.lower()
                if any(kw in lower for kw in ['"level":"error"', '"level":"warn"', "error", "warning", "panic", "fatal"]):
                    error_lines.append(line)
            logs_section["data"] = error_lines[-100:]
            logs_section["total_lines_scanned"] = 500
            logs_section["error_warning_count"] = len(error_lines)
    except Exception as e:
        logs_section["error"] = f"Failed to get operator logs: {str(e)}"
    report["operator_logs"] = logs_section

    # 2. OLM Subscription status
    olm_section = {"data": {}, "error": ""}
    try:
        subs = custom_api.list_namespaced_custom_object(
            group="operators.coreos.com",
            version="v1alpha1",
            namespace="opendatahub-operator-system",
            plural="subscriptions"
        )
        for sub in subs.get("items", []):
            name = sub.get("metadata", {}).get("name", "unknown")
            olm_section["data"][name] = {
                "currentCSV": sub.get("status", {}).get("currentCSV", ""),
                "installedCSV": sub.get("status", {}).get("installedCSV", ""),
                "state": sub.get("status", {}).get("state", ""),
                "conditions": [
                    {"type": c.get("type", ""), "status": c.get("status", ""), "message": c.get("message", "")}
                    for c in sub.get("status", {}).get("conditions", [])
                ]
            }
    except Exception as e:
        olm_section["error"] = f"Failed to get OLM status: {str(e)}"
    report["olm_status"] = olm_section

    # 3. DSC full conditions
    dsc_section = {"data": {}, "error": ""}
    try:
        dscs = custom_api.list_cluster_custom_object(
            group="datasciencecluster.opendatahub.io",
            version="v1",
            plural="datascienceclusters"
        )
        for dsc in dscs.get("items", []):
            name = dsc.get("metadata", {}).get("name", "unknown")
            status = dsc.get("status", {})
            dsc_section["data"][name] = {
                "conditions": [
                    {"type": c.get("type", ""), "status": c.get("status", ""), "reason": c.get("reason", ""), "message": c.get("message", "")}
                    for c in status.get("conditions", [])
                ],
                "installedComponents": status.get("installedComponents", {}),
                "phase": status.get("phase", "")
            }
    except Exception as e:
        dsc_section["error"] = f"Failed to get DSC conditions: {str(e)}"
    report["dsc_conditions"] = dsc_section

    # 4. DSCI conditions
    dsci_section = {"data": {}, "error": ""}
    try:
        dscis = custom_api.list_cluster_custom_object(
            group="dscinitialization.opendatahub.io",
            version="v1",
            plural="dscinitializations"
        )
        for dsci in dscis.get("items", []):
            name = dsci.get("metadata", {}).get("name", "unknown")
            status = dsci.get("status", {})
            dsci_section["data"][name] = {
                "conditions": [
                    {"type": c.get("type", ""), "status": c.get("status", ""), "reason": c.get("reason", ""), "message": c.get("message", "")}
                    for c in status.get("conditions", [])
                ],
                "phase": status.get("phase", "")
            }
    except Exception as e:
        dsci_section["error"] = f"Failed to get DSCI conditions: {str(e)}"
    report["dsci_conditions"] = dsci_section

    # 5. Resource pressure: pods with high restart counts or OOMKilled
    pressure_section = {"data": [], "error": ""}
    namespaces = ["opendatahub-operator-system", "opendatahub", "redhat-ods-operator", "redhat-ods-applications"]
    try:
        for ns in namespaces:
            try:
                ns_pods = v1.list_namespaced_pod(namespace=ns)
            except Exception:
                continue
            for pod in ns_pods.items:
                pod_issues = []
                for cs in (pod.status.container_statuses or []):
                    if cs.restart_count > 3:
                        pod_issues.append(f"container '{cs.name}' restarted {cs.restart_count} times")
                    if cs.last_state and cs.last_state.terminated:
                        if cs.last_state.terminated.reason == "OOMKilled":
                            pod_issues.append(f"container '{cs.name}' was OOMKilled")
                    if cs.state and cs.state.waiting and cs.state.waiting.reason in ("CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull"):
                        pod_issues.append(f"container '{cs.name}' in {cs.state.waiting.reason}")
                if pod_issues:
                    pressure_section["data"].append({
                        "namespace": ns,
                        "pod": pod.metadata.name,
                        "phase": pod.status.phase,
                        "issues": pod_issues
                    })
    except Exception as e:
        pressure_section["error"] = f"Failed to collect resource pressure data: {str(e)}"
    report["resource_pressure"] = pressure_section

    # 6. Operator deployment info
    deploy_section = {"data": {}, "error": ""}
    try:
        dep = apps_v1.read_namespaced_deployment(
            name="opendatahub-operator-controller-manager",
            namespace="opendatahub-operator-system"
        )
        containers = dep.spec.template.spec.containers or []
        deploy_section["data"] = {
            "ready_replicas": dep.status.ready_replicas or 0,
            "desired_replicas": dep.spec.replicas or 1,
            "images": [c.image for c in containers],
            "created": dep.metadata.creation_timestamp.isoformat() if dep.metadata.creation_timestamp else ""
        }
    except Exception as e:
        deploy_section["error"] = f"Failed to get operator deployment: {str(e)}"
    report["operator_deployment"] = deploy_section

    return json.dumps(report)
