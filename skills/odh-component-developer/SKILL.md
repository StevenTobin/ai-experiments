---
name: odh-component-developer
description: >-
  Scaffold and integrate new components into the Open Data Hub operator, or
  modify existing component controllers. Use when adding a new component,
  modifying component reconciliation logic, working with the action pipeline,
  or running make new-component.
---

# ODH Component Developer

Guide for adding new components or modifying existing ones in the opendatahub-operator.

## Quick Reference

| Task | Command / Location |
|------|--------------------|
| Scaffold new component | `make new-component COMPONENT=<name>` |
| Regenerate after API changes | `make generate manifests api-docs bundle` |
| Component API types | `api/components/v1alpha1/<name>_types.go` |
| Controller | `internal/controller/components/<name>/<name>_controller.go` |
| Actions | `internal/controller/components/<name>/<name>_controller_actions.go` |
| Handler (registry) | `internal/controller/components/<name>/<name>.go` |
| Support constants | `internal/controller/components/<name>/<name>_support.go` |
| DSC wiring | `api/datasciencecluster/v2/datasciencecluster_types.go` |
| RBAC markers | `internal/controller/datasciencecluster/kubebuilder_rbac.go` |
| Main import | `cmd/main.go` |

## New Component Workflow

### Step 1: Scaffold

```bash
make new-component COMPONENT=mycomponent
```

This generates: API types, controller package (4 files), DSC spec entry, RBAC markers, and runs `make generate manifests api-docs bundle fmt`.

### Step 2: Implement Business Logic

Fill in the generated files. The four files in `internal/controller/components/<name>/` each serve a distinct purpose:

**`<name>.go`** — Component handler (registry interface):
- `GetName()` — return component name constant
- `NewCRObject()` — construct the CR from DSC spec
- `Init()` — one-time setup (image param substitution)
- `IsEnabled()` — check `ManagementState == Managed`
- `UpdateDSCStatus()` — propagate component status to DSC

**`<name>_support.go`** — Constants and helpers:
- `ComponentName`, `LegacyComponentName`, `ReadyConditionType`
- `imageParamMap` for RELATED_IMAGE env vars
- `conditionTypes` slice
- `manifestPath()` returning `types.ManifestInfo`

**`<name>_controller.go`** — Reconciler builder:
- Use `reconciler.ReconcilerFor(mgr, &componentApi.MyComponent{})`
- Chain `.Owns()`, `.Watches()`, `.WithAction()`, `.WithConditions()`, `.Build(ctx)`

**`<name>_controller_actions.go`** — Custom action functions:
- `initialize()` — register manifest paths, apply namespace params
- Any component-specific reconciliation logic

### Step 3: Standard Action Pipeline

Most components follow this pipeline order:

```go
WithAction(sanitycheck.NewAction(...)).     // optional: check preconditions
WithAction(initialize).                      // register manifests, set params
WithAction(releases.NewAction()).            // track releases
WithAction(kustomize.NewAction(...)).        // render manifests
WithAction(deploy.NewAction(deploy.WithCache())).  // apply to cluster
WithAction(deployments.NewAction()).         // check deployment status
WithAction(gc.NewAction()).                  // MUST BE LAST
```

**Critical rule**: Garbage collection (`gc.NewAction()`) MUST be the final action.

### Step 4: Wiring

After scaffolding, verify these are updated (codegen handles most):

1. **DSC types** — `api/datasciencecluster/v2/datasciencecluster_types.go`: `Components` and `ComponentsStatus` structs
2. **RBAC** — `internal/controller/datasciencecluster/kubebuilder_rbac.go`
3. **main.go** — blank import: `_ "github.com/.../internal/controller/components/<name>"`
4. **CRD kustomization** — `config/crd/kustomization.yaml`
5. **DSC controller owns** — `internal/controller/datasciencecluster/datasciencecluster_controller.go`
6. **CSV internal objects** — `config/manifests/bases/opendatahub-operator.clusterserviceversion.yaml`
7. **PROJECT file** — resource entry for the new CRD

### Step 5: Manifests

- Add component manifest repo to `get_all_manifests.sh` (both ODH and RHOAI arrays)
- Manifests land in `opt/manifests/<component>/`
- Kustomize overlays go under the manifest source path
- `manifestPath()` in support file points to `odhdeploy.DefaultManifestPath` + component context dir

### Step 6: Regenerate and Verify

```bash
make generate manifests api-docs bundle fmt
make lint
make unit-test
```

## Modifying Existing Components

### Adding a New Action

1. Create the action function in `<name>_controller_actions.go`:

```go
func myCustomAction(ctx context.Context, rr *odhtypes.ReconciliationRequest) error {
    // Access rendered resources via rr.Resources
    // Access the CR instance via rr.Instance
    // Access the client via rr.Client
    return nil
}
```

2. Insert it in the correct position in the action chain (before GC, after relevant prerequisites).

### Adding Resource Ownership

Add `.Owns(&<type>{})` to the reconciler builder. For deployments, use the deployment predicate:

```go
Owns(&appsv1.Deployment{}, reconciler.WithPredicates(resources.NewDeploymentPredicate()))
```

### Dynamic CRD Watches

For resources that may not exist on all clusters:

```go
WatchesGVK(gvk.SomeCRD, reconciler.Dynamic(reconciler.CrdExists(gvk.SomeCRD)))
```

### Platform-Specific Code

Use build-tag split files when ODH and RHOAI need different logic:
- `<name>_types.odh.go` with `//go:build !rhoai`
- `<name>_types.rhoai.go` with `//go:build rhoai`

## Common Imports

```go
componentApi "github.com/opendatahub-io/opendatahub-operator/v2/api/components/v1alpha1"
dscv2 "github.com/opendatahub-io/opendatahub-operator/v2/api/datasciencecluster/v2"
"github.com/opendatahub-io/opendatahub-operator/v2/pkg/controller/actions/deploy"
"github.com/opendatahub-io/opendatahub-operator/v2/pkg/controller/actions/gc"
"github.com/opendatahub-io/opendatahub-operator/v2/pkg/controller/actions/render/kustomize"
"github.com/opendatahub-io/opendatahub-operator/v2/pkg/controller/actions/status/deployments"
"github.com/opendatahub-io/opendatahub-operator/v2/pkg/controller/actions/status/releases"
"github.com/opendatahub-io/opendatahub-operator/v2/pkg/controller/reconciler"
odhtypes "github.com/opendatahub-io/opendatahub-operator/v2/pkg/controller/types"
odhdeploy "github.com/opendatahub-io/opendatahub-operator/v2/pkg/deploy"
```

## Reference Components

- **Ray** — Minimal, clean example of the standard pattern
- **Dashboard** — Complex: custom actions, observability, hardware profiles
- **KServe** — Heavy dependencies: operators, Istio, subscriptions, apply ordering

For full integration checklist, see [docs/COMPONENT_INTEGRATION.md](docs/COMPONENT_INTEGRATION.md).
