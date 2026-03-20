from kfp import dsl

@dsl.component(
    base_image="registry.access.redhat.com/ubi9/python-311:latest"
)
def generate_report(analysis: str) -> str:
    """Formats the final report. Could later be expanded to post to Slack/Github."""
    print("=== OpenDataHub AI Health Analysis Report ===")
    print(analysis)
    print("=============================================")
    return analysis
