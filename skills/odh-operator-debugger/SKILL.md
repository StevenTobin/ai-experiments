---
name: odh-operator-debugger
description: >-
  Debug and troubleshoot the Open Data Hub operator, including reconciliation
  failures, component status issues, operator crashes, and resource
  conflicts. Use when investigating operator errors, CrashLoopBackOff,
  component not reconciling, or unexpected resource states.
---

# ODH Operator Debugger

Systematic approach to debugging opendatahub-operator issues.

## Diagnostic Decision Tree

**Operator pod not running?** → See [Pod Issues](#pod-issues)
**Component stuck / not deploying?** → See [Reconciliation Issues](#reconciliation-issues)
**Status conditions wrong?** → See [Status Debugging](#status-debugging)
**Webhook errors?** → See [Webhook Issues](#webhook-issues)
**Local dev broken?** → See [Local Development](#local-development)

---

## Pod Issues

### Check operator state

```bash
# ODH namespace
oc get pods -n opendatahub-operator-system
# RHOAI namespace
oc get pods -n redhat-ods-operator

# Describe for events and restart reasons
oc describe pod <pod> -n <namespace>

# Current logs
oc logs -n <namespace> <pod> -c manager --tail=200

# Previous crash logs
oc logs -n <namespace> <pod> -c manager --previous
```

### Common causes

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `OOMKilled` in lastState | Memory limit too low | Increase to 2Gi |
| Repeated restarts, no OOM | Panic in reconcile loop | Check `--previous` logs for stack trace |
| Certificate errors in logs | Webhook cert rotation failed | Check webhook secrets, restart |
| `context deadline exceeded` | Cluster API server slow | Check node health, API server |

### Namespace label conflicts

Only one namespace may have `opendatahub.io/application-namespace=true`. Check:

```bash
oc get ns -l opendatahub.io/application-namespace=true
```

If multiple namespaces have the label, remove duplicates. DSCI `.spec.applicationsNamespace` must match.

---

## Reconciliation Issues

### Understanding the action pipeline

Each component controller runs actions sequentially. If any action returns an error, the pipeline stops. Actions are defined in `internal/controller/components/<name>/<name>_controller.go`.

Typical pipeline: `sanitycheck → initialize → releases → kustomize → deploy → deployments (status) → gc`

### Debug a stuck component

1. **Check the component CR**:

```bash
oc get <componentkind> -o yaml
# e.g.: oc get ray default-ray -o yaml
# e.g.: oc get dashboard default-dashboard -o yaml
```

Look at `.status.conditions` — the `Ready` condition and its `reason`/`message` tell you which action failed.

2. **Check the DSC status**:

```bash
oc get dsc -o yaml
```

The `.status.components.<name>` section mirrors the component CR status.

3. **Check operator logs for the component**:

```bash
# Filter by component controller name
oc logs -n <operator-ns> <pod> -c manager | grep -i "<component-name>"
```

4. **Check if manifests exist**:

```bash
# Verify opt/manifests/<component>/ has content
ls opt/manifests/<component>/
```

Missing manifests → run `make get-manifests`.

### ManagementState gotchas

- `Managed` → component is actively reconciled
- `Removed` → component resources are cleaned up
- `{}` (empty) → treated as Removed, NOT as a no-op
- Only `Managed` causes the component's configs to take effect

### Singleton CR naming

All component CRs must be named `default-<component>`. This is enforced by XValidation rules. Creating a CR with a different name will be rejected by the webhook.

---

## Status Debugging

### Condition types per component

Each component has a `Ready` condition plus additional conditions declared in `conditionTypes` in the support file. Common extras:

- `DeploymentsAvailable` — all deployments have available replicas
- Custom conditions from `WithConditions()` in the reconciler builder

### Condition propagation

```
Component CR .status.conditions
    ↓ (UpdateDSCStatus in handler)
DSC .status.components.<name>
    ↓ (status aggregation controller)
DSC .status.conditions (overall Ready)
```

### Key status files

- Handler's `UpdateDSCStatus()`: `internal/controller/components/<name>/<name>.go`
- Condition utilities: `pkg/controller/conditions/`
- Status controller: `internal/controller/status/`

---

## Webhook Issues

### Symptoms
- `admission webhook denied the request`
- Connection refused on webhook port
- Certificate errors

### Debug steps

```bash
# Check webhook configurations
oc get validatingwebhookconfiguration | grep opendatahub
oc get mutatingwebhookconfiguration | grep opendatahub

# Check webhook service and endpoints
oc get svc -n <operator-ns> | grep webhook
oc get endpoints -n <operator-ns> | grep webhook

# Check certificate secrets
oc get secret -n <operator-ns> | grep webhook
```

### Local dev: skip webhooks

Use `make run-nowebhook` to avoid cert/webhook setup locally. This adds `-tags nowebhook` build tag.

---

## Local Development

### Running the operator locally

```bash
# With webhooks (requires cert setup)
make run

# Without webhooks (recommended for most dev work)
make run-nowebhook
```

Both enable pprof at `http://localhost:6060`.

### Profiling a slow reconciliation

```bash
# Heap profile
go tool pprof -http : http://localhost:6060/debug/pprof/heap

# CPU profile (30s sample)
go tool pprof -http : http://localhost:6060/debug/pprof/profile

# Save for offline analysis
curl -s "http://127.0.0.1:6060/debug/pprof/profile" > cpu-profile.out
```

### local.mk overrides

Create `local.mk` in repo root to customize without touching `Makefile`:

```makefile
VERSION=9.9.9
IMAGE_TAG_BASE=quay.io/myuser/opendatahub-operator
IMG_TAG=dev
OPERATOR_NAMESPACE=my-dev-ns
E2E_TEST_FLAGS="--deletion-policy=never" -timeout 15m
```

### Common local dev issues

| Issue | Solution |
|-------|----------|
| CRD not found | `make install` or `make manifests-all` |
| Missing manifests | `make get-manifests` |
| Stale generated code | `make generate manifests` |
| Import cycle | Check blank imports in `cmd/main.go` |
| Platform mismatch | Set `ODH_PLATFORM_TYPE=OpenDataHub` or `rhoai` |

---

## Upgrade Issues

When upgrading from v2.x to v2.2+:

1. Set components to `Removed` in DSC
2. Delete DSC and DSCI instances
3. Uninstall operator
4. Delete old CRDs if on v1alpha1
5. Clean install new version

See [docs/troubleshooting.md](docs/troubleshooting.md) for full details.
