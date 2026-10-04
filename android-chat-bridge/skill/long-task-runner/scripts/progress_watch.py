#!/usr/bin/env python3
"""Evaluate controller-fed, cumulative receipts within one coding attempt.

Data only: no commands, file writes, timers, worker cancellation or verification.
Keep the complete event history across calls in the existing parent checkpoint.
Stable semantic actionKey/checkId/phaseId values belong to the controller; command
IDs, prose, and invocation actionId values do not establish progress. Supplied
digests and 'verified' milestones are assertions, not independently proven facts.
This helper cannot enforce limits against a caller that fabricates/truncates JSON.
"""

import json
import re
import sys
from collections import Counter

MAX_BYTES = 1048576
MAX_EVENTS = 2048
SHA = re.compile(r"[0-9a-f]{64}\Z")
KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,95}\Z")
REQUIRED_EVENT = frozenset(("actionId", "phaseId", "actionKey", "checkId", "kind",
                            "state", "beforeSha256", "afterSha256",
                            "resultFingerprint", "verifiedMilestoneIds"))
OPTIONAL_EVENT = frozenset(("description", "commandId"))
IDENTITY = ("phaseId", "actionKey", "checkId", "kind", "beforeSha256")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def key(value, name):
    require(type(value) is str and KEY.fullmatch(value) is not None,
            "invalid " + name)


def digest(value, name):
    require(value is None or (type(value) is str and SHA.fullmatch(value) is not None),
            "invalid " + name)


def validate_event(event):
    require(type(event) is dict and REQUIRED_EVENT <= event.keys()
            and event.keys() <= REQUIRED_EVENT | OPTIONAL_EVENT, "invalid event fields")
    for field in ("actionId", "phaseId", "actionKey"):
        key(event[field], field)
    kind = event["kind"]
    require(kind in ("edit", "check", "inspect", "execute"), "invalid kind")
    if kind == "check":
        key(event["checkId"], "checkId")
        allowed = ("pending", "passed", "failed")
    else:
        require(event["checkId"] is None, "checkId is only for check actions")
        allowed = ("pending", "completed", "failed")
    require(event["state"] in allowed, "invalid action state")
    for field in ("beforeSha256", "afterSha256", "resultFingerprint"):
        digest(event[field], field)
    milestones = event["verifiedMilestoneIds"]
    require(type(milestones) is list and len(milestones) <= 32, "invalid milestones")
    for item in milestones:
        key(item, "milestone ID")
    require(len(set(milestones)) == len(milestones), "duplicate milestone ID")
    require(not milestones or kind == "check" and event["state"] == "passed",
            "only a passing check may record verified milestones")
    if event["state"] == "pending":
        require(event["afterSha256"] is None and event["resultFingerprint"] is None
                and not milestones, "pending action cannot contain a result")
    else:
        require(event["resultFingerprint"] is not None, "terminal result needs fingerprint")
    for field, limit in (("description", 1024), ("commandId", 128)):
        if field in event:
            require(type(event[field]) is str and len(event[field]) <= limit,
                    "invalid " + field)
    return {field: event[field] for field in REQUIRED_EVENT}


def evaluate(document):
    """Return a recommendation; raise ValueError for malformed/contradictory data."""
    require(type(document) is dict and set(document) == {"format", "limits", "events"},
            "invalid input fields")
    require(type(document["format"]) is int and document["format"] == 1, "invalid format")
    limits = document["limits"]
    require(type(limits) is dict and set(limits) == {"maxActions", "repetitionLimit"},
            "invalid limit fields")
    for field, low, high in (("maxActions", 1, 256), ("repetitionLimit", 2, 16)):
        require(type(limits[field]) is int and low <= limits[field] <= high,
                "invalid " + field)
    events = document["events"]
    require(type(events) is list and len(events) <= MAX_EVENTS, "invalid event history")
    actions = {}
    terminal_order = []
    snapshots = 0
    for raw in events:
        event = validate_event(raw)
        action_id = event["actionId"]
        previous = actions.get(action_id)
        if previous is not None:
            require(all(previous[field] == event[field] for field in IDENTITY),
                    "action identity changed")
            if previous["state"] != "pending":
                require(previous == event, "terminal action changed")
                snapshots += 1
                continue
            if event["state"] == "pending":
                snapshots += 1
                continue
        actions[action_id] = event
        if event["state"] != "pending":
            terminal_order.append(event)

    failures, unchanged, passed = Counter(), Counter(), Counter()
    milestones = set()
    artifact_changes = 0
    reasons = []
    seen_reasons = set()

    def reason(code, event=None, count=None):
        item = {"code": code}
        if event is not None:
            item.update(phaseId=event["phaseId"],
                        key=event["checkId"] if event["kind"] == "check" else event["actionKey"])
        if count is not None:
            item["count"] = count
        signature = (code, item.get("phaseId"), item.get("key"))
        if signature not in seen_reasons:
            seen_reasons.add(signature)
            reasons.append(item)

    bound = limits["repetitionLimit"]
    for event in terminal_order:
        # Progress is a previously unseen milestone reported by a passing check.
        # Byte changes are recorded separately and never reset either budget.
        milestones.update(event["verifiedMilestoneIds"])
        if event["beforeSha256"] != event["afterSha256"]:
            artifact_changes += 1
        phase = event["phaseId"]
        semantic_key = event["checkId"] if event["kind"] == "check" else event["actionKey"]
        if event["state"] == "failed":
            identity = (phase, event["kind"], semantic_key, event["resultFingerprint"])
            failures[identity] += 1
            if failures[identity] >= bound:
                reason("repeated-failure", event, failures[identity])
        if event["kind"] == "edit" and event["state"] == "completed" \
                and event["beforeSha256"] == event["afterSha256"]:
            identity = (phase, event["actionKey"])
            unchanged[identity] += 1
            if unchanged[identity] >= bound:
                reason("unchanged-rewrite", event, unchanged[identity])
        if event["kind"] == "check" and event["state"] == "passed":
            # A different command ID/prose/output fingerprint cannot disguise
            # another pass of the same semantic check on the same artifact.
            identity = (phase, event["checkId"], event["afterSha256"])
            passed[identity] += 1
            if passed[identity] >= bound:
                reason("repeated-passed-check", event, passed[identity])

    if len(actions) >= limits["maxActions"]:
        reason("action-budget-reached", count=len(actions))
    pending = sum(event["state"] == "pending" for event in actions.values())
    if pending:
        reason("pending-action", count=pending)
        decision = "wait"
    elif any(item["code"] == "repeated-failure" for item in reasons):
        decision = "diagnose"
    elif reasons:
        decision = "review"
    else:
        decision = "continue"
        reason("no-stagnation-detected")
    return {"format": 1, "decision": decision, "reasons": reasons,
            "allowNewAction": decision == "continue",
            "counts": {"events": len(events), "actions": len(actions),
                       "completedActions": len(terminal_order), "pendingActions": pending,
                       "duplicateSnapshots": snapshots, "artifactChanges": artifact_changes,
                       "verifiedMilestones": len(milestones)},
            "verifiedMilestoneIds": sorted(milestones),
            "authority": "controller-fed-receipts-only; not independent verification or enforcement"}


def unique_object(pairs):
    result = {}
    for name, value in pairs:
        require(name not in result, "duplicate JSON key")
        result[name] = value
    return result


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_BYTES + 1)
        require(len(raw) <= MAX_BYTES, "input exceeds 1 MiB")
        result = evaluate(json.loads(raw, object_pairs_hook=unique_object))
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        result = {"format": 1, "decision": "invalid", "allowNewAction": False,
                  "reasons": [{"code": "invalid-input", "detail": str(exc)}]}
        print(json.dumps(result, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
