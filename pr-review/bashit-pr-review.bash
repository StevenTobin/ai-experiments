#!/usr/bin/env bash
# Powerline segment: show count of unread PR review reports from today.
# Reports are "unread" if created today and not yet marked read.
# Mark as read: touch ~/.cache/pr-review/last-read

PR_REVIEW_DIR="${HOME}/git/ai-experiments/pr-review/reports"
PR_REVIEW_LAST_READ="${HOME}/.cache/pr-review/last-read"
PR_REVIEW_CHAR=${POWERLINE_PR_REVIEW_CHAR:="⎘ "}
PR_REVIEW_THEME_PROMPT_COLOR=${POWERLINE_PR_REVIEW_COLOR:=166}

# Cache to avoid hitting the filesystem on every prompt
_PR_REVIEW_CACHE_COUNT=""
_PR_REVIEW_CACHE_TIME=0

function __powerline_pr_review_prompt() {
    local now
    now=$(printf '%(%s)T' -1)

    # Refresh at most every 60 seconds
    if (( now - _PR_REVIEW_CACHE_TIME < 60 )); then
        if [[ -n "$_PR_REVIEW_CACHE_COUNT" ]]; then
            printf '%s|%s' "${PR_REVIEW_CHAR}${_PR_REVIEW_CACHE_COUNT} new" "${PR_REVIEW_THEME_PROMPT_COLOR}"
        fi
        return
    fi
    _PR_REVIEW_CACHE_TIME=$now

    [[ -d "$PR_REVIEW_DIR" ]] || return

    local count=0
    local ref_file="$PR_REVIEW_LAST_READ"

    if [[ -f "$ref_file" ]]; then
        # Count reports newer than last-read marker
        while IFS= read -r -d '' _; do
            ((count++))
        done < <(find "$PR_REVIEW_DIR" -maxdepth 1 -name '*.md' -newer "$ref_file" ! -name 'index.md' -print0 2>/dev/null)
    else
        # No marker: count all of today's reports
        local today
        today=$(date +%Y-%m-%d)
        while IFS= read -r -d '' _; do
            ((count++))
        done < <(find "$PR_REVIEW_DIR" -maxdepth 1 -name "*-${today}.md" ! -name 'index.md' -print0 2>/dev/null)
    fi

    if [[ $count -gt 0 ]]; then
        _PR_REVIEW_CACHE_COUNT="$count"
        printf '%s|%s' "${PR_REVIEW_CHAR}${count} new" "${PR_REVIEW_THEME_PROMPT_COLOR}"
    else
        _PR_REVIEW_CACHE_COUNT=""
    fi
}

# Convenience: mark all current reports as read
pr-review-read() {
    mkdir -p "$(dirname "$PR_REVIEW_LAST_READ")"
    touch "$PR_REVIEW_LAST_READ"
    _PR_REVIEW_CACHE_COUNT=""
    _PR_REVIEW_CACHE_TIME=0
    echo "PR reviews marked as read"
}

# Convenience: list unread reports
pr-review-ls() {
    local ref_file="$PR_REVIEW_LAST_READ"
    if [[ -f "$ref_file" ]]; then
        find "$PR_REVIEW_DIR" -maxdepth 1 -name '*.md' -newer "$ref_file" ! -name 'index.md' -print 2>/dev/null | sort
    else
        local today
        today=$(date +%Y-%m-%d)
        find "$PR_REVIEW_DIR" -maxdepth 1 -name "*-${today}.md" ! -name 'index.md' -print 2>/dev/null | sort
    fi
}
