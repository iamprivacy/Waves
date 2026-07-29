#!/usr/bin/env bash
#
# sync_star_history.sh: copy the star-history chart from the public repo into
# this tree, so the dev repo's README renders the same chart the public one does.
#
# The chart is generated ON the public repo by .github/workflows/star-history.yml
# and committed there (the private mirror meters Actions minutes, and the
# workflow guards on the repository name, so it can only ever run on public).
# That makes public the source of truth for these files and this tree a copy
# that goes stale between runs. Re-run this whenever you want the dev README to
# match; nothing depends on it being current, and release.sh takes the public
# tip's copy regardless of what is here.
#
# Leaves the changes in the working tree. Review and commit them yourself.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)" \
  || { echo "error: not inside a git repository" >&2; exit 1; }
cd "$REPO_ROOT" || { echo "error: cannot cd to repo root" >&2; exit 1; }

PUBLIC_REMOTE="public"
PUBLIC_BRANCH="main"
CHART_DIR="assets/star-history"
SH_START='<!-- star-history:start -->'
SH_END='<!-- star-history:end -->'

git remote get-url "$PUBLIC_REMOTE" >/dev/null 2>&1 \
  || { echo "error: remote '$PUBLIC_REMOTE' is not configured" >&2; exit 1; }

echo "→ fetching $PUBLIC_REMOTE/$PUBLIC_BRANCH ..."
git fetch --quiet "$PUBLIC_REMOTE" "$PUBLIC_BRANCH"
TIP="$(git rev-parse --verify "$PUBLIC_REMOTE/$PUBLIC_BRANCH")"

if ! git ls-tree -r --name-only "$TIP" | grep -q "^${CHART_DIR}/"; then
  echo "nothing to sync: $PUBLIC_REMOTE/$PUBLIC_BRANCH has no $CHART_DIR/ yet."
  echo "Run the Star History workflow on the public repo first."
  exit 0
fi

echo "→ copying $CHART_DIR/ from $(git rev-parse --short "$TIP") ..."
# Drop any file that is no longer part of the chart (a theme you stopped
# rendering) rather than leaving orphans behind, then take the public copies.
rm -rf "$CHART_DIR"
git checkout "$TIP" -- "$CHART_DIR"

# Splice public's marker block into this README. Through a file, not awk -v:
# -v does escape processing and rejects the newlines a multi-line block carries.
BLOCK="$(mktemp)"; trap 'rm -f "$BLOCK" "$BLOCK.readme"' EXIT
git show "$TIP:README.md" \
  | awk -v s="$SH_START" -v e="$SH_END" 'index($0,s){f=1;next} index($0,e){f=0} f' \
  > "$BLOCK"
if [ -s "$BLOCK" ]; then
  awk -v s="$SH_START" -v e="$SH_END" -v f="$BLOCK" \
      'index($0,s){print; while ((getline line < f) > 0) print line; close(f); skip=1; next}
       index($0,e){skip=0} !skip' \
      README.md > "$BLOCK.readme"
  # Refuse to write a README that lost the end marker: without it the next run
  # (and release.sh) would have no block to replace.
  grep -q "$SH_END" "$BLOCK.readme" \
    || { echo "error: spliced README lost the '$SH_END' marker; leaving README.md alone." >&2; exit 1; }
  mv "$BLOCK.readme" README.md
else
  echo "note: $PUBLIC_REMOTE/$PUBLIC_BRANCH README has an empty chart block; README.md left alone."
fi

echo
if git diff --quiet -- "$CHART_DIR" README.md; then
  echo "✓ already in sync with $PUBLIC_REMOTE/$PUBLIC_BRANCH; nothing changed."
else
  echo "✓ synced. Review and commit:"
  git status --short -- "$CHART_DIR" README.md | sed 's/^/    /'
fi
