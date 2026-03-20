from kfp import dsl

@dsl.component(
    base_image="registry.access.redhat.com/ubi9/python-311:latest"
)
def generate_report(issue_report: str, llm_analysis: str) -> str:
    """Generates a structured markdown health report combining deterministic analysis and LLM interpretation."""
    import json

    issues = json.loads(issue_report)
    by_severity = issues.get("issues_by_severity", {})
    overall_healthy = issues.get("overall_healthy", True)
    timestamp = issues.get("timestamp", "unknown")
    operator_version = issues.get("operator_version", "unknown")

    status = "HEALTHY" if overall_healthy else "ISSUES FOUND"

    lines = []
    lines.append(f"# ODH Cluster Health Report")
    lines.append(f"**Run:** {timestamp} | **Status:** {status} | **Operator:** {operator_version}")
    lines.append("")

    lines.append(f"## Summary")
    lines.append(f"{issues.get('summary', 'No data')}")
    lines.append("")

    for severity in ["critical", "high", "medium", "low"]:
        sev_issues = by_severity.get(severity, [])
        if not sev_issues:
            continue

        lines.append(f"## {severity.upper()} Issues")
        lines.append("")
        for issue in sev_issues:
            lines.append(f"### {issue['summary']}")
            lines.append(f"**Section:** {issue['section']}")

            if issue.get("correlated_signals"):
                lines.append("**Correlated signals:**")
                for signal in issue["correlated_signals"]:
                    lines.append(f"- [{signal['source']}] {signal['detail']}")
            lines.append("")

    healthy = issues.get("healthy_sections", [])
    if healthy:
        lines.append("## Healthy Systems")
        lines.append(f"{', '.join(healthy)} -- operating normally")
        lines.append("")

    lines.append("## AI Analysis")
    lines.append(llm_analysis)
    lines.append("")

    report = "\n".join(lines)

    print("=" * 60)
    print(report)
    print("=" * 60)

    return report
