#!/usr/bin/env bash
# Build one upload-ready zip per skill in skills/* for the cloud web interface claude.ai/code.
#
# Output layout matches the claude.ai/code  skill upload format:
#   <skill-name>.zip
#     <skill-name>/
#       SKILL.md
#       ...supporting files
#
# Special bundling:
#   review-facts -> includes agents/fact-reviewer.md (the subagent it spawns)
#   task         -> includes commands/*.md + commands/handlers/*.md and a
#                   CLAUDE-AI-NOTE.md explaining that slash commands map to
#                   reference procedures on claude.ai
#
# Usage:
#   scripts/build-claude-ai-zips.sh [output_dir]
#   (default output_dir: ./dist/claude-ai-skills)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${1:-$REPO_ROOT/dist/claude-ai-skills}"

cd "$REPO_ROOT"

if ! command -v zip >/dev/null 2>&1; then
  echo "error: 'zip' not found in PATH" >&2
  exit 1
fi

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

stage_skill() {
  # Copy skills/<name> into the staging dir, returning the path.
  local name="$1"
  local dest="$OUT_DIR/$name"
  cp -R "skills/$name" "$dest"
  echo "$dest"
}

zip_skill() {
  local name="$1"
  (cd "$OUT_DIR" && zip -rq "$name.zip" "$name" -x "*.DS_Store")
  rm -rf "${OUT_DIR:?}/$name"
}

bundle_review_facts() {
  local dest
  dest="$(stage_skill review-facts)"
  mkdir -p "$dest/agents"
  cp agents/fact-reviewer.md "$dest/agents/"
  zip_skill review-facts
}

bundle_task() {
  local dest
  dest="$(stage_skill task)"
  mkdir -p "$dest/commands/handlers"
  cp commands/*.md "$dest/commands/"
  cp commands/handlers/*.md "$dest/commands/handlers/"
  cat > "$dest/CLAUDE-AI-NOTE.md" <<'EOF'
# Using this skill on claude.ai

This skill was authored as a Claude Code plugin. The slash commands it
documents (`/add-task`, `/claim-task`, `/list-tasks`, `/process-tasks`,
`/promote-tasks`, `/task-config`) do not exist as dispatchers on claude.ai.

Treat the files under `commands/` (and `commands/handlers/`) as reference
procedures. Invoke them by asking naturally, e.g.:

  - "Add a task for X" -> follow commands/add-task.md
  - "List my tasks"    -> follow commands/list-tasks.md
  - "Claim a task"     -> follow commands/claim-task.md

The Linear handler procedures require the Linear MCP connector to be
enabled in the claude.ai workspace.
EOF
  zip_skill task
}

bundle_default() {
  # Skill with no extra bundling — just zip what's in skills/<name>/.
  local name="$1"
  stage_skill "$name" >/dev/null
  zip_skill "$name"
}

# Walk every skill in skills/* and dispatch to the right bundler.
for skill_dir in skills/*/; do
  name="$(basename "$skill_dir")"
  if [[ ! -f "skills/$name/SKILL.md" ]]; then
    echo "skip: skills/$name has no SKILL.md" >&2
    continue
  fi
  case "$name" in
    review-facts) bundle_review_facts ;;
    task)         bundle_task ;;
    *)            bundle_default "$name" ;;
  esac
done

echo
echo "Built $(ls "$OUT_DIR"/*.zip | wc -l | tr -d ' ') zips in $OUT_DIR:"
ls -lh "$OUT_DIR"/*.zip | awk '{ printf "  %-40s %s\n", $NF, $5 }'
