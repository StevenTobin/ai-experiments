from kfp import dsl

@dsl.component(
    base_image="registry.access.redhat.com/ubi9/python-311:latest",
    packages_to_install=["requests"]
)
def interpret_with_llm(issue_report: str, supplementary_data: str, model_name: str, endpoint_url: str) -> str:
    """Sends structured issue report to the LLM for root cause analysis and actionable recommendations."""
    import requests
    import json

    def truncate(text, max_chars=2000):
        if len(text) > max_chars:
            return text[:max_chars] + "\n... [truncated]"
        return text

    def format_issue(issue):
        lines = [f"- {issue['summary']}"]
        for signal in issue.get("correlated_signals", []):
            lines.append(f"  Correlated: [{signal['source']}] {signal['detail']}")
            for item in signal.get("data", [])[:3]:
                if isinstance(item, dict):
                    lines.append(f"    - {item.get('reason', '')}: {item.get('message', '')[:150]}")
                elif isinstance(item, str):
                    lines.append(f"    - {item[:150]}")
        return "\n".join(lines)

    issues = json.loads(issue_report)
    supplementary = json.loads(supplementary_data)

    overall_healthy = issues.get("overall_healthy", True)
    by_severity = issues.get("issues_by_severity", {})
    critical = by_severity.get("critical", [])
    high = by_severity.get("high", [])
    medium = by_severity.get("medium", [])
    low = by_severity.get("low", [])

    log_lines = supplementary.get("operator_logs", {}).get("data", [])
    olm_data = supplementary.get("olm_status", {}).get("data", {})

    if overall_healthy and not medium and not low:
        # Healthy cluster prompt
        sections = []
        sections.append("You are an expert OpenDataHub and Kubernetes SRE reviewing a healthy cluster.")
        sections.append(f"Health summary: {issues.get('summary', 'No issues found')}")
        sections.append(f"Healthy sections: {', '.join(issues.get('healthy_sections', []))}")
        sections.append(f"Operator version: {issues.get('operator_version', 'unknown')}")
        sections.append("")
        if log_lines:
            sections.append("OPERATOR LOG ERRORS/WARNINGS (even though cluster is healthy):")
            sections.append(truncate("\n".join(log_lines[:15])))
            sections.append("")
        if olm_data:
            sections.append("OLM STATUS:")
            for name, info in olm_data.items():
                sections.append(f"- {name}: state={info.get('state', 'unknown')}, csv={info.get('installedCSV', 'unknown')}")
            sections.append("")
        sections.append("INSTRUCTIONS:")
        sections.append("The cluster is currently healthy. Review the operator logs and OLM status for:")
        sections.append("1. Any patterns suggesting emerging issues or degradation trends")
        sections.append("2. Warnings that might become problems under load")
        sections.append("3. Any configuration improvements worth making")
        sections.append("If everything looks clean, say so briefly. Be concise, use markdown bullet points.")
        prompt = "\n".join(sections)
    else:
        # Issues detected prompt
        sections = []
        sections.append("You are an expert OpenDataHub and Kubernetes SRE performing root cause analysis on a cluster.")
        sections.append("The following issues were found by automated health checks. Analyze them and provide actionable guidance.\n")
        if critical:
            sections.append("CRITICAL ISSUES (service-impacting):")
            for issue in critical:
                sections.append(format_issue(issue))
            sections.append("")
        if high:
            sections.append("HIGH ISSUES (component degradation):")
            for issue in high:
                sections.append(format_issue(issue))
            sections.append("")
        if medium:
            sections.append("MEDIUM ISSUES (warnings):")
            for issue in medium[:5]:
                sections.append(f"- {issue['summary']}")
            sections.append("")
        if low:
            sections.append(f"LOW ISSUES: {len(low)} informational items (no action needed)")
            sections.append("")
        if log_lines:
            sections.append("OPERATOR LOG ERRORS (sample):")
            sections.append(truncate("\n".join(log_lines[:15])))
            sections.append("")
        sections.append("INSTRUCTIONS:")
        sections.append("For each CRITICAL and HIGH issue:")
        sections.append("1. Most likely root cause (reference specific error messages and resource names)")
        sections.append("2. Whether this is likely an operator bug, a configuration error, or an infrastructure problem")
        sections.append("3. Specific remediation command or action (e.g., oc commands, config changes)")
        sections.append("")
        sections.append("For MEDIUM issues, briefly note which need attention vs which are expected/acceptable.")
        sections.append("Be concise. Use markdown bullet points.")
        prompt = "\n".join(sections)

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
        return f"LLM analysis unavailable: {str(e)}"
