from kfp import dsl

@dsl.component(
    base_image="registry.access.redhat.com/ubi9/go-toolset:latest",
    packages_to_install=["gitpython", "kubernetes"]
)
def run_health_check() -> str:
    """Clones the opendatahub-operator repo and runs the health-check tool with JSON output."""
    import subprocess
    import tempfile
    import json

    work_dir = tempfile.mkdtemp()
    repo_url = "https://github.com/opendatahub-io/opendatahub-operator.git"

    print(f"Cloning {repo_url} into {work_dir}...")
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, work_dir],
            check=True,
            capture_output=True,
            text=True
        )
    except subprocess.CalledProcessError as e:
        return json.dumps({
            "exit_code": -1,
            "report": None,
            "stderr": f"Failed to clone repository: {e.stderr}"
        })

    print("Running health-check tool with -json output...")
    try:
        result = subprocess.run(
            ["go", "run", "cmd/health-check/main.go", "-json"],
            cwd=work_dir,
            check=False,
            capture_output=True,
            text=True,
            timeout=300
        )

        report = None
        if result.stdout.strip():
            try:
                report = json.loads(result.stdout)
            except json.JSONDecodeError:
                report = {"raw_output": result.stdout}

        if isinstance(report, dict):
            for section_key in ("nodes", "deployments", "pods", "events", "quotas", "operator", "dsci", "dsc"):
                section = report.get(section_key)
                if isinstance(section, dict):
                    section_data = section.get("data")
                    if isinstance(section_data, dict) and "data" in section_data:
                        del section_data["data"]

        stderr_lines = [
            line for line in result.stderr.splitlines()
            if not line.startswith("go: downloading")
        ]

        return json.dumps({
            "exit_code": result.returncode,
            "report": report,
            "stderr": "\n".join(stderr_lines)
        })
    except subprocess.TimeoutExpired:
        return json.dumps({
            "exit_code": -2,
            "report": None,
            "stderr": "Health check timed out after 300 seconds"
        })
    except Exception as e:
        return json.dumps({
            "exit_code": -1,
            "report": None,
            "stderr": f"Failed to execute health check tool: {str(e)}"
        })
