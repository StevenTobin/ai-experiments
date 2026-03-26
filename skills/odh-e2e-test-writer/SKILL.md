---
name: odh-e2e-test-writer
description: >-
  Write, modify, and run end-to-end tests for the Open Data Hub operator.
  Use when adding e2e tests for new components, modifying test suites,
  debugging test failures, or running e2e tests locally.
---

# ODH E2E Test Writer

Guide for writing and running e2e tests in the opendatahub-operator.

## Test Architecture Overview

Tests live in `tests/e2e/` and use Go's `testing` package with Gomega matchers and jq-based assertions. **Not Ginkgo** — standard `*testing.T` with a custom `RunTestCases` runner.

### Key abstractions

| Type | File | Purpose |
|------|------|---------|
| `TestContext` | `test_context_test.go` | Base context: client, namespaces, DSC/DSCI refs, resource helpers |
| `ComponentTestCtx` | `components_test.go` | Extends TestContext with GVK, NamespacedName, shared component validations |
| `RunTestCases` | `helper_test.go` | Runs `[]TestCase` sequentially with panic handling and diagnostics |
| `TestCase` | `controller_test.go` | `{name string, testFn func(*testing.T)}` |
| jq matchers | `pkg/utils/test/matchers/jq` | `jq.Match()` for unstructured resource assertions |

### Test group execution order

Defined in `controller_test.go` → `TestOdhOperator`:
1. Dependant operators
2. DSC management
3. Operator manager
4. DSC validation
5. **Components** (parallel within scenario groups)
6. **Services** (parallel)
7. Webhooks
8. Resilience
9. Upgrade (v2→v3)
10. Deletion

Components run in multiple scenario groups to avoid dependency conflicts. Tests within a group run in parallel; groups run sequentially.

## Writing a Component Test Suite

### Minimal example (follow Ray pattern)

Create `tests/e2e/<component>_test.go`:

```go
package e2e_test

import (
    "testing"

    "github.com/stretchr/testify/require"

    componentApi "github.com/opendatahub-io/opendatahub-operator/v2/api/components/v1alpha1"
)

type MyComponentTestCtx struct {
    *ComponentTestCtx
}

func myComponentTestSuite(t *testing.T) {
    t.Helper()

    ct, err := NewComponentTestCtx(t, &componentApi.MyComponent{})
    require.NoError(t, err)

    componentCtx := MyComponentTestCtx{
        ComponentTestCtx: ct,
    }

    testCases := []TestCase{
        {"Validate component enabled", componentCtx.ValidateComponentEnabled},
        {"Validate operands have OwnerReferences", componentCtx.ValidateOperandsOwnerReferences},
        {"Validate update operand resources", componentCtx.ValidateUpdateDeploymentsResources},
        {"Validate component releases", componentCtx.ValidateComponentReleases},
        {"Validate resource deletion recovery", componentCtx.ValidateAllDeletionRecovery},
        {"Validate component disabled", componentCtx.ValidateComponentDisabled},
    }

    RunTestCases(t, testCases)
}
```

### Register the suite

In `controller_test.go`, add to the `Components` scenarios:

```go
Components = TestGroup{
    scenarios: []map[string]TestFn{
        {
            // existing entries...
            componentApi.MyComponentComponentName: myComponentTestSuite,
        },
        // ... dependency-separated groups
    },
}
```

Place in the first scenario group unless it has dependencies on other components.

### Update DSC creation

In `helper_test.go`, update `CreateDSC()` to set the new component to `Managed`.

## Shared Component Validations

`ComponentTestCtx` provides reusable test methods — prefer these over writing custom versions:

- `ValidateComponentEnabled` — enables component, checks Ready condition
- `ValidateComponentDisabled` — disables component, checks resources removed
- `ValidateOperandsOwnerReferences` — deployments have correct ownerRef
- `ValidateUpdateDeploymentsResources` — tests replica count update
- `ValidateComponentReleases` — checks release info in DSC status
- `ValidateAllDeletionRecovery` — ConfigMap, Service, RBAC, SA, Deployment deletion/recreation
- `ValidateCRDsReinstated` — CRD removal and recreation on disable/enable

### Test tier annotations

Use `skipUnless(t, Smoke, Tier1)` at the top of test functions to control which tiers run a test. Available tiers: `Smoke`, `Tier1`, `Tier2`, `Tier3`.

## Writing Custom Assertions

### jq matchers

The jq matcher package enables expressive assertions on unstructured resources:

```go
// Simple field match
jq.Match(`.status.conditions[] | select(.type == "Ready") | .status == "True"`)

// Parameterized
jq.Match(`.spec.components.%s.managementState == "%s"`, componentName, operatorv1.Managed)

// Combined with Gomega
WithCondition(
    And(
        HaveLen(1),
        HaveEach(And(
            jq.Match(`.metadata.ownerReferences[0].kind == "%s"`, "DataScienceCluster"),
            jq.Match(`.status.conditions[] | select(.type == "Ready") | .status == "True"`),
        )),
    ),
)
```

### Resource helpers (TestContext)

```go
// Ensure resource exists with conditions
tc.EnsureResourceExists(
    WithMinimalObject(gvk.Deployment, nn),
    WithCondition(jq.Match(`.status.availableReplicas > 0`)),
)

// Ensure resources (list) exist
tc.EnsureResourcesExist(
    WithMinimalObject(gvk.Deployment, nn),
    WithListOptions(&client.ListOptions{...}),
    WithCondition(HaveEach(...)),
)

// Ensure resource is gone
tc.EnsureResourcesGone(WithMinimalObject(gvk.MyComponent, nn))

// Patch a resource and wait for conditions
tc.EventuallyResourcePatched(
    WithMinimalObject(gvk.DataScienceCluster, tc.DataScienceClusterNamespacedName),
    WithMutateFunc(testf.Transform(`.spec.components.%s.managementState = "%s"`, name, state)),
    WithCondition(jq.Match(...)),
)

// Delete and verify recreation
tc.EnsureResourceDeletedThenRecreated(
    WithMinimalObject(gvk.ConfigMap, nn),
)
```

## Running Tests

### Full e2e suite (requires cluster with operator deployed)

```bash
make e2e-test
```

### Single test

```bash
make e2e-test-single TEST="TestName"
```

### Setup cluster only (create DSCI/DSC)

```bash
make e2e-setup-cluster
```

### Configuration via environment

All prefixed with `E2E_TEST_`:

| Env var | Default | Purpose |
|---------|---------|---------|
| `E2E_TEST_DELETION_POLICY` | `always` | `always` / `on-failure` / `never` |
| `E2E_TEST_OPERATOR_NAMESPACE` | varies | Operator namespace |
| `E2E_TEST_APPS_NAMESPACE` | varies | Application namespace |
| `E2E_TEST_COMPONENTS` | all | Comma-separated component filter, prefix `!` to exclude |
| `E2E_TEST_SERVICES` | all | Comma-separated service filter |

### Via local.mk

```makefile
E2E_TEST_FLAGS="--deletion-policy=never" -timeout 15m
```

## Subcomponent Tests

For components that are subcomponents of a parent (e.g., ModelsAsService under KServe):

```go
ct, err := NewSubComponentTestCtx(t, &componentApi.ModelsAsService{}, "Kserve", "modelsAsService")
```

Then use `ValidateSubComponentEnabled`, `ValidateSubComponentDisabled`, `UpdateSubComponentStateInDataScienceCluster`.

## E2E Update Requirements

PRs that change behavior generally **must** include e2e updates. A GitHub Action enforces this. Legitimate exceptions (docs-only, pure refactors with unit coverage, deps without behavior change) require checking the "Skip requirement to update E2E test suite" checkbox in the PR template with justification.

See [docs/e2e-update-requirement-guidelines.md](docs/e2e-update-requirement-guidelines.md).
