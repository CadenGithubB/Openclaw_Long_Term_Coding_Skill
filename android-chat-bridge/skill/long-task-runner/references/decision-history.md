# Decision history for later review

Record material choices, their stated reasons, changes of direction and resulting
observations when the user requests an audit history. Brief reasons normally fit
one to three sentences. Ask for externally stated explanations in the existing
worker return/checkpoint, not raw internal deliberation or an extra inference call.
Use `not_recorded` when no reason was delivered, and list only alternatives actually
documented. Routine commands and formatting do not need a decision entry.

This is an inert documentation interface for the existing trusted parent. It has
no learning loop, scoring, policy edits, skill rewriting, improvement queue,
background reviewer or corrective action. Reading/rendering records starts no work.
Existing authorized app debugging remains available through its original workflow;
the history records its choices and results without initiating it.

## Make the explanation understandable

Use one to three plain-language sentences for each material choice, change or
observation. Describe the user's problem, what was chosen and the actual stated
reason before naming code or fields. For example: “The old ‘saved’ message stayed
on screen after a later save failed. I chose to clear it on failure so the screen
would not tell the user their note had been saved.” This is an illustrative style
example, not a reason to insert into another run.

Write this wording directly into the existing narrative fields; no extra schema or
inference call is needed. Keep expected outcomes distinct from observed results.
When no rationale or alternatives were recorded, state that absence instead of
filling it in. Each readable section introduces what it means, with the exact
original entry and provenance available beneath it. See the shared
[human-report guidance](android-run-reporting.md#write-the-human-explanation-first).

## Available interface and ownership

`scripts/decision_history.py` is a parent-side Python library, not a model tool or
CLI. The existing acceptance report command consumes its optional descriptor.
The parent can call `decision_drafts.capture` on exact retained worker draft bytes.
This is an explicit integration API, not a background listener or main-chat hook.
Report unavailable/partial capture when the active parent has not collected the
source; fixture tests do not demonstrate live integration. If native workers return only
at termination, intermediate/pre-crash decisions may never reach the parent.

The parent creates a private mode-0700 directory at the fixed
`<report-root>/decision-history`, outside worker access. It independently retains
the contract, registered versions, provenance and admitted evidence registry.
The worker may supply a bounded draft; it cannot choose a host path, origin,
receipt timestamp, registry, approval or application bindings.

Library calls:

- `initialize(root, metadata)`: metadata has `projectId`, `runId`,
  `contractSha256`, admitted `criterionIds`, `versions` and `synthetic`.
  `versions` has nullable SHA-256 fields `skillSha256`, `modelDigest`,
  `toolchainSha256`. Unknown values remain null. Registration is idempotent and
  cannot replace a different registration.
- `record(root, scope, draft, *, attempt_id, submission_id, ordinal, origin,
  capture_mode, source_ref, registry, expected_binding, review_requested=False)`:
  scope is `{projectId,runId,contractSha256}`; it must match registration.
  The parent supplies the remaining keyword arguments from actual admitted data.
- `descriptor(root, scope)`: a bounded version-1 reference and recording availability,
  last durable sequence/hash and immutable snapshot pointer. Pass it as the optional
  `decisionHistory` field to the existing Android report CLI.
- `projection(report_root, reference, run_id, contract_sha)`: report implementation
  detail; resolves the pinned revision under the fixed sidecar only. It never reads
  a worker-selected path or executes reference text.

The parent must catch validation/storage errors and retain original messages and
receipts. Publish explicit unavailable/partial coverage through the ordinary report
finalization path; never restart or stop a worker solely to repair documentation.
This library does not authenticate caller-supplied registry facts. Its consistency
checks cannot replace independent receipt admission.

## Worker submission

Write a JSON array to the project-local draft path designated by the parent. Copy
one decision's shape from `examples/decision-draft.json`, replacing example content
and criterion IDs with the current task. Do not wrap it in prose or Markdown.
The final human response may be ordinary prose; it is not a second JSON interface.

Validate the actual bytes in the existing worker attempt:

```text
python3 <skill>/scripts/decision_drafts.py --criterion <admitted-criterion-id> < <draft-file>
```

Repeat `--criterion` for other admitted criteria. A submission holds up to four
unique decision IDs, at most 2 KiB per draft and 16 KiB total. Only decisions and
amendments are accepted, with empty `evidenceRefs`; the parent owns observations.
The validator returns exit 0 or 2 and never writes history or runs another model.
An invalid format may be corrected while the same authorized app attempt is still
active; do not launch or extend an attempt solely to fill audit documentation.
If unavailable, leave capture partial and retain the raw statement.

The trusted parent retains the exact draft bytes outside worker access, then calls
`decision_drafts.capture(root, scope, raw, criterion_ids=..., attempt_id=...,
submission_id=..., capture_mode='checkpoint')`. The retained source ID and its
computed digest identify the worker statement. The parent revalidates the bytes;
a worker's claimed validation pass is not evidence of validity. Identical replays
are idempotent. Invalid bytes are not normalized or transcribed; the API records a
sticky coverage gap. Storage failures propagate to ordinary report finalization.
No caller should parse final prose to invent missing fields or stated rationale.

## Draft and provenance

Every draft has exactly `{eventType,decisionId,supersedes,payload}`. See
`examples/decision-draft.json` for a synthetic decision, not an actual observation.
Payload types:

| Event | Payload beyond `criterionIds` and `evidenceRefs` |
| --- | --- |
| `decision_recorded` | `title`, `category`, `problem`, `chosenApproach`, nullable `statedRationale`, `rationaleAvailability` (`recorded` or `not_recorded`), `documentedAlternatives`, `expectedOutcome`, `knownTradeoffs`, `openQuestions` |
| `decision_amended` | `change`, nullable `statedReason` |
| `outcome_observed` | `observation`, `status`, `application` boolean, nullable `sourceIdentitySha256`, `apkSha256`, `environmentSha256` |
| `review_annotation` | `comment`, `author` |

An amendment/outcome/review refers to an existing decision. A materially new choice
uses a new decision ID and `supersedes` naming a current prior decision. Original
events remain immutable; self-reference, reuse and supersession cycles are rejected.

Parent provenance is one of `agent_statement`, `user_instruction`,
`controller_receipt`, `reviewer_annotation`. Capture mode is `contemporaneous`,
`checkpoint` or `retrospective`; the parent records actual UTC receipt time and an
increasing sequence. Retrospective reconstruction must identify its source and never
invent or backdate rationale. Review annotations require an explicitly requested
review and remain distinct from original statements and observations.

Source and evidence references are opaque `{id,sha256}` pairs, never paths or URLs.
The parent registry maps IDs to retained message/receipt facts (`kind`, `sha256`,
`synthetic`, and applicable `status`/application hashes). Source kind must correspond
to its origin: `agent_message`, `user_message`, `controller_receipt`, `review_message`.
The human view retains IDs/hashes for the parent's existing evidence lookup; direct
message/artifact UI navigation still depends on that integration.

An agent saying a test passed is a claim. Controller observations require matching
non-synthetic receipts, status and applicable source/APK/environment hashes from
the parent's expected binding. Missing/stale/synthetic references remain visible as
unverified, without erasing the stated observation. `matched_parent_receipt` means
consistent with the parent's admitted facts, not independent proof or a grade.
Statuses are `passed`, `failed`, `inconclusive`, `not_run`, `blocked`, `not_applicable`;
they cannot override acceptance, cleanup or worker inactivity.

## Durability, limits and reports

The parent serializes writers under the same private-store lock conventions as the
Android reporter. Each append publishes an immutable JSON/Markdown pair with an
event hash chain, then atomically advances a flushed pointer. Renderings derive from
the same JSON. An old report pins its original history, not the mutable latest head.

Submission identity combines scope, attempt, retained source-message/receipt ID and
ordinal. Replaying identical data returns the prior event; a conflicting payload
under the same key fails. If source is absent, use a stable parent-issued submission
ID and retain the coverage gap. Reconcile saved drafts/receipts after interruption;
do not rerun app work to fill a gap or infer that an uncertain worker stopped.

Limits are separate from execution budgets: 4 KiB per event, 256 events, 1 MiB
canonical journal, 1,000 characters per stated rationale, eight entries per narrative
list, sixteen evidence references. Immutable revision retention has an additional
16 MiB byte ceiling (including prior revisions); this can fill before event/count
limits. The report summary contains at most eight current choices with 300-character
choice/reason excerpts and an explicit truncation flag. Full chronology is linked.

At a size/count/retention limit, preserve earlier data and write sticky partial
coverage with the last durable sequence and reason. No automatic pruning. At lock,
filesystem or corruption errors, the parent reports unavailable coverage while
retaining previous snapshots. Retention and recovery remain controller/operator
responsibilities; a hash cannot protect against a malicious owner rewriting history.

`recorded` means the recorded entries are available, not that every decision was
captured. `partial`, `unavailable`, `not_requested` are separate from app success.
For unavailable/not-requested history, the version-1 descriptor has scope, reason
and null `headSequence`, `headSha256`, `pointer`. Do not put the full journal into the
acceptance CLI's 128 KiB input, the exact execution ledger, or model-controlled notes.
Use the checkpoint's existing savedAs/read-back convention for the compact reference.
