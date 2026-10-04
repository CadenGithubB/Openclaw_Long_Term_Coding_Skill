#!/usr/bin/env python3
"""Validate checkpoint bookkeeping from stdin. No files, network or execution."""
import json
import sys

MAX_BYTES = 65536


def nonempty(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + " must be nonempty text")


def decide(state):
    if not isinstance(state, dict) or type(state.get("version")) is not int or state["version"] != 1:
        raise ValueError("expected checkpoint object version 1")
    for key in ("goal", "scope"):
        nonempty(state.get(key), key)
    criteria = state.get("criteria")
    attempts = state.get("attempts")
    if not isinstance(criteria, list) or not criteria or len(criteria) > 100:
        raise ValueError("criteria must contain 1..100 checks")
    if not isinstance(attempts, list) or len(attempts) > 8:
        raise ValueError("attempts must contain at most 8 runs")
    seen = set()
    for item in criteria:
        if not isinstance(item, dict):
            raise ValueError("invalid criterion")
        for key in ("id", "check"):
            nonempty(item.get(key), "criterion " + key)
        if item["id"] in seen:
            raise ValueError("duplicate criterion id")
        seen.add(item["id"])
        if item.get("status") not in ("pending", "failed", "passed"):
            raise ValueError("invalid criterion status")
        if item["status"] in ("failed", "passed"):
            nonempty(item.get("evidence"), "review evidence")
    seen = set()
    running = 0
    for index, run in enumerate(attempts):
        if not isinstance(run, dict):
            raise ValueError("invalid attempt")
        nonempty(run.get("run_id"), "run_id")
        if run["run_id"] in seen:
            raise ValueError("duplicate run id")
        seen.add(run["run_id"])
        if run.get("status") == "running":
            running += 1
            if index != len(attempts) - 1:
                raise ValueError("a running attempt must be last")
        elif run.get("status") in ("reviewed", "failed", "interrupted"):
            if type(run.get("progress")) is not bool:
                raise ValueError("reviewed progress must be boolean")
            nonempty(run.get("evidence"), "attempt evidence")
        else:
            raise ValueError("invalid attempt status")
    if running > 1:
        raise ValueError("only one writer may run")
    if state.get("cancelled", False) is not False:
        if state.get("cancelled") is not True:
            raise ValueError("cancelled must be boolean")
        return {"decision": "stop", "reason": "user cancelled; do not restart"}
    if running:
        return {"decision": "wait", "reason": "accepted worker still running; yield"}
    if all(item["status"] == "passed" for item in criteria):
        return {"decision": "complete", "reason": "all checks recorded as passed; verify evidence is real"}
    if len(attempts) == 8:
        return {"decision": "stop", "reason": "eight-run limit; work remains"}
    if len(attempts) >= 2 and all(run.get("progress") is False for run in attempts[-2:]):
        return {"decision": "stop", "reason": "two consecutive attempts without progress"}
    nonempty(state.get("next"), "next action")
    return {"decision": "continue", "reason": "unmet checks and retry budget available"}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("checkpoint exceeds 64 KiB")
        result = decide(json.loads(raw, object_pairs_hook=unique_object))
        print(json.dumps(result))
        return 0
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        print(json.dumps({"decision": "invalid", "error": str(exc)}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
