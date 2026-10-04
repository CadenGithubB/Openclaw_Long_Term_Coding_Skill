# Checkpoint bookkeeping

Keep this JSON block in the task's note alongside concise artifact/command evidence.
Preserve the goal, scope and complete acceptance list across updates. Failed or skipped
checks must not disappear. Only the parent updates the checkpoint.

```json
{
  "version": 1,
  "goal": "The user's requested outcome",
  "scope": "Explicitly authorized project and operations",
  "criteria": [
    {"id": "tests", "check": "Run the project tests", "status": "pending", "evidence": ""}
  ],
  "attempts": [],
  "next": "Inspect the authorized project and delegate implementation",
  "cancelled": false
}
```

Add each accepted worker as
`{ "run_id": "actual run ID", "child_session_key": "returned child session key", "status": "running" }`.
Preserve both identifiers for native status inspection and cancellation.
After review change its status to `reviewed`, `failed` or `interrupted`, and add
`progress` (JSON boolean) and nonempty `evidence`. A worker saying done is not progress
evidence. Set a criterion to `passed` or `failed` only after checking; record the actual
command/result or direct artifact inspection in its evidence.

When deciding whether to spawn again or finish, pass the exact JSON to
`python3 {baseDir}/scripts/checkpoint.py` via stdin, using the actual skill base directory
provided by OpenClaw. Use a quoted heredoc delimiter so JSON text is data, not shell code;
choose a delimiter that is absent from the data. Do not interpolate note text into an
unquoted shell command. The helper never reads note files or runs verification commands.

`wait`: yield for the existing child; `continue`: next scoped attempt;
`stop`: do not dispatch more work; report unfinished work and limit/cancellation.
The helper does not cancel workers or confirm they stopped. Inspect the recorded
child and confirm it is inactive before any replacement writer or resumed attempt.
`complete`: only finish if the
recorded evidence really proves all criteria; `invalid` (exit 2): repair the bookkeeping,
not the claimed outcome. The helper is a consistency check, not an independent verifier
or an enforcement boundary against a model bypassing the instructions.

## Coding acceptance

For coding work, also use the retained contract and level-specific evidence checks in
[coding verification](coding-verification.md). A bookkeeping `complete` decision
cannot replace an incomplete acceptance result or a missing runtime/visual test.

## Optional decision-history reference

When the trusted parent supplies [decision history](decision-history.md), keep its
project/run identity, availability, last durable sequence, pinned pointer/hashes and
short current-choice summary alongside the existing note bookkeeping. Preserve
`savedAs` and read-back rules. Do not add history fields to the exact JSON passed
to `checkpoint.py` or `task_store.py`; audit entries consume no execution attempts.
The authoritative history stays in the private parent-owned report sidecar, outside
worker write access. A note or recorded decision grants no authority to act.

## Operator workflow state

The optional packaged `scripts/task_runner.py` is a different, executable
workflow. Its three self-contained modules run only in the reviewed host
executor. The operator supplies a bounded immutable plan and exact pre-created
Linux workers. Commands in that operator plan execute only inside those workers;
the runner never evaluates note text or source as host code, creates workers,
or starts another agent CLI. The normal native subagent workflow is unchanged.

The invocation shape is:

```text
python3 -B -I {baseDir}/scripts/task_runner.py init --job <private-job-directory> --plan <operator-plan.json> --sha256 <plan-sha256>
python3 -B -I {baseDir}/scripts/task_runner.py run --job <private-job-directory> --sha256 <same-plan-sha256>
python3 -B -I {baseDir}/scripts/task_runner.py resume --job <private-job-directory> --sha256 <same-plan-sha256>
python3 -B -I {baseDir}/scripts/task_runner.py status --job <private-job-directory> --sha256 <same-plan-sha256>
python3 -B -I {baseDir}/scripts/task_runner.py cancel --job <private-job-directory> --sha256 <same-plan-sha256>
```

These placeholders must come from the authorized operator's prepared job, not
from a note's proposed commands. `init` creates the absent job directory under
an existing owner-private directory with mode `0700`; the operator plan must be
an owner-private regular file with mode `0600`. Startup checks the saved plan digest. Work and
verification budgets persist across invocations; available cleanup attempts do
not add work authority. Each successful step captures its exact artifact and
requires confirmed worker stop before checkpointing. Completion requires the
separate verifier's expected output and an unchanged artifact. A durable pending
execution is uncertain after interruption and is not automatically replayed.

Keep the operator's job directory, job identity and plan digest, plus the returned
status, checkpoint revision, artifact digest and invocation counters in the
parent note. The full verifier receipt remains in the durable runtime journal.
`checkpoint.py`
continues to validate note bookkeeping only; a `complete` decision from that
helper cannot override the executable runner's status or replace verification.
Runtime checkpoints stay in the operator-owned private job directory, outside
worker filesystems. Cancellation is durable and takes precedence over new work.
Acceptance-only crash flags are for disposable tests, not normal skill use.
