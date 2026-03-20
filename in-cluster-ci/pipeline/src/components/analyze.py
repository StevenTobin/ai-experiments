from kfp import dsl

@dsl.component(
    base_image="registry.access.redhat.com/ubi9/python-311:latest",
    packages_to_install=["requests"]
)
def analyze_data(cluster_data: str, health_check_output: str, model_name: str, endpoint_url: str) -> str:
    """Sends the collected data to the locally served LLM for analysis."""
    import requests
    import json

    def truncate(text, max_chars=3000):
        if len(text) > max_chars:
            return text[:max_chars] + "\n... [truncated]"
        return text

    hc_lines = []
    for line in health_check_output.split("\n"):
        if not line.startswith("go: downloading"):
            hc_lines.append(line)
    clean_hc = "\n".join(hc_lines)

    prompt = f"""You are an expert Kubernetes and OpenDataHub SRE.
Review the cluster state and health check output below.
List the top issues and give short actionable recommendations.

Cluster Data:
{truncate(cluster_data)}

Health Check:
{truncate(clean_hc)}

Respond in concise Markdown with bullet points."""

    headers = {"Content-Type": "application/json"}
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 512
    }

    full_url = f"{endpoint_url}/v1/chat/completions"

    try:
        response = requests.post(full_url, json=payload, headers=headers, timeout=300)
        response.raise_for_status()
        result = response.json()
        return result["choices"][0]["message"]["content"]
    except Exception as e:
        return f"Failed to get analysis from LLM: {str(e)}\nResponse: {response.text if 'response' in locals() else 'None'}"
