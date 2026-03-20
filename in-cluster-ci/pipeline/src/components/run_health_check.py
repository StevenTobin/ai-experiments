from kfp import dsl

@dsl.component(
    base_image="registry.access.redhat.com/ubi9/go-toolset:latest",
    packages_to_install=["gitpython", "kubernetes"]
)
def run_health_check() -> str:
    """Clones the opendatahub-operator repo and runs the health-check tool."""
    import subprocess
    import tempfile
    import os
    
    # We need to make sure we have a temp dir to clone into
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
        return f"Failed to clone repository: {e.stderr}"
    
    print("Running health-check tool...")
    # The health check tool needs KUBECONFIG or incluster config.
    # By default, client-go uses incluster config if running in a pod.
    try:
        # We need to run `go run cmd/health-check/main.go`
        result = subprocess.run(
            ["go", "run", "cmd/health-check/main.go"],
            cwd=work_dir,
            check=False,  # Don't throw exception on non-zero exit, we want the output
            capture_output=True,
            text=True
        )
        
        output = f"Health Check Exit Code: {result.returncode}\n\nSTDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"
        return output
    except Exception as e:
        return f"Failed to execute health check tool: {str(e)}"
