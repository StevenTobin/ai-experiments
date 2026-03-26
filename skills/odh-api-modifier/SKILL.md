---
name: odh-api-modifier
description: >-
  Add or modify CRD fields, API types, and run code generation for the Open
  Data Hub operator. Use when adding fields to component specs, modifying
  DSC/DSCI types, updating kubebuilder markers, running make generate or
  make manifests, or working with API versioning.
---

# ODH API Modifier

Guide for modifying CRD types and running code generation in the opendatahub-operator.

## API Type Locations

| API | Package | Primary File(s) |
|-----|---------|-----------------|
| Component CRDs | `api/components/v1alpha1/` | `<component>_types.go` |
| DSC v2 (primary) | `api/datasciencecluster/v2/` | `datasciencecluster_types.go` |
| DSC v1 (legacy) | `api/datasciencecluster/v1/` | `datasciencecluster_types.go` |
| DSCI v2 | `api/dscinitialization/v2/` | `dscinitialization_types.go` |
| Services | `api/services/v1alpha1/` | `<service>_types.go` |
| Common types | `api/common/` | Shared Status, Conditions, ManagementSpec |
| Infrastructure | `api/infrastructure/v1/` | HardwareProfile types |
| Features | `api/features/v1/` | FeatureTracker types |

## Adding a Field to a Component CRD

### Step 1: Add the field to the types file

Edit `api/components/v1alpha1/<component>_types.go`. Fields go into `CommonSpec` if they should be exposed in both the component CR **and** the DSC, or into the component's own `Spec` if only internal.

```go
type MyComponentCommonSpec struct {
    // +kubebuilder:validation:Optional
    // +kubebuilder:default:="default-value"
    // MyField controls something specific.
    MyField string `json:"myField,omitempty"`
}
```

The `DSC<Component>` struct embeds `<Component>CommonSpec`, so the field automatically appears in DSC.

### Step 2: Add field to status (if needed)

For status fields, add to `<Component>CommonStatus`:

```go
type MyComponentCommonStatus struct {
    common.ComponentReleaseStatus `json:",inline"`
    // MyStatusField reflects observed state.
    MyStatusField string `json:"myStatusField,omitempty"`
}
```

### Step 3: Wire through to the controller

In the component handler's `NewCRObject()` method (`internal/controller/components/<name>/<name>.go`), ensure the new field propagates from DSC to the component CR:

```go
func (s *componentHandler) NewCRObject(_ context.Context, _ client.Client, dsc *dscv2.DataScienceCluster) (common.PlatformObject, error) {
    return &componentApi.MyComponent{
        // ...
        Spec: componentApi.MyComponentSpec{
            MyComponentCommonSpec: dsc.Spec.Components.MyComponent.MyComponentCommonSpec,
        },
    }, nil
}
```

### Step 4: Regenerate

```bash
make generate manifests api-docs bundle fmt
```

This runs:
- `controller-gen` for DeepCopy methods (`zz_generated.deepcopy.go`)
- `controller-gen` for CRD YAML (`config/crd/bases/`)
- `controller-gen` for RBAC and webhooks
- API docs generation
- OLM bundle update

## Kubebuilder Markers Reference

### Commonly used markers

```go
// Validation
// +kubebuilder:validation:Optional
// +kubebuilder:validation:Required
// +kubebuilder:validation:Enum=value1;value2;value3
// +kubebuilder:validation:Minimum=0
// +kubebuilder:validation:Maximum=100
// +kubebuilder:validation:Pattern=`^[a-z]+$`
// +kubebuilder:validation:MinLength=1
// +kubebuilder:validation:MaxLength=63

// Defaults
// +kubebuilder:default:="my-default"
// +kubebuilder:default:=true

// CRD-level
// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:resource:scope=Cluster
// +kubebuilder:printcolumn:name="Ready",type=string,JSONPath=`.status.conditions[?(@.type=="Ready")].status`

// CEL validation (XValidation)
// +kubebuilder:validation:XValidation:rule="self.metadata.name == 'default-mycomponent'",message="name must be default-mycomponent"
```

### RBAC markers

RBAC markers for component resources go in `internal/controller/datasciencecluster/kubebuilder_rbac.go`:

```go
// +kubebuilder:rbac:groups=components.platform.opendatahub.io,resources=mycomponents,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=components.platform.opendatahub.io,resources=mycomponents/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=components.platform.opendatahub.io,resources=mycomponents/finalizers,verbs=update
```

## Type Hierarchy

Understanding how types compose is critical:

```
DSC spec                           Component CR
─────────                          ─────────────
Components struct                  MyComponent struct
  └─ DSCMyComponent                  ├─ Spec: MyComponentSpec
       ├─ ManagementSpec                │  └─ MyComponentCommonSpec (shared)
       └─ MyComponentCommonSpec         │     └─ (your new fields here)
            └─ (your new fields)        └─ Status: MyComponentStatus
                                             ├─ common.Status (conditions)
                                             └─ MyComponentCommonStatus (shared)

DSC status
──────────
ComponentsStatus struct
  └─ DSCMyComponentStatus
       ├─ ManagementSpec
       └─ *MyComponentCommonStatus (pointer, nil when not managed)
```

## Platform-Specific Types

When ODH and RHOAI need different fields or defaults, use build-tag split files:

- `<component>_types.odh.go` — `//go:build !rhoai`
- `<component>_types.rhoai.go` — `//go:build rhoai`

Examples exist for: Workbenches, ModelRegistry, Dashboard.

## API Versioning

- **DSC v2** is the primary/storage version used by controllers
- **DSC v1** exists for backward compatibility with conversion webhooks
- When adding fields to DSC v2, check if the v1↔v2 conversion in `api/datasciencecluster/` needs updating
- Component CRDs are all `v1alpha1`

## Code Generation Commands

```bash
# Full regeneration (most common)
make generate manifests api-docs bundle fmt

# Individual targets
make generate          # DeepCopy methods
make manifests         # CRDs, RBAC, webhooks
make api-docs          # API documentation
make bundle            # OLM bundle (both ODH and RHOAI)
make bundle-all        # Bundle for all platforms
make fmt               # Format code and imports
```

## Verification Checklist

After API changes:

- [ ] `make generate` — no diff in `zz_generated.deepcopy.go`
- [ ] `make manifests` — CRD YAML updated correctly
- [ ] `make api-docs` — docs reflect new fields
- [ ] `make bundle` — CSV and bundle manifests updated
- [ ] `make lint` — passes
- [ ] `make unit-test` — passes
- [ ] New field propagated in `NewCRObject()` (handler)
- [ ] New field used in controller actions if applicable
- [ ] Status field populated in `UpdateDSCStatus()` if applicable
- [ ] v1↔v2 conversion updated (if changing DSC types)
