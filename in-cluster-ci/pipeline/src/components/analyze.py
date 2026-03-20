from kfp import dsl

@dsl.component(
    base_image="registry.access.redhat.com/ubi9/python-311:latest",
    packages_to_install=["requests"]
)
def analyze_data(cluster_data: str, health_check_output: str, model_name: str, endpoint_url: str) -> str:
    """Sends the collected data to the locally served LLM for analysis."""
    import requests
    import json
    
    # We construct the prompt instructing the LLM to act as an SRE
    prompt = f"""
    You are an expert Kubernetes and OpenDataHub Site Reliability Engineer.
    Please review the following cluster state, recent operator logs, and health check output.
    Identify any potential issues, bugs, or misconfigurations, and provide recommendations.
    
    Cluster Data:
    {cluster_data}
    
    Health Check Output:
    {health_check_output}
    
    Provide your analysis in Markdown format.
    """
    
    headers = {"Content-Type": "application/json"}
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 1000
    }
    
    # We expect the vLLM server to be exposed at the given endpoint
    # E.g., http://analyzer-llm.in-cluster-ci.svc.cluster.local:8080/v1/chat/completions
    full_url = f"{endpoint_url}/v1/chat/completions"
    
    try:
        response = requests.post(full_url, json=payload, headers=headers)
        response.raise_for_status()
        result = response.json()
        return result["choices"][0]["message"]["content"]
    except Exception as e:
        return f"Failed to get analysis from LLM: {str(e)}\nResponse: {response.text if 'response' in locals() else 'None'}"
