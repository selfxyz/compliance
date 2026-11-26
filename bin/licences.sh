#!/usr/bin/env bash
# Licence check for one workspace: scan the head commit, scan the base commit, apply the policy.
# Used by the workflow and by tests/test_scan.py, so both run the same commands.
#
# Usage: licences.sh TARGET_REPO_DIR OUT_DIR
# Env:   HEAD BASE FULL SCAN_ARGS(json list) REPO WORKSPACE PROFILE VISIBILITY MODE FILE_CHECKS [OFFLINE=true]
# Exit:  1 only in block mode with a new `fail` finding, or when a step itself breaks.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
target=$(cd "$1" && pwd); mkdir -p "$2"; out=$(cd "$2" && pwd)

args=()
while IFS= read -r line; do args+=("$line"); done < <(python3 -c 'import json, os; print("\n".join(json.loads(os.environ["SCAN_ARGS"])))')
if [ "${OFFLINE:-}" = true ]; then args+=(--offline); fi
scan() { (cd "$target" && python3 "$ROOT/scanner/scan.py" "$1" "${args[@]}" --out "$out/$2" --csv "$out/$2/deps.csv"); }

scan "$HEAD" head
policy=(--repo "$REPO" --workspace "$WORKSPACE" --profile "$PROFILE" --visibility "$VISIBILITY" --mode "$MODE"
        --config "$target/.github/compliance.json" --head "$out/head/deps.csv" --head-ref "$HEAD"
        --out "$out/findings.json" --summary "$out/summary.md")
if [ "$FILE_CHECKS" = true ]; then policy+=(--checkout "$target"); fi

# Pushes and PRs: also scan where the change started, so only findings it adds count as new.
base=""
if [ "$FULL" != true ]; then base=$(git -C "$target" merge-base "$BASE" "$HEAD" 2>/dev/null || true); fi
if [ -n "$base" ] && [ "$base" != "$(git -C "$target" rev-parse "$HEAD")" ]; then
  if scan "$base" base; then
    policy+=(--base "$out/base/deps.csv" --base-ref "$base")
  else
    rm -rf "$out/base"
    echo "::notice::this workspace cannot be scanned at the base commit (it is new); every finding counts as new"
  fi
fi

rc=0
python3 "$ROOT/policy/policy.py" "${policy[@]}" > /dev/null || rc=$?
if [ -f "$out/summary.md" ] && [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then cat "$out/summary.md" >> "$GITHUB_STEP_SUMMARY"; fi
exit $rc
