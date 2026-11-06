#!/usr/bin/env bash
# Generate per-environment secrets under secrets/<env>/ (git-ignored). Never overwrites.
# Files are 0644 inside a 0700 directory: containers (postgres uid 70, app uid 10001) must
# read the bind-mounted files; the 0700 directory keeps other host users out.
set -euo pipefail
env="${1:?usage: init-secrets.sh dev|prod}"
case "$env" in dev|prod) ;; *) echo "unknown env: $env" >&2; exit 2 ;; esac
dir="$(cd "$(dirname "$0")/../.." && pwd)/secrets/$env"
mkdir -p "$dir"; chmod 700 "$(dirname "$dir")" "$dir"
for name in pg_superuser_password db_owner_password db_app_password initial_admin_password; do
  f="$dir/$name"
  if [[ -e "$f" ]]; then echo "exists:  $f"; continue; fi
  umask 022
  python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > "$f"
  chmod 644 "$f"
  echo "created: $f"
done
