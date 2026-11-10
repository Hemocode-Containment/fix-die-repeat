#!/bin/sh

set -eu

script_dir="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
skill_root="$(CDPATH='' cd -- "$script_dir/.." && pwd)"
skill_file="$skill_root/SKILL.md"

# Print the given message to stderr and exit with status 1; used for every
# contract violation so the script reports a single failure and stops.
fail() {
  printf 'not ok - %s\n' "$1" >&2
  exit 1
}

[ -f "$skill_file" ] || fail "SKILL.md is missing"

frontmatter_fields="$(
  awk '
    NR == 1 && $0 == "---" { in_frontmatter = 1; next }
    in_frontmatter && $0 == "---" { exit }
    in_frontmatter && $0 ~ /^[A-Za-z0-9_-]+:/ {
      sub(/:.*/, "", $0)
      print
    }
  ' "$skill_file" | sort
)"

[ "$frontmatter_fields" = "$(printf 'description\nname')" ] ||
  fail "SKILL.md frontmatter must contain only name and description"

references="$(
  grep -oE 'references/[a-z0-9-]+\.md' "$skill_file" | sort -u
)"
[ -n "$references" ] || fail "SKILL.md cites no references"

for reference in $references; do
  [ -f "$skill_root/$reference" ] || fail "missing cited reference: $reference"
done

reference_count="$(printf '%s\n' "$references" | wc -l | tr -d ' ')"
[ "$reference_count" -eq 4 ] || fail "SKILL.md must cite exactly four direct references"

project_reference="$skill_root/references/projects.md"
project_snapshot="$skill_root/scripts/project_snapshot.py"
[ -f "$project_snapshot" ] || fail "scripts/project_snapshot.py is missing"
for project_contract in \
  'exactly one open linked Project' \
  'Never hard-code an account' \
  'Exit status `0`' \
  'Exit status `0` means a complete valid snapshot' \
  'One reserved `Human` slot' \
  'One shared `Human` slot' \
  'at most one monitored initiative' \
  'Milestone coverage follows open native delivery artifacts' \
  'at most 14 days' \
  'Do not create portfolio shadow milestones' \
  'Broad plan approval does not approve these writes' \
  'changed.' \
  'Preserve successful writes' \
  'Never retry blindly'
do
  grep -Fq "$project_contract" "$project_reference" ||
    fail "projects.md is missing contract: $project_contract"
done

python3 -m py_compile "$project_snapshot" ||
  fail "project_snapshot.py does not compile"

grep -Fq 'recommend at most one milestone' "$skill_root/references/lifecycle.md" ||
  fail "lifecycle.md must bound missing-milestone recommendations"
grep -Fq 'Do not invent a due date' "$skill_root/references/lifecycle.md" ||
  fail "lifecycle.md must require evidence for milestone due dates"
grep -Fq 'Assess every open milestone with a due date' \
  "$skill_root/references/lifecycle.md" ||
  fail "lifecycle.md must assess every open dated milestone"
grep -Fq 'nearest due date' "$skill_root/references/lifecycle.md" ||
  fail "lifecycle.md must order dated milestone assessments"
for schedule_status in 'on track' 'at risk' 'insufficient evidence'; do
  grep -Fq "$schedule_status" "$skill_root/references/lifecycle.md" ||
    fail "lifecycle.md must define the $schedule_status milestone status"
done
grep -Fq 'Do not infer schedule confidence from issue counts' \
  "$skill_root/references/lifecycle.md" ||
  fail "lifecycle.md must reject count-only milestone forecasts"
grep -Fq 'undated open milestones unclassified' \
  "$skill_root/references/lifecycle.md" ||
  fail "lifecycle.md must leave undated milestones unclassified"
grep -Fq 'when no open items remain' "$skill_root/references/lifecycle.md" ||
  fail "lifecycle.md must classify completed milestone scope"
grep -Fq 'when open work remains after the due date' \
  "$skill_root/references/lifecycle.md" ||
  fail "lifecycle.md must classify overdue milestone scope"
grep -Fq 'when the snapshot supports neither conclusion' \
  "$skill_root/references/lifecycle.md" ||
  fail "lifecycle.md must reject unsupported milestone confidence"
grep -Fq "Keep an empty milestone's closure or reuse flag" \
  "$skill_root/references/lifecycle.md" ||
  fail "lifecycle.md must preserve empty milestone handling"
grep -Fq 'native blockers and linked pull requests' \
  "$skill_root/references/github-routing.md" ||
  fail "github-routing.md must gather milestone schedule evidence"
grep -Fq 'create one milestone' "$skill_root/references/github-routing.md" ||
  fail "github-routing.md must gate milestone creation"
grep -Fq 'assign one issue or pull request to one milestone' \
  "$skill_root/references/github-routing.md" ||
  fail "github-routing.md must gate each milestone assignment"
for endpoint in 'local changes' 'committed branch' 'pushed branch' 'draft pull request'; do
  grep -Fq "$endpoint" "$skill_root/references/lifecycle.md" ||
    fail "lifecycle.md must make the $endpoint endpoint explicit"
done
if ! grep -Fq 'validated commit pushed' "$skill_root/references/lifecycle.md" ||
  ! grep -Fq 'repository-appropriate delivery branch' \
    "$skill_root/references/lifecycle.md"; then
  fail "lifecycle.md must default start work to a pushed delivery branch"
fi
grep -Fq 'task-owned changes remain unstaged or uncommitted' \
  "$skill_root/references/lifecycle.md" ||
  fail "lifecycle.md must reject dirty or unpushed completion"
grep -Fq 'Opening a pull request is a separate delivery endpoint' \
  "$skill_root/references/lifecycle.md" ||
  fail "lifecycle.md must keep pull request creation explicit"

# These are literal compatibility markers, not shell expressions.
# shellcheck disable=SC2016
for forbidden in \
  '$ARGUMENTS' \
  'AskUserQuestion' \
  'EnterWorktree' \
  '.claude/' \
  '/roadmap' \
  'argument-hint:' \
  'allowed-tools:'
do
  matches="$(find "$skill_root" -type f -name '*.md' \
    -exec grep -Fl "$forbidden" {} + || true)"
  if [ -n "$matches" ]; then
    fail "Claude-only contract leaked into markdown: $forbidden ($matches)"
  fi
done

if find "$skill_root" -type f \( \
  -name 'roadmap.md' -o \
  -name 'INDEX.md' -o \
  -path '*/archive/*' \
\) | grep -q .; then
  fail "runtime roadmap state must not live inside the skill"
fi

printf 'ok - roadmap skill contracts validated\n'
