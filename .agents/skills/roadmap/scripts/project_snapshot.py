#!/usr/bin/env python3
"""Capture and validate the canonical GitHub Project for an initiative registry."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import date
from typing import Any


EXPECTED_FIELDS = {
    "Status": ("ProjectV2SingleSelectField", ["Candidate", "Planned", "Active", "Paused", "Completed", "Dropped"]),
    "Priority": ("ProjectV2SingleSelectField", ["Now", "Next", "Later", "Unscheduled"]),
    "Lane": ("ProjectV2SingleSelectField", ["Cost, Monetization, Reputation", "Automation and Leverage", "Releases and Community", "Foundations and Access"]),
    "Attention": ("ProjectV2SingleSelectField", ["Human", "Monitored automation"]),
    "Exit gate": ("ProjectV2Field", "TEXT"),
    "Deadline": ("ProjectV2Field", "DATE"),
    "Review by": ("ProjectV2Field", "DATE"),
    "Program": ("ProjectV2Field", "TEXT"),
    "Last reviewed": ("ProjectV2Field", "DATE"),
}
LANE_COST = "Cost, Monetization, Reputation"
TERMINAL = {"Completed", "Dropped"}
REQUIRED_READS = ("project", "fields", "views", "workflows", "items", "milestones")
GITHUB_DELIVERY_URL = re.compile(r"https://github\.com/([^/\s]+)/([^/\s]+)/(?:(issues|pull)/(\d+))")
GITHUB_MILESTONE_URL = re.compile(r"https://github\.com/([^/\s]+)/([^/\s]+)/milestone/(\d+)")


def finding(code: str, message: str, **details: Any) -> dict[str, Any]:
    """Build one contract violation as a `{code, message, details}` dict.

    `details` is present only when extra context is passed, so JSON consumers get a stable minimal
    shape otherwise.
    """
    result = {"code": code, "message": message}
    if details:
        result["details"] = details
    return result


def documented_critical_interrupt(item: dict[str, Any]) -> bool:
    """Check that an item body documents a complete critical interrupt.

    `evaluate_snapshot` allows a second non-cost lane to hold human attention only when all five
    interrupt markers are present, so a partial writeout must not count as sanctioned preemption.
    """
    body = item.get("body", "").lower()
    markers = (
        "critical interrupt evidence:",
        "approved by:",
        "review or expiry:",
        "displaced initiative:",
        "resumption plan:",
    )
    return all(marker in body for marker in markers)


def markdown_section(body: str, heading: str) -> str:
    """Extract a `##`/`###` section's text up to the next heading, or `""` when it is absent."""
    match = re.search(
        rf"(?ms)^#{{2,3}} {re.escape(heading)}\s*$\n(.*?)(?=^#{{2,3}} |\Z)",
        body,
    )
    return match.group(1).strip() if match else ""


def parse_milestone_links(body: str) -> list[dict[str, Any]]:
    """Parse `Delivery milestones` links into `{repository, number, url}` dicts for coverage."""
    links: list[dict[str, Any]] = []
    for owner, repo, number in GITHUB_MILESTONE_URL.findall(markdown_section(body, "Delivery milestones")):
        links.append({
            "repository": f"{owner}/{repo}",
            "number": int(number),
            "url": f"https://github.com/{owner}/{repo}/milestone/{number}",
        })
    return links


def parse_delivery_links(body: str) -> list[dict[str, Any]]:
    """Parse `Delivery work` links into `{repository, number, kind, url}` records.

    `kind` records whether the link targets an issue or a pull request.
    """
    links: list[dict[str, Any]] = []
    for owner, repo, kind, number in GITHUB_DELIVERY_URL.findall(markdown_section(body, "Delivery work")):
        links.append({
            "repository": f"{owner}/{repo}",
            "number": int(number),
            "kind": "pull" if kind == "pull" else "issue",
            "url": f"https://github.com/{owner}/{repo}/{kind}/{number}",
        })
    return links


def parse_milestone_exceptions(body: str) -> list[dict[str, str]]:
    """Parse the `Milestone exceptions` section into fully populated exception dicts.

    Malformed lines are skipped silently; only parseable, complete rows reach
    `valid_milestone_exception`, which judges their content.
    """
    exceptions: list[dict[str, str]] = []
    for line in markdown_section(body, "Milestone exceptions").splitlines():
        if not line.startswith("- "):
            continue
        parts = [part.strip() for part in line[2:].split("|")]
        if len(parts) != 7:
            continue
        values: dict[str, str] = {"repository": parts[0]}
        keys = {
            "Owner": "owner",
            "Evidence": "evidence",
            "Rationale": "rationale",
            "Created": "created",
            "Review or expiry": "reviewOrExpiry",
            "Exit condition": "exitCondition",
        }
        for part in parts[1:]:
            label, separator, value = part.partition(":")
            key = keys.get(label.strip())
            if not separator or not key:
                values = {}
                break
            values[key] = value.strip()
        if values and all(values.get(key) for key in ("repository", *keys.values())):
            exceptions.append(values)
    return exceptions


def valid_milestone_exception(exception: dict[str, str], today: date) -> tuple[bool, bool]:
    """Validate one exception and report `(valid, current)`.

    `current` (review or expiry date still ahead) is only meaningful when `valid`; validity requires
    https evidence and a review window of at most 14 days.
    """
    try:
        created = date.fromisoformat(exception["created"])
        review = date.fromisoformat(exception["reviewOrExpiry"])
    except (KeyError, ValueError):
        return False, False
    required = ("repository", "owner", "evidence", "rationale", "exitCondition")
    valid = (
        all(exception.get(key) for key in required)
        and exception["evidence"].startswith("https://")
        and created <= today
        and created <= review
        and (review - created).days <= 14
    )
    return valid, valid and review >= today


def capture_milestone_coverage(item: dict[str, Any]) -> dict[str, Any]:
    """Resolve each milestone and delivery link in an item body through the REST API.

    Any failed read flips `readComplete` off and marks the entry `UNKNOWN`, because a snapshot with
    unverifiable coverage must not pass as valid.
    """
    read_complete = True
    milestones: list[dict[str, Any]] = []
    for link in parse_milestone_links(item.get("body", "")):
        milestone = dict(link)
        try:
            payload = run_gh("api", f"repos/{link['repository']}/milestones/{link['number']}")
            milestone.update({
                "state": str(payload.get("state", "")).upper(),
                "title": payload.get("title"),
                "dueOn": payload.get("due_on"),
            })
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            read_complete = False
            milestone["state"] = "UNKNOWN"
        milestones.append(milestone)

    artifacts: list[dict[str, Any]] = []
    for link in parse_delivery_links(item.get("body", "")):
        artifact = dict(link)
        try:
            payload = run_gh("api", f"repos/{link['repository']}/issues/{link['number']}")
            native_milestone = payload.get("milestone")
            artifact.update({
                "state": str(payload.get("state", "")).upper(),
                "milestoneNumber": native_milestone.get("number") if native_milestone else None,
            })
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            read_complete = False
            artifact.update({"state": "UNKNOWN", "milestoneNumber": None})
        artifacts.append(artifact)

    return {
        "readComplete": read_complete,
        "milestones": milestones,
        "artifacts": artifacts,
        "exceptions": parse_milestone_exceptions(item.get("body", "")),
    }


def evaluate_snapshot(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Check a captured snapshot against the portfolio contract.

    Returns one finding per violation, spanning read completeness, field/view/workflow
    configuration, per-item delivery coverage, terminal-state agreement, attention limits, and
    review staleness.
    """
    findings: list[dict[str, Any]] = []
    completeness = snapshot.get("readCompleteness", {})
    missing_reads = [name for name in REQUIRED_READS if not completeness.get(name)]
    if missing_reads:
        findings.append(finding("incomplete_read", "Required Project reads are incomplete.", reads=missing_reads))

    fields = {field["name"]: field for field in snapshot.get("fields", [])}
    for name, (expected_type, expected_value) in EXPECTED_FIELDS.items():
        actual = fields.get(name)
        if not actual:
            findings.append(finding("missing_field", f"Required Project field `{name}` is missing.", field=name))
            continue
        if actual.get("type") != expected_type:
            findings.append(finding("field_type", f"Project field `{name}` has the wrong type.", field=name, expected=expected_type, actual=actual.get("type")))
            continue
        if isinstance(expected_value, list):
            if actual.get("options", []) != expected_value:
                findings.append(finding("field_options", f"Project field `{name}` has the wrong options or order.", field=name, expected=expected_value, actual=actual.get("options", [])))
        elif actual.get("dataType") != expected_value:
            findings.append(finding("field_data_type", f"Project field `{name}` has the wrong data type.", field=name, expected=expected_value, actual=actual.get("dataType")))

    expected_views = {
        "Inventory": ("TABLE_LAYOUT", None, ["Lane"]),
        "Current": ("BOARD_LAYOUT", "status:Active,Paused", ["Lane"]),
        "Pipeline": ("TABLE_LAYOUT", "status:Candidate,Planned", ["Lane"]),
        "History": ("TABLE_LAYOUT", "status:Completed,Dropped", ["Lane"]),
    }
    views = {view["name"]: view for view in snapshot.get("views", [])}
    for name, (layout, view_filter, group_by) in expected_views.items():
        actual = views.get(name)
        if not actual:
            findings.append(finding("missing_view", f"Required Project view `{name}` is missing.", view=name))
            continue
        if (actual.get("layout"), actual.get("filter"), actual.get("groupBy", [])) != (layout, view_filter, group_by):
            findings.append(finding("view_configuration", f"Project view `{name}` does not match the portfolio contract.", view=name))
    pipeline = views.get("Pipeline", {})
    if pipeline and pipeline.get("sortBy", [])[:2] != ["Priority", "Deadline"]:
        findings.append(finding("pipeline_sort", "The `Pipeline` view must sort by `Priority` and then `Deadline`."))

    workflows = {workflow["name"]: workflow for workflow in snapshot.get("workflows", [])}
    if workflows.get("Auto-add sub-issues to project", {}).get("enabled"):
        findings.append(finding("subissue_automation", "Automatic sub-issue addition must remain disabled."))
    if not workflows.get("Item added to project", {}).get("enabled"):
        findings.append(finding("candidate_automation", "Item-added automation must set new initiatives to `Candidate`."))
    for name in ("Auto-close issue", "Item closed", "Pull request linked to issue", "Pull request merged"):
        if workflows.get(name, {}).get("enabled"):
            findings.append(finding("terminal_automation", f"Terminal automation `{name}` must remain disabled.", workflow=name))

    items = snapshot.get("items", [])
    human_items: list[dict[str, Any]] = []
    monitored_items: list[dict[str, Any]] = []
    today = date.today()
    for item in items:
        values = item.get("fields", {})
        status = values.get("Status")
        priority = values.get("Priority")
        attention = values.get("Attention")
        state = item.get("state")
        title = item.get("title", item.get("id", "unknown item"))
        coverage = item.get("milestoneCoverage", {})
        exceptions = coverage.get("exceptions", [])
        current_exceptions: dict[str, dict[str, str]] = {}
        for exception in exceptions:
            valid, current = valid_milestone_exception(exception, today)
            if not valid:
                findings.append(finding("invalid_milestone_exception", f"Initiative `{title}` has an invalid milestone exception.", item=item.get("id"), repository=exception.get("repository")))
            elif not current:
                findings.append(finding("expired_milestone_exception", f"Initiative `{title}` has an expired milestone exception.", item=item.get("id"), repository=exception.get("repository")))
            else:
                current_exceptions[exception["repository"]] = exception

        if status in {"Planned", "Active"}:
            open_artifacts = [artifact for artifact in coverage.get("artifacts", []) if artifact.get("state") == "OPEN"]
            milestones = {
                (milestone.get("repository"), milestone.get("number")): milestone
                for milestone in coverage.get("milestones", [])
            }
            for artifact in open_artifacts:
                repository = artifact.get("repository")
                milestone_number = artifact.get("milestoneNumber")
                if milestone_number is None:
                    if repository not in current_exceptions:
                        findings.append(finding("uncovered_delivery_work", f"Open delivery work for `{title}` has no native milestone or current exception.", item=item.get("id"), artifact=artifact.get("url")))
                    continue
                milestone = milestones.get((repository, milestone_number))
                if not milestone:
                    findings.append(finding("missing_milestone_link", f"Initiative `{title}` does not link its native delivery milestone.", item=item.get("id"), artifact=artifact.get("url"), repository=repository, milestone=milestone_number))
                elif milestone.get("state") != "OPEN":
                    findings.append(finding("closed_milestone_coverage", f"Open delivery work for `{title}` uses a milestone that is not open.", item=item.get("id"), artifact=artifact.get("url"), milestone=milestone.get("url")))
            if status == "Active" and not open_artifacts and not current_exceptions:
                findings.append(finding("active_without_delivery", f"Active initiative `{title}` has no open delivery work or current milestone exception.", item=item.get("id")))
        if (state in {"CLOSED", "MERGED"}) != (status in TERMINAL):
            findings.append(finding("terminal_mismatch", f"`{title}` has inconsistent issue and Project terminal state.", item=item.get("id")))
        if status == "Active" and (priority != "Now" or not values.get("Lane") or not values.get("Exit gate") or attention not in {"Human", "Monitored automation"}):
            findings.append(finding("active_contract", f"Active initiative `{title}` is missing a required active-state value.", item=item.get("id")))
        if status != "Active" and attention:
            findings.append(finding("inactive_attention", f"Non-active initiative `{title}` still owns attention.", item=item.get("id")))
        if attention == "Human":
            human_items.append(item)
        elif attention == "Monitored automation":
            monitored_items.append(item)

        reviewed = values.get("Last reviewed")
        try:
            if reviewed and (today - date.fromisoformat(reviewed)).days > 14:
                findings.append(finding("stale_review", f"Initiative `{title}` has not been reviewed in 14 days.", item=item.get("id"), lastReviewed=reviewed))
        except ValueError:
            findings.append(finding("invalid_date", f"Initiative `{title}` has an invalid `Last reviewed` date.", item=item.get("id")))

    if len(human_items) > 2:
        findings.append(finding("human_attention_limit", "More than two initiatives own human attention."))
    lane_counts = Counter(item.get("fields", {}).get("Lane") for item in human_items)
    for lane, count in lane_counts.items():
        if lane and count > 1:
            findings.append(finding("human_lane_limit", f"Lane `{lane}` owns more than one human-attention slot.", lane=lane))
    shared = [item for item in human_items if item.get("fields", {}).get("Lane") != LANE_COST]
    if len(shared) > 1:
        critical_replacement = len(human_items) <= 2 and len({item.get("fields", {}).get("Lane") for item in shared}) == len(shared) and any(documented_critical_interrupt(item) for item in shared)
        if not critical_replacement:
            findings.append(finding("shared_human_limit", "More than one non-cost initiative owns human attention without a documented critical preemption."))

    if len(monitored_items) > 1:
        findings.append(finding("monitored_limit", "More than one initiative uses monitored automation."))
    safeguard_markers = ("supervised pass:", "health evidence:", "resource bound:", "stop condition:")
    for item in monitored_items:
        values = item.get("fields", {})
        body = item.get("body", "").lower()
        if not item.get("assignees") or not values.get("Review by") or not values.get("Exit gate") or not all(marker in body for marker in safeguard_markers):
            findings.append(finding("monitored_safeguards", f"Monitored initiative `{item.get('title', item.get('id'))}` lacks required supervision evidence.", item=item.get("id")))

    return findings


def run_gh(*args: str) -> Any:
    """Run `gh` with the given arguments and return its parsed JSON stdout.

    A non-zero exit raises `subprocess.CalledProcessError`, which callers treat as a failed read.
    """
    completed = subprocess.run(["gh", *args], check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def graphql(query: str, **variables: str) -> dict[str, Any]:
    """Run one GraphQL query through `gh api graphql` and return the `data` payload.

    GraphQL-level errors raise `RuntimeError` instead of returning partial data, so a caller never
    mistakes a failed read for a complete one.
    """
    args = ["api", "graphql", "-f", f"query={query}"]
    for name, value in variables.items():
        args.extend(("-f", f"{name}={value}"))
    payload = run_gh(*args)
    if payload.get("errors"):
        raise RuntimeError(payload["errors"][0].get("message", "GraphQL query failed"))
    return payload["data"]


def resolve_project(repo: str, project_number: int | None) -> dict[str, Any]:
    """Find the single open linked Project for a repository, optionally narrowed by number.

    Zero or several candidates raise `RuntimeError`: a snapshot must capture an unambiguous
    canonical Project, never a guess.
    """
    owner, name = repo.split("/", 1)
    query = """
    query($owner:String!,$name:String!){
      repository(owner:$owner,name:$name){projectsV2(first:100){totalCount nodes{
        id number title closed url owner{... on User{login} ... on Organization{login}}
      }}}
    }
    """
    data = graphql(query, owner=owner, name=name)
    repository = data.get("repository")
    if not repository:
        raise RuntimeError(f"Repository `{repo}` was not found or cannot be read")
    connection = repository["projectsV2"]
    if connection["totalCount"] > len(connection["nodes"]):
        raise RuntimeError("Linked Project discovery was incomplete")
    candidates = [project for project in connection["nodes"] if not project["closed"]]
    if project_number is not None:
        candidates = [project for project in candidates if project["number"] == project_number]
    if len(candidates) != 1:
        qualifier = f"number {project_number}" if project_number is not None else "canonical open"
        raise RuntimeError(f"Expected exactly one linked {qualifier} Project, found {len(candidates)}")
    return candidates[0]


def project_metadata(project_id: str) -> dict[str, Any]:
    """Fetch a Project's fields, views, and workflows in one node query.

    Each collection carries `totalCount` so callers can detect truncated first pages.
    """
    query = """
    query($id:ID!){node(id:$id){... on ProjectV2{
      fields(first:100){totalCount nodes{
        __typename
        ... on ProjectV2FieldCommon{id name}
        ... on ProjectV2Field{dataType}
        ... on ProjectV2SingleSelectField{options{id name}}
      }}
      views(first:100){totalCount nodes{id name layout filter
        groupByFields(first:20){nodes{... on ProjectV2FieldCommon{name}}}
        verticalGroupByFields(first:20){nodes{... on ProjectV2FieldCommon{name}}}
        sortByFields(first:20){nodes{field{... on ProjectV2FieldCommon{name}} direction}}
      }}
      workflows(first:100){totalCount nodes{id name enabled}}
    }}}
    """
    return graphql(query, id=project_id)["node"]


def normalize_view(raw: dict[str, Any]) -> dict[str, Any]:
    """Flatten a raw view node to `{id, name, layout, filter, groupBy, sortBy}`.

    Board views prefer their vertical grouping when one is set; other layouts keep horizontal
    grouping.
    """
    horizontal = raw.get("groupByFields", {}).get("nodes", [])
    vertical = raw.get("verticalGroupByFields", {}).get("nodes", [])
    grouping = vertical if raw.get("layout") == "BOARD_LAYOUT" and vertical else horizontal
    return {
        "id": raw["id"],
        "name": raw["name"],
        "layout": raw["layout"],
        "filter": raw.get("filter") or None,
        "groupBy": [node["name"] for node in grouping],
        "sortBy": [node["field"]["name"] for node in raw["sortByFields"]["nodes"]],
    }


def project_items(project_id: str) -> list[dict[str, Any]]:
    """Page through every Project item and flatten field values to a `{field name: value}` map.

    Items without linked content (drafts) keep a `Draft item` placeholder title.
    """
    query = """
    query($id:ID!,$after:String){node(id:$id){... on ProjectV2{items(first:100,after:$after){
      pageInfo{hasNextPage endCursor}
      nodes{id content{
        ... on Issue{title body state url number repository{nameWithOwner} assignees(first:20){nodes{login}}}
        ... on PullRequest{title body state url number repository{nameWithOwner} assignees(first:20){nodes{login}}}
      } fieldValues(first:100){nodes{
        ... on ProjectV2ItemFieldTextValue{text field{... on ProjectV2FieldCommon{name}}}
        ... on ProjectV2ItemFieldDateValue{date field{... on ProjectV2FieldCommon{name}}}
        ... on ProjectV2ItemFieldSingleSelectValue{name field{... on ProjectV2FieldCommon{name}}}
      }}}
    }}}}
    """
    items: list[dict[str, Any]] = []
    after = ""
    while True:
        variables: dict[str, Any] = {"id": project_id}
        if after:
            variables["after"] = after
        connection = graphql(query, **variables)["node"]["items"]
        for raw in connection["nodes"]:
            content = raw.get("content") or {}
            values: dict[str, Any] = {}
            for value in raw.get("fieldValues", {}).get("nodes", []):
                field = value.get("field") or {}
                field_name = field.get("name")
                if field_name:
                    values[field_name] = value.get("name", value.get("text", value.get("date")))
            items.append({
                "id": raw["id"],
                "title": content.get("title", "Draft item"),
                "body": content.get("body", ""),
                "state": content.get("state"),
                "url": content.get("url"),
                "repository": (content.get("repository") or {}).get("nameWithOwner"),
                "assignees": [node["login"] for node in (content.get("assignees") or {}).get("nodes", [])],
                "fields": values,
            })
        page = connection["pageInfo"]
        if not page["hasNextPage"]:
            return items
        after = page["endCursor"]


def capture(repo: str, project_number: int | None) -> dict[str, Any]:
    """Assemble the full snapshot for one Project.

    `readCompleteness` marks every required read (project, fields, views, workflows, items,
    milestones) as fully returned, so consumers can trust the snapshot before acting on `findings`.
    """
    project = resolve_project(repo, project_number)
    metadata = project_metadata(project["id"])
    fields = []
    for raw in metadata["fields"]["nodes"]:
        fields.append({
            "id": raw["id"],
            "name": raw["name"],
            "type": raw["__typename"],
            **({"dataType": raw["dataType"]} if raw.get("dataType") else {}),
            **({"options": [option["name"] for option in raw["options"]]} if "options" in raw else {}),
        })
    views = [normalize_view(raw) for raw in metadata["views"]["nodes"]]
    workflows = [{"id": raw["id"], "name": raw["name"], "enabled": raw["enabled"]} for raw in metadata["workflows"]["nodes"]]
    complete = all(metadata[key]["totalCount"] == len(metadata[key]["nodes"]) for key in ("fields", "views", "workflows"))
    items = project_items(project["id"])
    for item in items:
        item["milestoneCoverage"] = capture_milestone_coverage(item)
    snapshot = {
        "version": 1,
        "repository": repo,
        "project": project,
        "readCompleteness": {
            "project": True,
            "fields": complete,
            "views": complete,
            "workflows": complete,
            "items": True,
            "milestones": all(item["milestoneCoverage"]["readComplete"] for item in items),
        },
        "fields": fields,
        "views": views,
        "workflows": workflows,
        "items": items,
    }
    snapshot["findings"] = evaluate_snapshot(snapshot)
    return snapshot


def main() -> int:
    """Capture, evaluate, and print the snapshot as JSON.

    Exit codes: `0` for a complete, clean snapshot; `1` for failed or incomplete reads; `2` for a
    complete snapshot with findings.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, metavar="OWNER/REPO")
    parser.add_argument("--project-number", type=int)
    parser.add_argument("--format", choices=("json",), default="json")
    args = parser.parse_args()
    if args.repo.count("/") != 1:
        parser.error("--repo must use OWNER/REPO")
    try:
        snapshot = capture(args.repo, args.project_number)
    except (
        subprocess.CalledProcessError,
        OSError,
        json.JSONDecodeError,
        KeyError,
        RuntimeError,
    ) as error:
        snapshot = {
            "version": 1,
            "repository": args.repo,
            "readCompleteness": {name: False for name in REQUIRED_READS},
            "findings": [finding("snapshot_error", str(error))],
        }
        print(json.dumps(snapshot, indent=2, sort_keys=True))
        return 1
    print(json.dumps(snapshot, indent=2, sort_keys=True))
    if any(not snapshot["readCompleteness"].get(name) for name in REQUIRED_READS):
        return 1
    return 2 if snapshot["findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
