#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CACHE_DIR="${HOME}/.cache/pr-review"
REPORTS_DIR="${SCRIPT_DIR}/reports"
REPOS_CONF="${SCRIPT_DIR}/repos.conf"
PROMPT_FILE="${SCRIPT_DIR}/review-prompt.md"
LOCKFILE="/tmp/pr-review.lock"

BUILD_TIMEOUT=300    # 5 min
TEST_TIMEOUT=600     # 10 min
export CLUSTER_TIMEOUT=900  # 15 min (used in subshell timeouts)

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[OK]${NC} $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }

cleanup() {
    rm -f "$LOCKFILE"
}
trap cleanup EXIT

# Prevent overlapping runs
if [ -e "$LOCKFILE" ]; then
    pid=$(cat "$LOCKFILE" 2>/dev/null || true)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        log_warn "Another run is active (pid $pid), exiting"
        exit 0
    fi
    log_warn "Stale lockfile found, removing"
    rm -f "$LOCKFILE"
fi
echo $$ > "$LOCKFILE"

mkdir -p "$CACHE_DIR" "$REPORTS_DIR"

# ---------- dependency checks ----------

for cmd in gh claude oc git; do
    if [ "$cmd" = "oc" ]; then
        continue  # oc is optional
    fi
    if ! command -v "$cmd" &>/dev/null; then
        log_error "Required command not found: $cmd"
        exit 1
    fi
done

# ---------- resolve GH username ----------

GH_USER=$(gh api user --jq '.login' 2>/dev/null) || {
    log_error "Failed to get GitHub username -- is gh authenticated?"
    exit 1
}
log_info "Running as GitHub user: $GH_USER"

# ---------- cluster availability ----------

CLUSTER_AVAILABLE=false
if command -v oc &>/dev/null && oc whoami &>/dev/null 2>&1; then
    CLUSTER_AVAILABLE=true
    log_info "Cluster access detected: $(oc whoami --show-server 2>/dev/null || echo 'unknown')"
else
    log_info "No cluster access -- skipping cluster smoke tests"
fi

# ---------- helpers ----------

run_with_timeout() {
    local timeout_secs=$1
    shift
    local logfile=$1
    shift
    timeout --kill-after=30 "$timeout_secs" "$@" > "$logfile" 2>&1
}

ensure_repo_clone() {
    local full_repo=$1  # owner/repo
    local repo_name="${full_repo##*/}"
    CLONE_DIR="${CACHE_DIR}/${repo_name}"

    if [ -d "$CLONE_DIR/.git" ]; then
        log_info "Updating cached clone: $repo_name"
        git -C "$CLONE_DIR" fetch --all --prune --quiet 2>/dev/null || true
    else
        log_info "Cloning $full_repo"
        gh repo clone "$full_repo" "$CLONE_DIR" -- --quiet 2>/dev/null
    fi
}

user_has_reviewed() {
    local full_repo=$1
    local pr_number=$2

    local reviews
    reviews=$(gh pr view "$pr_number" \
        --repo "$full_repo" \
        --json reviews,comments \
        --jq "
            (.reviews[]? | select(.author.login == \"$GH_USER\") | .author.login),
            (.comments[]? | select(.author.login == \"$GH_USER\") | .author.login)
        " 2>/dev/null) || true

    [ -n "$reviews" ]
}

report_exists() {
    local repo_name=$1
    local pr_number=$2
    local today
    today=$(date +%Y-%m-%d)
    [ -f "${REPORTS_DIR}/${repo_name}-PR${pr_number}-${today}.md" ]
}

has_make_target() {
    local dir=$1
    local target=$2
    make -C "$dir" -n "$target" &>/dev/null 2>&1
}

# Classify PR changes into tiers to decide how deep the pipeline goes.
#   tier1 = lint + test only (docs, tests, CI, markdown, scripts)
#   tier2 = lint + test + build (Go source not touching API/controller/config)
#   tier3 = full pipeline incl. cluster (API, controllers, RBAC, CRDs, manifests, Dockerfiles)
classify_pr_tier() {
    local diff_file=$1
    local tier="tier1"

    if [ ! -s "$diff_file" ]; then
        echo "tier2"
        return
    fi

    local changed_files
    changed_files=$(grep -E '^\+\+\+ b/' "$diff_file" | sed 's|^+++ b/||') || true

    local file
    while IFS= read -r file; do
        [ -z "$file" ] && continue

        case "$file" in
            # Tier 1 explicitly: test files, docs, CI, scripts, markdown
            tests/*|*_test.go|docs/*|*.md|.github/*|.gitlab-ci.yml|hack/*|*.sh)
                ;;
            # Tier 3: API, controllers, RBAC, CRDs, manifests, Dockerfiles
            api/*|internal/controller/*|config/crd/*|config/rbac/*|config/default/*|\
            config/manager/*|config/samples/*|config/monitoring/*|Dockerfile*|*.Dockerfile)
                echo "tier3"
                return
                ;;
            # Tier 2: Go source, Makefile, build infra
            *.go|Makefile|go.mod|go.sum|pkg/*|cmd/*)
                tier="tier2"
                ;;
            # Anything else stays at current tier
            *)
                ;;
        esac
    done <<< "$changed_files"

    echo "$tier"
}

# ---------- cluster smoke test ----------

run_cluster_smoke_test() {
    local clone_dir=$1
    local repo_name=$2
    local pr_number=$3
    local logfile="${CACHE_DIR}/${repo_name}-PR${pr_number}-cluster.log"
    local dev_img="localhost/${repo_name}:pr${pr_number}"

    echo "=== Cluster Smoke Test ===" > "$logfile"

    CLUSTER_LOG="$logfile"

    if [ "$CLUSTER_AVAILABLE" != "true" ]; then
        echo "SKIPPED: no cluster access" >> "$logfile"
        return 0
    fi

    if ! has_make_target "$clone_dir" deploy; then
        echo "SKIPPED: no 'deploy' make target (not an operator repo)" >> "$logfile"
        return 0
    fi

    local cluster_failed=false

    (
        cd "$clone_dir"

        echo "--- Building operator image ---" >> "$logfile"
        if ! timeout --kill-after=30 "$BUILD_TIMEOUT" \
            make image-build IMG="$dev_img" >> "$logfile" 2>&1; then
            echo "FAILED: image build" >> "$logfile"
            exit 1
        fi

        echo "--- Deploying operator ---" >> "$logfile"
        if ! timeout --kill-after=30 120 \
            make deploy IMG="$dev_img" >> "$logfile" 2>&1; then
            echo "FAILED: operator deploy" >> "$logfile"
            make undeploy >> "$logfile" 2>&1 || true
            exit 1
        fi

        echo "--- Waiting for operator pod ---" >> "$logfile"
        local ns
        ns=$(grep -r 'OPERATOR_NAMESPACE\|namespace:' config/default/kustomization.yaml 2>/dev/null \
            | grep -oP '(?<=namespace: ).*' | head -1) || ns="opendatahub-operator-system"

        if ! timeout 120 bash -c "
            until oc get pods -n '$ns' -l control-plane=controller-manager \
                --no-headers 2>/dev/null | grep -q Running; do
                sleep 5
            done
        "; then
            echo "FAILED: operator pod never reached Running" >> "$logfile"
            oc get pods -n "$ns" >> "$logfile" 2>&1 || true
            oc get events -n "$ns" --sort-by='.lastTimestamp' | tail -20 >> "$logfile" 2>&1 || true
            make undeploy >> "$logfile" 2>&1 || true
            exit 1
        fi

        echo "--- Creating sample DSC/DSCI ---" >> "$logfile"
        local dsci_sample dsc_sample
        dsci_sample=$(find config/samples -name '*dscinitial*' -o -name '*dsci*' 2>/dev/null | head -1)
        dsc_sample=$(find config/samples -name '*datasciencecluster*' -o -name '*dsc*' 2>/dev/null | grep -v dsci | head -1)

        if [ -n "$dsci_sample" ]; then
            oc apply -f "$dsci_sample" >> "$logfile" 2>&1 || true
        fi
        if [ -n "$dsc_sample" ]; then
            oc apply -f "$dsc_sample" >> "$logfile" 2>&1 || true
        fi

        echo "--- Waiting for conditions (up to 5 min) ---" >> "$logfile"
        sleep 30  # give reconciler time to start

        local dsc_name
        dsc_name=$(oc get datasciencecluster -o name 2>/dev/null | head -1) || true
        if [ -n "$dsc_name" ]; then
            local end_time=$((SECONDS + 270))
            while [ $SECONDS -lt $end_time ]; do
                local conditions
                conditions=$(oc get "$dsc_name" -o jsonpath='{.status.conditions}' 2>/dev/null) || true
                echo "  Conditions at $(date +%H:%M:%S): $conditions" >> "$logfile"
                if echo "$conditions" | grep -q '"status":"True".*"type":"Ready"'; then
                    echo "SUCCESS: DSC reached Ready" >> "$logfile"
                    break
                fi
                sleep 15
            done
        fi

        echo "--- Pod status ---" >> "$logfile"
        oc get pods -A -l app.kubernetes.io/part-of=opendatahub-operator --no-headers >> "$logfile" 2>&1 || true

        echo "--- Recent events ---" >> "$logfile"
        oc get events -A --sort-by='.lastTimestamp' --field-selector type=Warning 2>/dev/null | tail -20 >> "$logfile" 2>&1 || true

        echo "--- Cleaning up ---" >> "$logfile"
        [ -n "$dsc_sample" ] && oc delete -f "$dsc_sample" --ignore-not-found >> "$logfile" 2>&1 || true
        [ -n "$dsci_sample" ] && oc delete -f "$dsci_sample" --ignore-not-found >> "$logfile" 2>&1 || true
        make undeploy >> "$logfile" 2>&1 || true

    ) || cluster_failed=true

    if [ "$cluster_failed" = true ]; then
        echo "CLUSTER SMOKE TEST: FAILED (see details above)" >> "$logfile"
    fi
}

# ---------- review a single PR ----------

review_pr() {
    local full_repo=$1
    local pr_json=$2
    local repo_name="${full_repo##*/}"

    local pr_number title author url head_ref
    pr_number=$(echo "$pr_json" | jq -r '.number')
    title=$(echo "$pr_json" | jq -r '.title')
    author=$(echo "$pr_json" | jq -r '.author.login')
    url=$(echo "$pr_json" | jq -r '.url')
    head_ref=$(echo "$pr_json" | jq -r '.headRefName')

    log_info "Processing: ${repo_name} PR #${pr_number} - ${title}"

    # --- skip checks ---
    if report_exists "$repo_name" "$pr_number"; then
        log_info "  Report already exists for today, skipping"
        return 0
    fi

    if user_has_reviewed "$full_repo" "$pr_number"; then
        log_info "  You have already reviewed/commented, skipping"
        return 0
    fi

    # --- checkout ---
    ensure_repo_clone "$full_repo"
    local clone_dir="$CLONE_DIR"
    (
        cd "$clone_dir"
        gh pr checkout "$pr_number" --force 2>/dev/null || {
            git checkout "$head_ref" 2>/dev/null || {
                log_error "  Failed to checkout PR branch"
                return 1
            }
        }
    )

    # --- get diff (needed early for tier classification) ---
    local diff_file="${CACHE_DIR}/${repo_name}-PR${pr_number}.diff"
    gh pr diff "$pr_number" --repo "$full_repo" > "$diff_file" 2>/dev/null || true

    # --- classify change tier ---
    #   tier1 = lint + test only (docs, tests, CI, scripts)
    #   tier2 = lint + test + build (Go source, pkg/, Makefile)
    #   tier3 = full pipeline incl. cluster (API, controllers, CRDs, manifests)
    local tier
    tier=$(classify_pr_tier "$diff_file")
    log_info "  Change tier: ${tier}"

    # --- gated pipeline: lint -> test -> build (tier2+) -> cluster (tier3) ---
    local build_log="${CACHE_DIR}/${repo_name}-PR${pr_number}-build.log"
    local test_log="${CACHE_DIR}/${repo_name}-PR${pr_number}-test.log"
    local lint_log="${CACHE_DIR}/${repo_name}-PR${pr_number}-lint.log"
    local lint_status="PASS" test_status="PASS" build_status="SKIP"
    local gate_passed=true

    # Stage 1: lint (always runs -- cheapest)
    log_info "  [1/4] Running lint..."
    if has_make_target "$clone_dir" lint; then
        if ! run_with_timeout "$BUILD_TIMEOUT" "$lint_log" make -C "$clone_dir" lint; then
            lint_status="FAIL"
            gate_passed=false
        fi
    else
        echo "No lint target" > "$lint_log"
        lint_status="N/A"
    fi

    # Stage 2: unit tests (always runs -- compiles only test binaries)
    log_info "  [2/4] Running unit tests..."
    if has_make_target "$clone_dir" unit-test; then
        if ! run_with_timeout "$TEST_TIMEOUT" "$test_log" make -C "$clone_dir" unit-test; then
            test_status="FAIL"
            gate_passed=false
        fi
    elif has_make_target "$clone_dir" test; then
        if ! run_with_timeout "$TEST_TIMEOUT" "$test_log" make -C "$clone_dir" test; then
            test_status="FAIL"
            gate_passed=false
        fi
    else
        echo "No test target" > "$test_log"
        test_status="N/A"
    fi

    # Stage 3: full build (tier2+ only, and only if lint+tests passed)
    if [ "$tier" = "tier1" ]; then
        log_info "  [3/4] Skipping build -- tier1 changes (docs/tests/CI only)"
        echo "SKIPPED: tier1 changes do not require a build" > "$build_log"
    elif [ "$gate_passed" = true ]; then
        log_info "  [3/4] Running build..."
        build_status="PASS"
        if has_make_target "$clone_dir" build; then
            if ! run_with_timeout "$BUILD_TIMEOUT" "$build_log" make -C "$clone_dir" build; then
                build_status="FAIL"
                gate_passed=false
            fi
        else
            echo "No build target" > "$build_log"
            build_status="N/A"
        fi
    else
        log_warn "  [3/4] Skipping build -- lint or tests failed"
        echo "SKIPPED: lint or unit tests failed" > "$build_log"
    fi

    # Stage 4: cluster smoke test (tier3 only, and only if build passed)
    local cluster_log="${CACHE_DIR}/${repo_name}-PR${pr_number}-cluster.log"
    if [ "$tier" != "tier3" ]; then
        log_info "  [4/4] Skipping cluster test -- not tier3 (no API/controller/manifest changes)"
        echo "SKIPPED: ${tier} changes do not require cluster testing" > "$cluster_log"
    elif [ "$gate_passed" = true ]; then
        log_info "  [4/4] Running cluster smoke test..."
        run_cluster_smoke_test "$clone_dir" "$repo_name" "$pr_number"
        cluster_log="$CLUSTER_LOG"
    else
        log_warn "  [4/4] Skipping cluster smoke test -- earlier stage failed"
        echo "SKIPPED: earlier pipeline stage failed" > "$cluster_log"
    fi

    # --- CodeRabbit review (optional) ---
    local coderabbit_log="${CACHE_DIR}/${repo_name}-PR${pr_number}-coderabbit.log"
    local coderabbit_status="SKIP"
    if command -v coderabbit &>/dev/null; then
        log_info "  Running CodeRabbit review..."
        local base_branch
        base_branch=$(gh pr view "$pr_number" --repo "$full_repo" --json baseRefName --jq '.baseRefName' 2>/dev/null) || base_branch="main"

        if run_with_timeout "$TEST_TIMEOUT" "$coderabbit_log" \
            coderabbit review --plain --base "origin/${base_branch}" \
                --config "${clone_dir}/AGENTS.md" \
                --cwd "$clone_dir"; then
            coderabbit_status="DONE"
        else
            coderabbit_status="FAIL"
        fi
    else
        echo "SKIPPED: coderabbit CLI not installed" > "$coderabbit_log"
    fi

    # --- compose context for Claude ---
    local context_file="${CACHE_DIR}/${repo_name}-PR${pr_number}-context.md"
    cat > "$context_file" <<CONTEXT_EOF
# PR #${pr_number}: ${title}
- **Repo**: ${full_repo}
- **Author**: ${author}
- **URL**: ${url}
- **Branch**: ${head_ref}

## Pipeline
- Change Tier: ${tier} (tier1=lint+test, tier2=+build, tier3=+cluster)
- Lint: ${lint_status}
- Unit Tests: ${test_status}
- Build: ${build_status}
- CodeRabbit: ${coderabbit_status}


### Build Output (last 100 lines)
\`\`\`
$(tail -100 "$build_log" 2>/dev/null || echo "No output")
\`\`\`

### Lint Output (last 100 lines)
\`\`\`
$(tail -100 "$lint_log" 2>/dev/null || echo "No output")
\`\`\`

### Test Output (last 100 lines)
\`\`\`
$(tail -100 "$test_log" 2>/dev/null || echo "No output")
\`\`\`

## Cluster Smoke Test
\`\`\`
$(cat "$cluster_log" 2>/dev/null || echo "Not run")
\`\`\`

## CodeRabbit Review (${coderabbit_status})
\`\`\`
$(tail -200 "$coderabbit_log" 2>/dev/null || echo "Not run")
\`\`\`

## PR Diff
\`\`\`diff
$(cat "$diff_file" 2>/dev/null || echo "No diff available")
\`\`\`
CONTEXT_EOF

    # --- run Claude review ---
    log_info "  Running AI review with Claude..."
    local today
    today=$(date +%Y-%m-%d)
    local report_file="${REPORTS_DIR}/${repo_name}-PR${pr_number}-${today}.md"

    local review_output
    review_output=$(cat "$context_file" | claude -p \
        --system-prompt "$(cat "$PROMPT_FILE")" \
        --max-budget-usd 1.00 \
        --allowedTools "" \
        2>/dev/null) || {
        log_error "  Claude review failed"
        review_output="*AI review failed -- see logs*"
    }

    # --- write report ---
    cat > "$report_file" <<REPORT_EOF
# PR #${pr_number}: ${title}
- **Repo**: ${full_repo}
- **Author**: ${author}
- **URL**: ${url}
- **Reviewed**: $(date '+%Y-%m-%d %H:%M')

## Pipeline (${tier})
| Check | Status |
|-------|--------|
| Lint | ${lint_status} |
| Unit Tests | ${test_status} |
| Build | ${build_status} |
| CodeRabbit | ${coderabbit_status} |

## Cluster Smoke Test
\`\`\`
$(cat "$cluster_log" 2>/dev/null || echo "Not run")
\`\`\`

## CodeRabbit Review
\`\`\`
$(tail -200 "$coderabbit_log" 2>/dev/null || echo "Not run")
\`\`\`

## AI Review

${review_output}
REPORT_EOF

    log_success "  Report written: ${report_file}"

    # --- update index ---
    local index_file="${REPORTS_DIR}/index.md"
    if [ ! -f "$index_file" ]; then
        {
            echo "# PR Review Index"
            echo ""
            echo "| Date | Repo | PR | Title | Author | Risk |"
            echo "|------|------|----|-------|--------|------|"
        } > "$index_file"
    fi

    local risk_level
    risk_level=$(echo "$review_output" | grep -ioP '(LOW|MEDIUM|HIGH)' | head -1) || risk_level="?"

    echo "| ${today} | ${repo_name} | [#${pr_number}](${url}) | ${title} | ${author} | ${risk_level} |" >> "$index_file"

    # --- cleanup temp files ---
    rm -f "$context_file" "$diff_file"
}

# ---------- main loop ----------

main() {
    log_info "=== PR Review Bot starting at $(date) ==="

    if [ ! -f "$REPOS_CONF" ]; then
        log_error "Repos config not found: $REPOS_CONF"
        exit 1
    fi

    if [ ! -f "$PROMPT_FILE" ]; then
        log_error "Prompt template not found: $PROMPT_FILE"
        exit 1
    fi

    local total_reviewed=0

    while IFS= read -r full_repo || [ -n "$full_repo" ]; do
        # skip blanks and comments
        full_repo=$(echo "$full_repo" | sed 's/#.*//' | xargs)
        [ -z "$full_repo" ] && continue

        local repo_name="${full_repo##*/}"
        log_info "Checking ${full_repo} for team-compass PRs..."

        local pr_list
        pr_list=$(gh pr list \
            --repo "$full_repo" \
            --label team-compass \
            --state open \
            --json number,title,headRefName,url,author \
            2>/dev/null) || {
            log_warn "  Failed to list PRs for $full_repo"
            continue
        }

        local pr_count
        pr_count=$(echo "$pr_list" | jq 'length')

        if [ "$pr_count" -eq 0 ]; then
            log_info "  No team-compass PRs found"
            continue
        fi

        log_info "  Found $pr_count team-compass PR(s)"

        echo "$pr_list" | jq -c '.[]' | while IFS= read -r pr_json; do
            review_pr "$full_repo" "$pr_json" || {
                local num
                num=$(echo "$pr_json" | jq -r '.number')
                log_warn "  Failed to review PR #${num}, continuing..."
            }
            total_reviewed=$((total_reviewed + 1))
        done
    done < "$REPOS_CONF"

    log_info "=== PR Review Bot finished at $(date) ==="
}

# Allow running a single PR: ./pr-review.sh owner/repo 123
if [ $# -eq 2 ]; then
    full_repo="$1"
    pr_number="$2"
    log_info "Single-PR mode: $full_repo #$pr_number"
    pr_json=$(gh pr view "$pr_number" \
        --repo "$full_repo" \
        --json number,title,headRefName,url,author 2>/dev/null) || {
        log_error "Failed to fetch PR #$pr_number from $full_repo"
        exit 1
    }
    review_pr "$full_repo" "$pr_json"
elif [ $# -eq 0 ]; then
    main
else
    echo "Usage: $0 [owner/repo pr_number]"
    echo "  No args: scan all repos in repos.conf"
    echo "  Two args: review a single PR"
    exit 1
fi
