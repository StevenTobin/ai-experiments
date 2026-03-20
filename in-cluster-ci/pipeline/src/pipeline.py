from kfp import dsl
from kfp import compiler

from components.run_health_check import run_health_check
from components.collect_supplementary import collect_supplementary_data
from components.analyze_issues import analyze_issues
from components.analyze import interpret_with_llm
from components.report import generate_report

@dsl.pipeline(
    name="odh-cluster-analyzer",
    description="Structured ODH cluster health analysis: collects health data, performs deterministic issue analysis, then uses an LLM for root cause interpretation."
)
def analyzer_pipeline(
    model_name: str = "Qwen2.5-Coder-3B-Instruct",
    endpoint_url: str = "http://analyzer-llm-predictor.in-cluster-ci.svc.cluster.local:8080"
):
    # Phase 1: Parallel structured data collection
    health_task = run_health_check()
    health_task.set_env_variable("HOME", "/tmp")
    health_task.set_env_variable("GOPATH", "/tmp/go")
    health_task.set_caching_options(False)

    supplementary_task = collect_supplementary_data()
    supplementary_task.set_caching_options(False)

    # Phase 2: Deterministic issue analysis
    issues_task = analyze_issues(
        health_data=health_task.output,
        supplementary_data=supplementary_task.output
    )
    issues_task.set_caching_options(False)

    # Phase 3: LLM root cause interpretation
    llm_task = interpret_with_llm(
        issue_report=issues_task.output,
        supplementary_data=supplementary_task.output,
        model_name=model_name,
        endpoint_url=endpoint_url
    )
    llm_task.set_caching_options(False)

    # Phase 4: Structured report
    report_task = generate_report(
        issue_report=issues_task.output,
        llm_analysis=llm_task.output
    )
    report_task.set_caching_options(False)

if __name__ == "__main__":
    compiler.Compiler().compile(analyzer_pipeline, "analyzer_pipeline.yaml")
