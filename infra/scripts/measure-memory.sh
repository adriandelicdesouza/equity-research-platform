#!/usr/bin/env bash
# Snapshot memory for this project's containers (read-only; no side effects).
# Reads cgroup v2 memory.current / memory.peak / memory.max from the host and prints a
# Markdown table row block, plus host-level availability. Usage: measure-memory.sh <label>
set -euo pipefail
label="${1:-snapshot}"
project="${COMPOSE_PROJECT_NAME:-erp-dev}"
mib() { awk -v b="$1" 'BEGIN { if (b=="max") print "max"; else printf "%.1f", b/1048576 }'; }
echo "### ${label} — $(date -u +%Y-%m-%dT%H:%M:%SZ) (project ${project})"
echo
echo "| Container | Current (MiB) | Peak since start (MiB) | Limit (MiB) |"
echo "|---|---|---|---|"
total_cur=0; total_peak=0
for id in $(docker ps -q --filter "label=com.docker.compose.project=${project}"); do
  name=$(docker inspect -f '{{.Name}}' "$id" | sed 's#^/##')
  full=$(docker inspect -f '{{.Id}}' "$id")
  cg="/sys/fs/cgroup/system.slice/docker-${full}.scope"
  cur=$(cat "$cg/memory.current"); peak=$(cat "$cg/memory.peak"); max=$(cat "$cg/memory.max")
  total_cur=$((total_cur + cur)); total_peak=$((total_peak + peak))
  echo "| ${name} | $(mib "$cur") | $(mib "$peak") | $(mib "$max") |"
done
echo "| **Total** | **$(mib $total_cur)** | **$(mib $total_peak)** (sum of per-container peaks) | |"
echo
avail=$(awk '/MemAvailable/ {printf "%.0f", $2/1024}' /proc/meminfo)
swap=$(awk '/SwapTotal/ {t=$2} /SwapFree/ {f=$2} END {printf "%.0f", (t-f)/1024}' /proc/meminfo)
mc=$(systemctl is-active minecraft 2>/dev/null || true)
echo "Host: MemAvailable ${avail} MiB, swap used ${swap} MiB, minecraft.service: ${mc}"
echo
