#!/usr/bin/env bash
# Fail if .gitignore hides anything other than known generated, local or data paths.
# Read-only. Guards against data rules (e.g. `datasets/`) silently matching source
# directories, which let local checks pass while a clean checkout could not build (Session 9).
# Usage: check-ignored-sources.sh  (run from the repository root; `make check` runs it)
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

# Ignored paths that are expected. Anchored extended regexes over repo-relative paths;
# `--directory` collapses wholly-ignored directories into one entry ending in `/`.
allowed=(
  '^\.env(\..+)?$'                      # environment files (.env.example is tracked)
  '^secrets/'                           # generated secrets
  '^data/'                              # application data (bind mounts)
  '^\.venv/' '^\.tools/'                # Python environments
  '^\.coverage$' '^htmlcov/'            # coverage output
  '^\.hypothesis/'                      # hypothesis example database
  '(^|/)\.[a-z_]*cache/'                # .pytest_cache, .mypy_cache, .ruff_cache, .import_linter_cache
  '(^|/)__pycache__/' '\.py[cod]$'      # bytecode
  '(^|/)node_modules/'                  # npm packages
  '^frontend/dist/'                     # frontend build output
  '^\.claude/settings\.local\.json$'    # per-user Claude Code settings (global ignore)
)
pattern="$(IFS='|'; echo "${allowed[*]}")"

unexpected="$(git ls-files --others --ignored --exclude-standard --directory 2>/dev/null \
  | grep -Ev "$pattern" || true)"

if [[ -n "$unexpected" ]]; then
  echo "Ignored paths outside the expected generated/data set (possible hidden source):" >&2
  while IFS= read -r path; do
    rule="$(git check-ignore -v -- "${path%/}" 2>/dev/null | cut -f1 || true)"
    echo "  $path    (rule: ${rule:-unknown})" >&2
  done <<< "$unexpected"
  echo "Track the files (rename the directory or anchor the rule), or extend the allow-list" >&2
  echo "in infra/scripts/check-ignored-sources.sh if the path is genuinely generated." >&2
  exit 1
fi
echo "ignored-file check: no unexpected ignored paths"
