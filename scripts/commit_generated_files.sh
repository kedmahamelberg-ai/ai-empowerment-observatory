#!/usr/bin/env bash
set -euo pipefail

# Commit multiple generated files on top of the latest remote branch.
# Usage:
#   bash scripts/commit_generated_files.sh \
#     "commit message" main file1 file2 [file3 ...]

message="${1:?Commit message is required.}"
branch="${2:-main}"
shift 2

if [[ "$#" -lt 1 ]]; then
  echo "::error::At least one generated file is required."
  exit 1
fi

files=("$@")
tmp_root="${RUNNER_TEMP:-/tmp}/aieo-generated-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}"
rm -rf "$tmp_root"
mkdir -p "$tmp_root"

for file in "${files[@]}"; do
  if [[ ! -f "$file" ]]; then
    echo "::error::Generated file does not exist: $file"
    exit 1
  fi
  mkdir -p "$tmp_root/$(dirname "$file")"
  cp "$file" "$tmp_root/$file"
done

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

for attempt in 1 2 3 4 5; do
  echo "Commit/push attempt ${attempt}/5 for ${files[*]}"

  git fetch --no-tags origin "$branch"
  git reset --hard "origin/$branch"

  for file in "${files[@]}"; do
    mkdir -p "$(dirname "$file")"
    cp "$tmp_root/$file" "$file"
  done

  git add -- "${files[@]}"

  if git diff --cached --quiet; then
    echo "Remote branch already contains these generated outputs."
    exit 0
  fi

  git commit -m "$message"

  if git push origin "HEAD:$branch"; then
    echo "Pushed generated outputs successfully."
    exit 0
  fi

  echo "Remote advanced again before push; retrying on newest ${branch}."
  sleep $((attempt * 5))
done

echo "::error::Could not push generated outputs after 5 attempts."
exit 1
