#!/usr/bin/env bash
set -euo pipefail

# Commit one generated file on top of the latest remote branch.
# This avoids non-fast-forward failures when another workflow or a manual
# website edit advances main while a long-running model job is still running.
#
# Usage:
#   bash scripts/commit_generated_file.sh \
#     path/to/generated-file.json \
#     "commit message" \
#     main

target="${1:?Generated file path is required.}"
message="${2:?Commit message is required.}"
branch="${3:-main}"

if [[ ! -f "$target" ]]; then
  echo "::error::Generated file does not exist: $target"
  exit 1
fi

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

tmp_root="${RUNNER_TEMP:-/tmp}"
tmp_file="${tmp_root}/aieo-generated-$(basename "$target")-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}"
cp "$target" "$tmp_file"

for attempt in 1 2 3 4 5; do
  echo "Commit/push attempt ${attempt}/5 for ${target}"

  # Start from the newest remote main so unrelated website/data commits
  # made during the model run are retained.
  git fetch --no-tags origin "$branch"
  git reset --hard "origin/$branch"

  mkdir -p "$(dirname "$target")"
  cp "$tmp_file" "$target"
  git add -- "$target"

  if git diff --cached --quiet; then
    echo "Remote branch already contains this generated output."
    exit 0
  fi

  git commit -m "$message"

  if git push origin "HEAD:$branch"; then
    echo "Pushed generated output successfully."
    exit 0
  fi

  echo "Remote advanced again before push; retrying on the newest ${branch}."
  sleep $((attempt * 5))
done

echo "::error::Could not push ${target} after 5 attempts."
exit 1
