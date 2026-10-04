# Coding verification

Read this for multi-step coding, unfamiliar build targets, or repeated repair/check
failures. These helpers improve a cooperating workflow. They do not authenticate
model-written JSON, grant tools, install dependencies, or supervise bypassed calls.
The parent retains the original request/contract, classifies evidence, and checks
the actual observations. Keep run and cancellation records in the existing notes
workflow. Per-worker `/tmp` journals are operational evidence, not persistent memory.

## Environment and first executable milestone

Use the real skill directory and project path returned by the environment:

```text
python3 <skill>/scripts/preflight.py --profile android --project <project>
```

Profiles are `python`, `javascript`, `android-build`, `android-emulator`, and the
combined `android`. Use `android-build` in a build worker and `android-emulator`
on the separate test machine. A build worker does not need an emulator, and an
emulator machine does not need Gradle or the source project. For the emulator
profile, pass its writable test-artifact directory as `--project`. The probe records OS/architecture,
Python identity, executable locations, a temporary write-and-remove test, and missing
or unverified capabilities. It does not install, download, boot an emulator or run
project code. Android discovery reports the configured SDK and Gradle route; tools
outside PATH or an unconfigured SDK need a measured follow-up, not a claim that they
cannot exist. A Gradle wrapper's presence is not permission to run unknown code.
Missing prerequisites return `prerequisites-missing`, `setupRequired: true` and
`authorization: not-assessed`. Exit 2 means the environment is incomplete; it does
not mean setup was denied or the user's task should be abandoned. Continue already
authorized setup work. `setupRequired: false` means no gap was detected by this
probe, not that an offline build or emulator has been verified.

Record implementation and testing environments separately. An Android build may
need a different prepared environment from its emulator. Use only an available,
authorized executor for each; `host` prose or an alternate path does not switch an
`exec` tool out of its sandbox. Check installed tool versions/compatibility there.
Bind each result to the actual task workspace. On this installation, workshop-skill
sessions and ordinary task sessions can use different containers despite sharing
an agent name and image. A workshop session's missing project is not evidence
that the task session lacks it. Have the operator verify the exact container's
workspace mount before diagnosing or stopping that worker.
Do not weaken confinement to make a missing capability available.

Validate prepared tools through the same native `exec` route the agent will use.
The executor can replace an image's `PATH`; a successful operator-side Docker
command does not prove the agent can find Java or SDK tools. Use the executor's
supported tool-path configuration and recheck actual commands before changing the
project or installing another copy. In this installation, `tools.exec.pathPrepend`
with the prepared `/opt` tool directories fixes that mismatch without changing the
container policy.

When local inference is required, verify the selected model is installed locally,
pin its provider/model and remove cloud fallbacks from the isolated test profile.
A loopback provider URL alone does not establish that the model server runs the
model locally. Keep this check separate from the build worker's network isolation.
For a local-only Ollama run, inspect the selected model's local weights/digest and
reject remote model/host routing or cloud fallbacks before sending task data. A
cloud-capable plugin is not proof of cloud use, and local weights are not proof that
the shared server has no outbound access. Verify server-side network restrictions
separately when claiming enforced offline inference; do not silently reconfigure a
shared service. If the required local route is unverified, withhold the model call.

Prove the smallest original app can build, install and launch before expanding it
into the full requested application. Preserve failures and exact missing prerequisites.
Tool installation/setup may continue when already in scope and available through
the authorized tools. If the required route is unavailable, return the specific
unmet requirement while preserving any useful source work. Do not substitute a
browser/desktop/manual-delivery outcome without the user's agreement.

For a worker without network access, use an authorized provisioning step to prepare
a pinned toolchain and the exact project's dependencies ahead of time. Record source
URLs, versions, operating system/architecture and verified checksums. Android needs
a compatible JDK, Gradle distribution, Android Gradle plugin, SDK platform/build tools
and their transitive dependencies. An SDK ZIP alone is not an offline build cache.
Keep legitimately accepted SDK license records with the installation; do not infer
license acceptance from permission to download tools.

Supply the prepared image/SDK read-only and give each attempt a separate writable
Gradle home seeded from the prepared cache. Use the same compatible Gradle version
for preparation and execution. Pre-seed the wrapper distribution too: `--offline`
does not make a missing wrapper ZIP available. Freeze dependency versions and run
the actual build/test tasks during preparation; a dependency listing alone may omit
task-specific downloads. Then repeat from clean project outputs with `--offline`
and the worker's network disabled. A missing cache entry remains setup work. Keep
network isolation intact while refreshing the approved bundle outside the worker.

Prepare emulator binaries and the system image separately for the actual test
machine. Downloading them does not prove that its architecture, acceleration,
display or device access works. Do not promise an accelerated emulator inside a
Docker VM; measure the supported test route before claiming Android execution.
Keep ADB and the emulator on an isolated, explicitly identified device connection.
Wait for guest readiness before install/launch; a boot log alone does not prove
ADB availability. Record the exact APK digest, device serial, install and activity
results, visible before/after state, and termination of owned processes. Preserve
initialization/transport failures separately from application failures.

## Retained acceptance contract

Retain one parent-owned contract before implementation. The example in
`examples/android-contract.json` demonstrates separate source, build, launch and
behavior requirements for a notes app. The original game example is retained in
`examples/android-game-contract.json`; its gameplay checks apply only to games.
Replace the example request and checks with the actual user request;
do not force Android requirements onto unrelated tasks. The writing worker must not
replace the retained baseline with its own easier criteria.

`acceptance.py` reads `{ "contract": ..., "report": ... }` from stdin. It exports
`digest_request(text)`, `digest_contract(contract)` and `evaluate(input)` for a
reviewed caller. Exit 0 means submitted evidence covers the contract, 1 incomplete,
2 malformed or mismatched. Independently inspect evidence; this is a consistency
gate, not a substitute for a trusted executor. `checkpoint.py` remains the run-budget
bookkeeper and cannot override this result.

A complete input example with every requirement still pending is packaged as
`examples/android-pending-input.json`. Passing it to the CLI must return incomplete:

```text
python3 <skill>/scripts/acceptance.py < <skill>/examples/android-pending-input.json
```

To record a real command receipt, copy its `argv` to `command`, actual `exitCode`,
`completedAtMs` to `checkedAtMs`, and retained receipt digest to `recordSha256`.
Set `performed` true only after the command ran. Bind `artifactSha256` to the exact
artifact/tree digest in the report and `environmentSha256` to the retained measured
environment record. Set the semantic level and explicit checks after reviewing what
the command actually measured. For example, a successful `check_source.py` report
can add a `syntax` record with a passing `parser-accepted-source` check; it cannot
add a `launch` record. A wrapper exit zero alone supplies no passing assertions.
Keep the actual output log beside the receipt so the parent can verify this mapping.

Contract fields:

- `version: 1`, `request`: exact original request/constraints.
- `criteria`: each has `id`, `check`, `requiredLevels`, `artifactId`, `environmentId`.
- Levels: `source`, `syntax`, `build`, `launch`, `behavior`, `visual`, `performance`. They are distinct,
  not interchangeable ranks. A successful syntax receipt cannot cover launch.
  Performance requires an executed, applicable measurement with explicit checks;
  an inspection saying the animation looks smooth does not qualify.

Report fields:

- `requestSha256`, `contractSha256`, `criteria`: the retained definitions unchanged.
- `asOfMs`: report time; `artifacts`: IDs mapped to `{sha256, changedAtMs}`;
  `environments`: IDs mapped to `{sha256}` of retained measured environment records.
- `evidence`: records with `id`, `criterionId`, `level`, `artifactSha256`,
  `environmentSha256`, `checkedAtMs`, `performed`, `kind`, `command`, `exitCode`,
  `checks` (nonempty `{id, passed}` observations), and `recordSha256` of retained
  evidence. Use argv arrays for commands. Inspection records use null command/exit.

The gate refuses changed/removed requirements, missing levels, stale artifact or
environment bindings, future/unperformed checks, missing assertions and failed exits.
A newer failed check supersedes an older pass. Record a legitimate user scope change
as an explicit parent-owned contract revision retaining the previous contract and
the user's amendment; do not silently rewrite it in a worker response.

Every Android run also retains the [run report](android-run-reporting.md), including
explicit missing evidence on failure or interruption. Derive behavior from the app:
notes need creation/editing/persistence across relaunch; forms need validation and
state restoration; permission/background features need the relevant lifecycle checks.
Use normal Android tests and UI automation where applicable. Check that important
controls are visible, enabled and outside system bars or keyboard overlays. A tap
command's exit code does not prove activation: verify the resulting UI or saved
state before advancing. If a visible-area diagnostic works after the normal tap
fails, retain the original failure and distinguish that diagnostic from an app fix. Temporal recording
is optional for custom rendering/animation or other time-dependent requirements.

For games, behavioral evidence should cover input, motion, collisions, score and
restart. Graphics claims need observed frames/interaction on the current artifact:
two identically drawn wing frames are not an animation. A claimed Android test needs
Android execution evidence. Retain screenshots/video/log locations and exact build
identity. Do not label a browser-only observation as an emulator test.

## Check the checker

```text
python3 <skill>/scripts/check_source.py --language javascript --file <file>
python3 <skill>/scripts/check_source.py --language html --file <file>
python3 <skill>/scripts/check_source.py --language python --file <file>
```

The helper self-tests its parser with valid and invalid inert input. Python parsing
and compilation do not execute the source; Node uses `--check`. HTML uses a parser,
not an assumed `<script>` offset. External-script/runtime coverage remains unverified.
Missing parsers, parser self-test failures and incomplete coverage are nonzero exits.
Do not suppress failures with `|| true`. Syntax-only success cannot establish behavior.

When a custom test fails, inspect its raw input and test a known-good/known-bad case
before rewriting the application. Preserve its failing receipt. Avoid repeatedly
rewriting whole files while the checker itself is suspect.

## Within-attempt progress

`work_step.py` runs commands only on Linux, inside a worker already authorized by the
parent. Linux detection alone does not establish a sandbox; this is not a host-entry
route. Read the parent's actual authorization/environment before using it. It does
not create workers. Do not use it to restart services or invoke another agent CLI.

```text
python3 <skill>/scripts/work_step.py init --state <new-private-worker-tmp-dir> --project <project> --watch src/game.js --watch index.html --max-actions 24 --repetitions 3
python3 <skill>/scripts/work_step.py run --state <same-dir> --phase game --key syntax --kind check --check syntax --timeout 60 -- python3 <skill>/scripts/check_source.py --language javascript --file <project>/src/game.js
python3 <skill>/scripts/work_step.py status --state <same-dir>
```

Declare the relevant source/artifact watch paths before work; missing files are
recorded explicitly and may be created. Use stable semantic phase/key/check IDs, not
new names for each retry. Counters survive wrapper restarts. Choose budgets for the
actual scoped attempt, including known compilation time; the example is not a
universal limit. The wrapper records exact bounded output bytes, exit result, watched
file digests and a pending reservation before execution. A check that changes watched
artifacts fails. A timeout or output cap fails. An interrupted pending action cannot
be replayed; parent review and actual inactivity confirmation are still required.

After repeated identical failures it requests diagnosis; after unchanged writes or
repeated passes of the same check it requests review and refuses further commands
through that attempt journal. Byte changes alone are not verified milestones, and
the cumulative action budget bounds rewrite churn. These decisions do not mark the
task complete, cancel a native worker, or prove all subprocesses/provider work stopped.
Do not delete/reset the journal or rename actions to escape the result.

For external tools, `progress_watch.py` accepts controller-fed ordered receipts with
`format:1`, `limits:{maxActions,repetitionLimit}` and `events`. The action bound
is 1..256 and the repetition bound is 2..16. Each event contains
`actionId`, `phaseId`, `actionKey`, `checkId` (required, null except for checks), `kind`
(`edit/check/inspect/execute`), `state` (`pending/completed/passed/failed` as applicable),
`beforeSha256`, `afterSha256`, `resultFingerprint`, `verifiedMilestoneIds`. Pending
snapshots have null result/after; repeated snapshots retain the same action ID and
do not consume another action. Only genuine passing checks may assert milestones.
Keep the full history. A worker-editable journal or supplied JSON can be bypassed;
the parent must inspect receipts and apply the same review to direct tool calls.

## Replay the original task

Before claiming a reliable release, record the actual loaded skill/package hash and
model identity, then evaluate the original user prompt in a clean authorized project.
Do not give the evaluator this case's expected answer. Check the actual artifact and
Android interaction, then test interruption/resumption of the real project. A data
fixture, another model's dry run, or deterministic arithmetic test cannot substitute
for that live acceptance. Preserve blocked and failed outcomes instead of tuning the
prompt until the desired report appears.
