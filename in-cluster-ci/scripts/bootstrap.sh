#!/bin/bash
set -e

# Define directories
PROJECT_DIR="/home/stobin/git/ai/experiments/in-cluster-ci"
ODH_GITOPS_DIR="/home/stobin/git/odh-gitops"

# 1. Install ArgoCD
echo "Installing ArgoCD..."
oc create namespace argocd || true
oc apply -n argocd --server-side --force-conflicts -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

echo "Waiting for ArgoCD server to be ready..."
oc wait --for=condition=Available deployment/argocd-server -n argocd --timeout=300s

# 2. Add local repository to ArgoCD
# For local dev we can just port-forward or use local paths if argocd has access
# BUT, since ArgoCD runs in cluster, it needs a real git repo.
# In a real environment, you'd push these changes to a remote Git repo and point ArgoCD to it.
# We'll set the repo URL in the App-of-Apps to a placeholder for now.
# You will need to commit and push this directory to a git server accessible by the cluster.

echo "IMPORTANT: ArgoCD needs access to your git repositories."
echo "Please ensure both odh-gitops and in-cluster-ci are pushed to a remote git server."

# 3. Apply the bootstrap App of Apps
echo "Applying bootstrap App of Apps..."
oc apply -f "$PROJECT_DIR/gitops/bootstrap/app-of-apps.yaml"

echo "Bootstrap complete! Check ArgoCD UI or 'oc get applications -n argocd' for progress."
