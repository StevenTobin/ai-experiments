from kfp import dsl

@dsl.component(
    base_image="registry.access.redhat.com/ubi9/python-311:latest",
    packages_to_install=["kubernetes", "requests"]
)
def collect_cluster_state() -> str:
    """Collects logs and status from the OpenDataHub operator."""
    from kubernetes import client, config
    import json
    import os
    
    # In-cluster config
    try:
        config.load_incluster_config()
    except Exception as e:
        return f"Error loading config: {str(e)}"
        
    v1 = client.CoreV1Api()
    custom_api = client.CustomObjectsApi()
    
    report = {"logs": "", "components": {}}
    
    # 1. Get ODH Operator Logs
    try:
        pods = v1.list_namespaced_pod(
            namespace="opendatahub-operator-system",
            label_selector="name=opendatahub-operator"
        )
        if pods.items:
            pod_name = pods.items[0].metadata.name
            logs = v1.read_namespaced_pod_log(
                name=pod_name, 
                namespace="opendatahub-operator-system",
                tail_lines=200
            )
            report["logs"] = logs
    except Exception as e:
        report["logs"] = f"Failed to get logs: {str(e)}"
        
    # 2. Get DSC Status
    try:
        dsc = custom_api.get_cluster_custom_object(
            group="datasciencecluster.opendatahub.io",
            version="v1",
            plural="datascienceclusters",
            name="default-dsc"
        )
        if "status" in dsc and "installedComponents" in dsc["status"]:
            report["components"] = dsc["status"]["installedComponents"]
    except Exception as e:
        report["components"] = {"error": f"Failed to get DSC: {str(e)}"}
        
    return json.dumps(report)
