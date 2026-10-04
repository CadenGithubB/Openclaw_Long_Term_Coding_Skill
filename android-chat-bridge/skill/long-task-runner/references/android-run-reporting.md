# General Android run reporting

The environment supports Android applications generally. Retain the exact requested
features and acceptance contract. A notes app, form, background task and game have
different functional tests. Build/install/launch, package-scoped input/logs,
screenshots, authorized lifecycle actions and cleanup remain reusable operations.
Video and rendering measurements are optional capabilities, not mandatory costs.

## Write the human explanation first

Begin each evidence section with one to three short sentences for a reader who
has not seen the commands or code. Explain what happened, why the check or choice
mattered, and what its result establishes or leaves uncertain. Use ordinary words:
“the app remembered the note after reopening” is more useful than “persistence=true”.
Keep identifiers, commands, hashes and exact field names in the technical record
beneath that explanation. Explain an unavoidable technical term on first use.

Use each stage's existing `reason` for the concrete account of that stage, not the
same generic sentence copied across every stage. A useful example is: “We saved a
note, closed the app completely, and reopened it. The same text was still there,
which shows that saving survives an app restart in this test.” Only write that
when the retained test record supports it. For an unrun check, say it was not run
and give the recorded reason; never phrase a planned check as completed work.

Explain an agent's choice from its recorded statement, and identify whose
explanation it is. If no reason was recorded, say so. A later reader's explanation
must be labeled as commentary and must not replace or invent the original reason.
No additional model call is needed just to produce this prose.

The readable reports lead with these explanations and keep the exact records in
expandable technical sections. JSON remains authoritative for machine consumers;
prose cannot promote a failed, missing or mismatched check to a pass. New snapshots
carry `readableVersion: 2` so changing the presentation cannot overwrite a prior
immutable report with the same facts. Older histories remain readable with their
original renderer and event hashes. A new accepted entry uses the new presentation;
replaying an old entry remains idempotent and does not rewrite its snapshot.

## Report throughout the existing lifecycle

The parent/controller owns the report outside worker write access. Reuse its run ID,
retained contract, receipts, artifact ownership and cancellation state. Do not create
another runner or give the model generic ADB, host paths or service endpoints.

1. Before work, publish the run identity and contract with evidence still `not_run`.
2. After each reviewed receipt, update metadata and invoke the existing acceptance
   command with `--android-report-dir`. It automatically emits paired machine/readable
   files for that snapshot; a receipt must not exist solely in final response prose.
3. In the parent's existing finalization path, publish `completed`, `failed`,
   `cancelled` or `timed_out` with the actual stage results and cleanup outcome.
   Preserve useful artifacts and mark stages that never ran `not_run`.
4. After a parent crash/restart, first inspect the authoritative controller journal
   and exact owned processes. Reconcile the report from retained receipts. Publish
   `interrupted` only after that status is established; a missing client or stale
   timestamp is not proof of worker inactivity. Report generation never retries work
   or marks cleanup passed by itself. A killed process cannot publish its own final
   report; reconciliation belongs to the existing parent/status/recovery path.

The current helper is a report materializer and acceptance-CLI hook. It is not a
collector, scheduler or live supervisor installation. A real controller integration
must invoke this hook at the lifecycle points above. If that wiring or report storage
is unavailable, state that reporting is incomplete; do not claim automatic live
coverage based on this candidate's fixture tests. Preserve existing report snapshots
when publication fails. Return `reportUnavailable` alongside the failure.

## Input and independent stage results

Keep the original `{contract, report}` acceptance fields. With the report option,
add `androidRun`, following `examples/android-run-report-input.json`. The example
intentionally contains no APK, screenshots, tests or successful checks.

- `version: 1`, stable `runId`, `lifecycle`, and explicit `reason`.
- `source`: `revision`, `dirty`, `snapshotSha256`, `manifestRef`. Use null for unknown.
  A clean commit can identify source; a dirty or unknown checkout requires a retained
  bounded source-manifest hash. The helper derives `sourceIdentitySha256` from these
  fields. Record the source actually used to build; do not substitute later edits.
- `apk`: null if absent, otherwise `{artifactRef, sourceIdentitySha256}`. The artifact
  receipt contains the output location, exact SHA-256 and bytes. Its ID/hash must also
  match the acceptance report. Retain a produced APK even if later tests fail.
- `artifacts`: up to 64 IDs with relative admitted `path`, `sha256`, `bytes`, and kind
  (`apk`, `source_manifest`, `build_log`, `test_result`, `screenshot`, `recording`,
  `crash_log`, `environment`, `cleanup_receipt`). Paths are data references; the helper
  does not open/copy them. The trusted collector verifies bytes and permits only
  run-scoped evidence. Never gather credentials, source secrets or unrelated host logs.
- `environments`: `buildRef`, `androidRef`, `toolchainRefs`, pointing to retained
  environment records. Build/Android reference IDs and hashes must also match the
  acceptance report's environment map. Record actual SDK/JDK/Gradle/image/emulator
  versions and relevant configuration in those admitted records; absent access is null.
- `stages`: any of `preflight`, `build`, `artifact_verification`, `install`, `launch`,
  `automated_tests`, `functional`, `visual`, `performance`, `crash_logs`, `cleanup`.
  Each has `{status, reason, evidenceRefs, apkSha256}`. Statuses are `passed`, `failed`,
  `not_run`, `blocked`, `inconclusive`, `not_applicable`; omitted stages become `not_run`.
  Passing results need actual receipts and applicable source/APK/environment bindings.
  Invalid bindings remain visible but are downgraded to `inconclusive`.
- `crashes`: `{status, package, startMs, endMs, observedCrashes, evidenceRefs, reason}`.
  `collected` requires a package-scoped recorded window and crash-log receipt tied to
  the APK. `not_run`/`unavailable` use null count/window/package and no invented log.
  Zero means no crash observed in that window, not a crash-free application. A crash
  from another APK remains recorded without being attributed to the current APK.

Keep stage results independent. A build can pass while runtime fails. Exit zero and
launch do not satisfy app-specific behavior. Functional success still requires the
existing acceptance evaluator's retained `behavior` criteria. Optional performance
criteria use the distinct `performance` level and executed applicable measurements.
Video PTS and visual opinions cannot establish rendering performance.

Required stages follow the retained contract's evidence levels; they are listed in
`requiredStages`. Do not force an unrequested runtime or visual test onto a build-only
request. Such a request can finish with runtime/functional stages explicitly not run.
Conversely, requested behavioral/visual/performance criteria cannot be skipped.
Preflight and cleanup are retained for every run; cleanup may be not applicable when
the parent has evidence that no owned process/resource needed cleanup.

## Durable output

In an already-created private, parent-owned directory:

```sh
python3 <skill>/scripts/acceptance.py --android-report-dir <owned-report-directory> < <retained-input.json>
```

The command retains its strict 128 KiB input limit and duplicate-key checks. Without
the option, the old acceptance-only interface remains unchanged. With it, exit 0
requires complete acceptance and successful required run stages; 1 means incomplete;
2 means invalid/unavailable. Lifecycle and build/runtime outcomes remain in the report.

Each snapshot has immutable `report.json` and `report.md` files. A file lock serializes
publication. Files and directories are flushed before the atomic `latest.json` pointer
advances; the pointer includes both hashes. Repeating identical receipts is idempotent.
If publication is interrupted, reconcile the same input; never rerun the application
to repair a report. Up to 64 snapshots are retained; the helper fails explicitly at
the limit and never deletes prior evidence. Retention/archive policy belongs to the
existing trusted controller. Private immutable ownership is a prerequisite, not a
defense against an adversary controlling this report directory.

Return the readable report and its manifest reference with the APK/hash, source
identity, tests, collected screenshots/recordings, crash window and unfinished checks.
If an artifact was never produced, say unavailable and why. Hashes and valid JSON
establish consistency, not authenticity; the parent must inspect actual receipts.

## Optional decision history

The report CLI accepts a fourth, optional `decisionHistory` field only with
`--android-report-dir`. Use the versioned descriptor from the trusted parent's
[decision-history helper](decision-history.md), never the full journal. It pins a
revision beneath `<report-root>/decision-history`; later entries cannot alter an
older report's chronology. Report summaries contain at most eight current choices.

History availability and `historyReportingComplete` are separate from application
acceptance and the CLI's existing exits. Broken/missing history becomes unavailable;
it does not turn a passing build into a failure, trigger a retry, or erase receipts.
Report consumers must inspect that separate field before claiming documentation is
complete. Legacy inputs without the extension retain their existing output shape;
the omitted field means this reporting extension was not requested.
