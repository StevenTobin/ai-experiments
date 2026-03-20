# OpenDataHub In-Cluster CI & Health Analyzer

This repository contains an automated, AI-driven CI and health analyzer designed to be deployed alongside OpenDataHub on short-lived clusters. It uses OpenDataHub's own components (Model Serving and Data Science Pipelines) to monitor operator logs, assess cluster health, and provide recommendations using a locally served Large Language Model.

## Architecture

1.  **ArgoCD (GitOps)**: Bootstraps the entire environment.
2.  **OpenDataHub Operator**: The core platform.
3.  **KServe (vLLM)**: Hosts a Large Language Model (defaulting to Llama-3-8B).
4.  **Data Science Pipelines (KFP)**: Runs a scheduled Python pipeline that:
    *   Collects operator logs and cluster state.
    *   Queries the local LLM endpoint with a custom prompt.
    *   Generates a Markdown report with SRE-style recommendations.

## Prerequisites

*   An OpenShift cluster with cluster-admin access.
*   A default storage class capable of provisioning PVCs for model weights.
*   Ensure that the model you plan to serve is available via PVC or S3. (The default Helm values assume a `model-weights-pvc` exists. Adjust `helm/in-cluster-ci/values.yaml` as needed).

## Deployment Instructions

### 1. Push to a Git Repository

ArgoCD requires the manifests to be hosted in a Git repository accessible from the cluster.

1.  Fork or push this directory to a repository (e.g., GitHub, GitLab).
2.  Update the `repoURL` in the following files to point to your repository:
    *   `gitops/bootstrap/app-of-apps.yaml`
    *   `gitops/apps/odh-instance.yaml`
    *   `gitops/apps/in-cluster-ci.yaml`

### 2. Bootstrap the Cluster

Run the bootstrap script. This will install ArgoCD and apply the root "App of Apps".

```bash
./scripts/bootstrap.sh
```

### 3. What Happens Next?

Once the bootstrap script completes, ArgoCD takes over and performs the following in sequence (via `sync-waves`):

1.  **Wave 1**: Installs the OpenDataHub Operator.
2.  **Wave 2**: Applies the `DSCInitialization` and `DataScienceCluster` custom resources, which triggers the operator to deploy KServe, Data Science Pipelines, and other required components.
3.  **Wave 3**: Deploys the `in-cluster-ci` Helm chart.
    *   Creates an `InferenceService` serving the LLM.
    *   A Helm hook (`Setup Job`) runs a Python script that compiles the KFP pipeline and submits a scheduled run to the Data Science Pipelines API.

## Testing Locally (Without ArgoCD)

If you already have OpenDataHub installed and just want to deploy the analyzer:

```bash
# Ensure KServe and Data Science Pipelines are enabled in your DSC
helm install in-cluster-ci ./helm/in-cluster-ci -n in-cluster-ci --create-namespace
```

## Directory Structure

*   `gitops/`: ArgoCD applications and bootstrap configuration.
*   `helm/`: Helm charts for the ODH instance and the analyzer application.
*   `pipeline/`: Source code for the Kubeflow Pipeline and its components.
*   `scripts/`: Utility scripts for quick deployment.
