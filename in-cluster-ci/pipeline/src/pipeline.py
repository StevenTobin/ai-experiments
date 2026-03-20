import os
from kfp import dsl
from kfp import compiler

from components.collect import collect_cluster_state
from components.run_health_check import run_health_check
from components.analyze import analyze_data
from components.report import generate_report

@dsl.pipeline(
    name="odh-in-cluster-ci-analyzer",
    description="Collects ODH logs, runs health check, analyzes with a local LLM, and generates a health report."
)
def analyzer_pipeline(
    model_name: str = "Qwen/Qwen2.5-0.5B-Instruct",
    endpoint_url: str = "http://analyzer-llm-predictor.in-cluster-ci.svc.cluster.local:8080"
):
    # Step 1: Collect Data
    collect_task = collect_cluster_state()
    
    # Step 1b: Run Health Check
    health_check_task = run_health_check()
    
    # Step 2: Analyze Data
    analyze_task = analyze_data(
        cluster_data=collect_task.output,
        health_check_output=health_check_task.output,
        model_name=model_name,
        endpoint_url=endpoint_url
    )
    
    # Step 3: Generate Report
    report_task = generate_report(analysis=analyze_task.output)

if __name__ == "__main__":
    compiler.Compiler().compile(analyzer_pipeline, "analyzer_pipeline.yaml")
