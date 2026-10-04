---
name: long-task-runner
description: Complete long coding tasks with sandboxed OpenClaw subagents, durable checkpoints, verification, and bounded recovery. Skip small single-turn tasks.
metadata:
  openclaw:
    emoji: "🏁"
    category: automation
---

# Long task runner

Use the registered Android route below when available for an Android application; other long coding work uses OpenClaw's native announcing subagents. This is a checkpoint-and-review workflow, not a background daemon: it cannot restart a stopped Gateway or guarantee that a model keeps working. Never start another agent CLI, bypass permissions, disable the sandbox, or build a shell polling loop.

## Reasoning for complex work

For complex implementation or debugging, inspect `session_status`. Unless the
user explicitly chose another mode, request high thinking if supported and not
already high or higher: discover the registered `sessions` tool and its calling
wrapper, then use `action: "patch"`, `thinkingLevel: "high"`, omitting `sessionKey`
to target the current session. Do not invent a deferred tool ID. Recheck the thinking setting reported by `session_status` and record it; preserve the
model, provider and global configuration.

A patch records the requested session mode; the current run may have captured
its setting at startup, so it does not prove that an active generation switched.
For a reliable trial, the operator starts the run with high requested beforehand.
Qwen 3.6 uses boolean thinking; high is not evidence of a distinct stronger mode,
and `/think` prose is not a supported switch. Preserve the original budgets:
never restart the session, reset counters or create a continuation loop to apply
the change. Report the observed mode and measured elapsed time, and verify the
work itself rather than claiming improved accuracy from the setting.

## Android through the registered project tool

For registered `android_project`, the main agent writes through the trusted
controller's Linux worker, scaffold and separate emulator.
Use this action order and the shared evidence-explanation rules. The generic
shell preflight, delegation, acceptance/report CLI and operator-runner workflows
below do not apply to this route. Do not probe host paths or spawn another writer.

1. **Retain the full request.** Keep every requested artifact, behavior and constraint
   as an acceptance check. Read the installed `notes` policy and use `note_read`
   for the intended checkpoint. A partial milestone never replaces the full
   application or its remaining checks.
2. **Prepare, then checkpoint.** `prepare` checks the actual environment and
   returns `jobId`. For a new job, its optional `timeLimitMinutes` is an integer
   from 5 to 240, defaulting to 120; the initial budget is set once. Choose less
   only when the user's or chat's deadline is shorter. `prepare` takes no `jobId`.
   Before lengthy source generation, use `note_write` to save a compact initial
   checkpoint with the goal, checks, job/report identity, selected time budget and
   next action; inspect the result, then use `note_read` to verify it. Mark
   unperformed checks `NOT RUN`. If notes are unavailable, report the recovery gap and continue
   feasible authorized work without bypassing the notes policy.
3. **Reach an early build.** Use original native Java with the fixed offline SDK 35
   scaffold. Make the first implementation compact, covering a real portion of
   the requested app; retain remaining requirements as incomplete.
   Do not substitute an unrelated demo. After every accepted `write_sources`,
   including a repair, make `build` the next Android work action if continuing.
   Cancellation, exhausted time or limits take precedence: stop instead.
4. **Repair the actual failed checks.** Compare every unique compiler location
   with the submitted line, declaration and types. Preserve
   unaffected source. Record the failed check and next repair in the checkpoint;
   submit the repair and rebuild. A repair remains unverified until that exact
   source revision builds; do not claim errors are fixed from an accepted write.
5. **Test the qualified app.** Only a verified current APK qualifies for
   `start_test`. Include the same returned `jobId` in `start_test`, `tap` and
   `observe`. Inspect actual screens and observations against
   every retained behavior, including gameplay for games. Launch or delivered
   input alone proves no behavior. Keep unobserved checks incomplete.
   `uiSummary` (a tap's `afterUiSummary`) lists visible text and controls with
   bounds and centers; it is captured after its screenshot, so a launch frame may
   still show a splash. Observe again when they disagree. Cite each capture only
   for what it shows: a splash or loading frame proves nothing about the app, so
   name the evidence actually used (for example the tree or a later frame). Tap a control's `center`
   in actual pixels of the reported display (`0 <= x < width`, `0 <= y < height`);
   never scale into the schema's 0–8192 bound. An outside tap is refused without
   input or guest stop: correct it with the same `jobId`, without a rebuild.
   Games keep running while you think, and your turns take tens of seconds: a
   single tap followed by a later `observe` shows only the aftermath (a bird that
   already fell is not proof the tap failed). Exercise gameplay inside one `tap`
   by setting `count` (up to 30) and `intervalMs` (80–1500, at most 15 seconds in
   total); a burst of 4 or more also returns frames captured during the burst.
   Judge motion, scoring and collisions from those frames, not from one later
   screen.
6. **Leave time to finish safely.** Read receipt counters, source qualification
   and remaining controller time when provided. The chat/runtime deadline may
   already be fixed and shorter; choosing a job budget does not extend the current
   chat. Keep the original job and selected time limit, with at most 20 writes,
   10 builds and 120 actions for the whole chat session. If work remains after new
   successful work since the last grant (a source write, build, launch, tap or
   observation), `extend` may add 5–30 controller minutes. Give a `reason` naming
   the remaining checks. It allows at most eight grants and 240 minutes in total
   including the initial limit; it adds no chat time, writes or builds, and each
   request uses one action. Your turns can take several minutes, so request time
   when about 20 minutes remain, not at the deadline; receipts warn below 15
   minutes. A refused extension means finishing or stopping within the remaining
   time. Do not create automatic continuation loops or change global
   configuration to gain time. Before lengthy generation,
   reserve time within the earliest job, chat or user deadline for `stop`,
   confirmed cleanup, and final checkpoint save/read-back.
   Finish with verified progress, remaining checks and cleanup state.

Use the returned `jobId` for `write_sources`, `build`, `start_test`, `tap`,
`observe`, `extend`, `status` and `stop`. `android_project` is a direct tool in
your tool list: call it by name with its parameters, not through `tool_call`, so
its screenshots arrive as images you can inspect:

```json
{"action":"prepare","timeLimitMinutes":120,"reason":"Check the prepared offline tools and create a clean project before writing the app."}
```

Include `action` in every call. This source-write example is structural; replace
both placeholders with the returned job ID and complete original Java source:

```json
{"action":"write_sources","jobId":"<returned jobId>","files":[{"name":"MainActivity.java","content":"<complete original Java source>"}],"reason":"Write the app using the prepared Android project."}
```

Returned screenshots are also shown to the operator in the chat, with a caption,
whenever the display copy succeeds; never resend screenshot paths with other
tools or `MEDIA:` lines.

Only if `android_project` is absent from your direct tools, call it through
`tool_call` with `id` outside `args` and these parameters inside it as a JSON
object; screenshots then arrive as text and cannot be viewed, so rely on
`uiSummary` and say so in the report.

If validation rejects a call before execution, correct its envelope,
retaining the job and intended source; do not rewrite the app or repeat
`prepare` for that error. `write_sources` retires a previous guest and invalidates
the old APK qualification, so changed source must be rebuilt and retested.

Use the user's exact checkpoint `title` unchanged in `note_read`/`note_write`;
never append `/checkpoint` or another subpath. Read before replacing a note.
Otherwise prefer an already authorized existing folder. A pending or denied
folder request is not permission. Use the returned `savedAs`, not an invented
save; do not substitute workspace files as persistent memory. The controller's
retained report is evidence, not a replacement checkpoint or permission to retry.

Supply a short plain-language `reason` for each material action. Inspect the
controller's reasons, receipts and artifact identities before reporting results.
Use completed-tense evidence only when receipts establish it; an unchecked box
does not make a completion claim accurate. Copy counts, identities and causes
into checkpoints from receipts; write "unknown" rather than estimating one. Completion requires every requested
acceptance check to pass at its required evidence level.

Use `status` after uncertainty and `stop` when finished or cancelled; confirm its
receipt. Uncertain cleanup and exhausted limits remain incomplete. On
`cleanup-uncertain` or unconfirmed cleanup, end the attempt for operator
reconciliation. After a run is cancelled, do not repeat `prepare` in that
session; use only registered `status`/`stop` for inspection and report the known
cause and unknowns. After your own `stop` or a job's expiry with confirmed
cleanup, `prepare` (without `jobId`) may start a continuation job when requested
work remains. It receives only what the session has left of its writes, builds,
actions and 240 minutes, and starts from a fresh scaffold, so resend complete
source. A refused continuation means the session's limits are used: report the
outcome instead of retrying. A tool failure does not establish
that host tools, Docker or the emulator are missing or down. Do not invent restart
commands. Failure authorizes no shell, agent CLI or `curl` fallback, nested model
run, automatic restart, arbitrary build scripts or unrestricted ADB.

## Start the work

1. Preserve the user's exact requested outcome and constraints in an acceptance contract before implementation. Keep each requested target and behavior as a separate check; follow-ups add requirements unless the user explicitly replaces them. Build instructions, source files, and a browser prototype do not satisfy a requested Android build or emulator test. Use the contract and evidence checks in [coding verification](references/coding-verification.md).
2. Check the actual implementation and test environments before choosing a framework. Run the packaged `preflight.py` in the intended worker, using its actual project path and appropriate profile (`android-build` and `android-emulator` when those run separately). If Python/helper access is missing, use available read-only tool/version probes and report the limitation; do not loop on a missing helper. For unfamiliar platforms, prove a minimal build and launch before expanding the implementation. A tool path is not proof of a usable compiler, emulator or writable host project. Missing prerequisites are setup work when installation is already authorized; they do not imply denial. For workers without networking, prepare pinned tools and project dependencies through the authorized provisioning route, then verify the actual build offline as described in coding verification.
3. Read the installed `notes` skill. Save a checkpoint through `note_write`, normally under an existing `projects/<outcome>/` folder. Follow folder consent and read-before-replace rules; use the returned `savedAs`. Do not use workspace files as persistent memory on this host. Record goal, authorized scope, acceptance checks, worker run IDs, verified progress, next action and blockers. The parent alone writes this checkpoint; workers return evidence.
   If checkpoint storage is denied/unavailable, say that recovery is not durable. Do not invent a different storage location or abandon otherwise feasible in-turn work.
4. If the request is small enough to finish and verify in the current turn, do it directly. Otherwise delegate the implementation with `sessions_spawn`.

Use the real tool schemas, not remembered aliases. Notes use `title`, not `path`:
`note_read({"title":"<savedAs>"})`; `note_write({"title":"<savedAs>","content":"..."})`.
For deferred tools, discover/describe them and follow the current calling wrapper.
Do dependent calls separately: save, inspect its result, then read back. A validation
error is not a successful save. Do not claim a run ID is recorded until it is saved.

## Delegate safely

For an ordinary long coding task, spawn one writer at a time. Use `task` (not
`objective`) for the assignment, and JSON numbers/booleans, not strings:

```json
{"task":"<scoped assignment and exact acceptance commands>","runtime":"subagent","mode":"run","context":"isolated","sandbox":"require","runTimeoutSeconds":7200,"cleanup":"keep","expectsCompletionMessage":true,"taskName":"<stable-lowercase-name>"}
```

The task prompt must include the goal, allowed scope, acceptance checks, current verified state, exact working directory, measured environment, retained acceptance contract, helper locations, and required verification. Pass these facts explicitly: an isolated child does not inherit your conversation or every skill. Do not tell it to read unrelated private notes. Tell it to inspect current state before editing, preserve unrelated changes and return changed paths, commands, exit codes, remaining work and blockers. It must not declare the user's entire request complete.

Only edit a project location the sandbox actually permits. This host's bootstrap workspace is read-only by policy. A writable scratch directory is not permission to edit host projects or a promise of durable artifacts. If the requested repository is unavailable, report that exact limitation instead of claiming coding success.

Use at most one writer concurrently in a shared workspace. A separate reviewer may run concurrently only when it is explicitly read-only. Do not ask a child to weaken permissions or invoke `openclaw`, Codex, Claude, or another agent CLI from `exec`.

Inspect the spawn result first. Only `status: "accepted"` with a run ID creates a
running worker. Save that run ID and the returned child session key as
`child_session_key` in the checkpoint, read it
back, then call `sessions_yield` with a short acknowledgment. Do not batch spawn and
yield: a rejected spawn leaves nothing to wait for. Completion is push-driven;
inspect history/status only to diagnose a missing/failed result or on user request.

For an extended coding attempt, use the [bounded work-step workflow](references/coding-verification.md#within-attempt-progress) for command-based edits and checks inside the already authorized Linux worker. Set up one attempt journal with stable check/action names and declared watched artifacts. Preserve its counters throughout the attempt. A diagnosis/review result means return the evidence to the parent, not reset the journal or rename a repeated check. Read-only tool inspection may diagnose the failure; another implementation attempt follows parent review and the existing total-run limit. The wrapper does not create a sandbox or authorize host execution. Native writes outside the wrapper must also be included in parent review; the helper cannot observe or constrain bypassed tools.

The two-hour limit is a circuit breaker, not a target. A timed-out worker may have partial side effects: inspect them and record the outcome before retrying. If cancellation or a new directive arrives, record it and use the available native cancellation tool for the recorded child. A cancellation request or the helper's `stop` decision does not prove the worker stopped. Confirm that the old writer is inactive before starting a replacement; if this is unconfirmed, report the uncertainty and withhold another writer. Do not restart obsolete work from a stale checkpoint.

## Review and continue

When a child reports back:

1. Treat its reply as evidence, not proof.
2. Inspect changed files and run the exact acceptance checks in the requested environment. Use the packaged `check_source.py` for supported syntax checks; its known-valid/known-invalid parser probes distinguish checker failures from source failures. Check the checker and raw file before a rewrite when they disagree. Preserve the last working version and make focused repairs. Do not silently substitute an interpreter, weaken an assertion or omit a failed case to obtain a pass.
   Keep source, syntax, build, launch, behavioral and visual evidence distinct. A parser pass, function-name search or printed success label proves no gameplay or graphics behavior. Record actual commands, exit results, artifact/environment digests and retained logs or observed frames. Use `acceptance.py` against the independently retained contract; inspect the referenced evidence before trusting its data-only result.
3. Update the checkpoint with verified facts, run status and whether this attempt made material progress. Keep counters across continuations, not just within the current turn.
4. If safe, in-scope work remains, spawn a fresh repair or continuation child and yield again. Put the failed check and exact next action in its task prompt.
5. Stop after two consecutive reviewed attempts make no material progress, or after eight child runs for the same request. Report the evidence and remaining work instead of looping indefinitely. A retry limit is not a successful completion.

For multi-run work, use the data-only helper and checkpoint schema in [references/checkpoint.md](references/checkpoint.md) to check this decision. It reads JSON from stdin and never executes commands from the note. Its result checks bookkeeping, not truth: you still have to run the acceptance checks yourself. If Python is unavailable, apply the same rules directly and disclose that the helper was not run.

Do not mark the request complete while an acceptance check is failing, skipped without justification, or merely asserted by a child. Do not broaden scope to deployments, external messages, purchases, credential changes, destructive cleanup, or permission changes without the user's authorization.

## Finish

Finish only when the requested artifact exists and every retained acceptance check passes at its required evidence level. A missing runtime or unavailable test remains incomplete. Run the acceptance evaluator as well as the existing checkpoint bookkeeping; neither can replace inspecting genuine evidence. Save and read back the final checkpoint. Summarize result, actual verification, caveats and checkpoint title. Leave task records and retained child transcripts available.

After a restart or an explicit resume request, read the checkpoint and inspect actual artifacts/run status before continuing. Recheck the recorded child before starting another writer, including after a recorded cancellation or timeout. Do not start duplicate work merely because the last note says running. Resume only the current authorized request; never sweep old notes to restart arbitrary tasks. Use the native `/goal` feature only when explicitly requested and available; a stored goal alone is not an automatic scheduler.

## Explain the evidence

Lead each human-facing evidence section with one to three plain-language sentences:
what happened, why it mattered, and what the result means or leaves unknown. Keep
commands, identifiers and hashes beneath that explanation. Attribute reasons to
recorded statements; say when a reason is missing instead of guessing. Follow the
[report writing guidance](references/android-run-reporting.md#write-the-human-explanation-first).

## General Android development and run reports

Android work covers the requested application, not only games. Derive functional
checks from the user's app: persistence, forms, permissions, lifecycle or other
requested behavior. Keep game-specific checks confined to game tasks. Recording
is optional when temporal evidence helps; it is not required for every application.

For each Android run, use [run reporting](references/android-run-reporting.md).
The parent retains a report before work, refreshes it after reviewed receipts, and
reconciles it on completion, failure, cancellation, timeout or interruption. Keep
build and functional success separate. Use the existing acceptance command's report
option; it does not collect artifacts or replace the controller's lifecycle logic.

When decision history is requested and the trusted parent has the capture hook,
use [decision history](references/decision-history.md). Return brief stated reasons
for material choices in the existing checkpoint/return; record missing explanations
as missing. This is documentation for later requested review. It adds no model call,
automatic learning, self-correction, policy update or background reviewer.

## Animation-aware Android verification

For motion, transitions, disappearance, collision behavior or rendering performance,
read [animation testing](references/animation-testing.md). Confirm the required
capture and image-delivery capabilities are actually registered before using them.
Keep capture, visual, performance and model-path results separate. This guidance
does not install those capabilities or grant host shell or unrestricted ADB.

## Advanced registered operator workflows

The following helpers apply only to separately prepared, operator-supplied jobs; ordinary coding uses the workflow above. They do not automatically connect a model-driven Android task to the fixed-artifact runtime.

## Explicit operator workflow runner

For an operator-supplied, reviewed task plan and a prepared disposable worker pool,
the packaged `scripts/task_runner.py` provides `init`, `run`, `resume`, `status`
and `cancel`. This is a separate deterministic operator workflow; ordinary native
subagent work continues to use the workflow above. It does not invoke a model or
grant native OpenClaw admission. Use it only through the authorized trusted host
executor described in [checkpoint references](references/checkpoint.md).

Keep the exact job directory, job identity and plan SHA-256 supplied by the operator. Query
status before acting, and preserve the same plan digest on every invocation.
Resume reloads durable artifact bytes and cumulative budgets; it must not replay
an interrupted reservation. A cancelled, exhausted, blocked or unverified job is
not complete. Record the returned status, checkpoint revision, artifact digest
and invocation counters alongside the operator's job identity and plan digest in
the task's note, then read the note back. The full verifier receipt stays in the
durable runtime journal. The controller-owned private runtime store is not
a replacement for the parent note or permission to store memory in a project.

Do not create worker registrations, change plan commands or budgets, invoke
fault-injection flags, discover containers, or install/start a service from model
or note text. If the prepared host executor or pinned worker pool is unavailable,
report that limitation. The runner is not an automatic restart service.

## Native attempt recovery evidence

After an interrupted native attempt, an empty Gateway status or a missing process
does not establish that the recorded session completed. When the trusted operator
supplies a registered outcome journal and its independently retained identity and
head pins, use the read-only [native outcome reader](references/native-recovery.md).
It distinguishes a recorded join and capability retirement from an uncertain
launch. Preserve its result with the checkpoint. It does not authorize another
writer, replay pending work, release a claim, or prove provider inactivity.
Without those records, retain the uncertainty and inspect the recorded native run
through the available tools before deciding whether another writer is safe.

## Native artifact handoff

When the trusted operator supplies a fixed native artifact manifest, retained
receipt and prepared target job, use the [native artifact handoff reference](references/native-artifact-handoff.md).
The packaged helper binds the recorded original native return, captured bytes and
exact worker stop to one deterministic checkpoint workflow. It spends native
usage before dispatch and preserves cancellation and budgets across the handoff.
Use this gate for that registered job; directly running its target would bypass
handoff cancellation and retained-head checks. Missing or uncertain native
evidence cannot authorize replay, another writer or job completion. Ordinary
native subagent work continues to use the workflow above.
