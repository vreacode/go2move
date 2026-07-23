#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_URL="git@github-carmove:vreacode/carmove.git"
COMMIT_MESSAGE="${1:-chore: sync carmove updates}"

cd "${REPO_ROOT}"

if [[ "$(git branch --show-current)" != "main" ]]; then
  echo "Refusing to sync: current branch is not main." >&2
  exit 1
fi

if ! git ls-remote "${REMOTE_URL}" HEAD >/dev/null 2>&1; then
  echo "GitHub SSH authentication is not ready. Add the generated public key to vreacode/carmove first." >&2
  exit 2
fi

# Track normal source changes and project files. Deliberately exclude loose
# root-level books/PDFs and other workspace material from automatic commits.
git add -u
for path in apps deployment ros2 tools docs; do
  [[ -e "${path}" ]] && git add -- "${path}"
done

staged_files="$(git diff --cached --name-only)"
if [[ -z "${staged_files}" ]]; then
  echo "Nothing to sync."
  exit 0
fi

if printf '%s\n' "${staged_files}" | rg -i '(^|/)(\.env($|\.)|.*(secret|password|private[_-]?key|access[_-]?token).*)' >/dev/null; then
  echo "Refusing to sync: a staged path looks like a secret-bearing file." >&2
  git diff --cached --name-only >&2
  exit 3
fi

git diff --cached --check
git commit -m "${COMMIT_MESSAGE}"
git remote set-url origin "${REMOTE_URL}"
git push origin main

echo "GitHub sync complete: ${REMOTE_URL} (main)."
