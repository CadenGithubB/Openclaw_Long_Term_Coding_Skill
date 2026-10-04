# Normal-chat Android integration

The `android_project` tool lets the main OpenClaw chat write original Java source, build it in the prepared offline Linux worker, and test the resulting APK in a separate Android emulator. The chat chooses the changes; the trusted controller enforces the fixed operations and retains evidence. Installation and live outcomes belong in this stage's results, separately from this source description.

## Workflow and evidence

Read the installed long-task-runner skill, preserve the full requested outcome, and prepare one project. The first `prepare` may select `timeLimitMinutes`, an integer from 5 to 60; a new job defaults to 60 minutes when it is omitted. That selection fixes the job's initial deadline. Later preparation cannot change it, omission preserves an existing selection, and cancellation or a terminal job cannot reset it. The only way to add time is `extend`, described below. Save and read back its checkpoint before lengthy implementation. Reach a compact first build early; each accepted source revision must be built before further Android work, unless the run must stop. Repairs remain proposed until their exact revision passes the build. A small milestone never replaces the user's remaining requirements.

`build` qualifies an APK only after offline compilation, signature and package/launcher checks. `start_test`, `tap` and `observe` return actual guest observations. The model must inspect them against each requested behavior: successful launch or input delivery alone is not a behavioral pass. Finish with `stop`, a confirmed cleanup receipt, and an accurate checkpoint readback.

Each observation also returns `uiSummary`, and each tap `afterUiSummary`: bounded visible text and controls from the guest view tree, with bounds and centers in actual display pixels. The adapter refuses declarations, limits size, depth, nodes and listed elements, and strips nonprintable text; the raw XML stays retained. The tree is captured after its screenshot and the receipts time both, so a launch screenshot can still show a splash while the later tree shows the app. These summaries are untrusted observation data, not a verdict, and do not depend on image delivery. A failed dump or summary is recorded as unavailable without stopping the guest.

The controller records the adapter's measured display at `start_test`. A tap outside that display is refused before the adapter is called: no input reaches the guest, the guest is not stopped, and the qualified APK stays current, so the model can correct the coordinates without a source write or rebuild. The refusal still uses one work action. Cancellation or an expired deadline still stops the job. Any guest failure during an accepted tap keeps the existing stop-and-repair behavior.

Each receipt includes a final status and a progress snapshot: current source revision, the revision with a qualified APK, unused write/build/action limits, the selected controller duration, granted and remaining extensions, and remaining controller time. A plain-language explanation accompanies these fields. Historical jobs retain their recorded duration, including the earlier 20-minute limit. Idempotent action replay preserves its original progress snapshot; `status` reports fresh progress. Ended jobs have no work time left, regardless of unused counters.

`extend` moves the deadline of an active job 5 to 30 minutes later, with a required reason. The controller grants it only when a new successful build has occurred since the previous grant, or since preparation for the first grant. Builds are the progress event the controller can verify. A job allows at most 3 grants and 120 minutes in total, including its initial selection; a request beyond that total is reduced to the remainder. A running guest's own deadline moves first, and a guest that cannot accept it leaves the job deadline unchanged. Extensions add no source writes, builds or actions, cannot revive an expired, cancelled or ended job, and are recorded with their reasons. The request uses one work action, and a refused request changes nothing else.

The chat run has a separate time budget. Choosing a longer controller duration or extending the job does not extend that budget or an already-running chat; the operator must request enough chat time when starting a fresh run. Use the earlier deadline to reserve time for stopping, confirming cleanup, and saving the final checkpoint. A chat timeout does not establish that controller cleanup completed.

The plugin presents one JSON receipt, optional readable report text, and native screenshot blocks. Its short `details` field identifies the outcome and report without repeating the entire receipt. Recognized offline Java compilation failures are condensed to bounded unique compiler blocks, retaining source/caret and symbol/location context. Unknown or unsafe-to-condense failures keep their original diagnostic text. The controller's original receipt and complete command logs remain on disk; presentation changes never overwrite them.

The generated report explains retained facts and the model's stated reasons. `status` and `stop` return the owned report through the tool, so the model does not need host-path access. Reports and screenshots must pass the existing bounded regular-file, same-job and link checks. Reports supplement the authorized notes checkpoint and do not authorize another attempt.

## Fixed boundaries

This update makes the initial controller duration selectable within a fixed range and explains it in receipts. The controller accepts no model-supplied host command, endpoint, Docker configuration, build script or unrestricted ADB operation.

- Trusted main-session identity and verified local `ollama/qwen3.6:35b` metadata are required. Preparation checks the loopback provider, pinned model digest, no fallback and disabled Ollama cloud setting.
- The pinned worker runs as UID/GID 1000, network disabled, read-only root, dropped capabilities, no Docker socket, one project mount, two CPUs, 2 GiB RAM and 128 processes. The existing bounded project volume is unchanged.
- One job still allows 8 source writes, 3 builds and 40 work actions. Its initial duration is 5 to 60 minutes, defaulting to 60 for a new job. Progress-gated extensions can raise the total to at most 120 minutes; no existing counters, deadlines, actors or cancelled sessions are reset.
- Per-call caps remain unchanged: offline compilation and guest boot each allow at most 180 seconds, subject to the earlier job deadline. The controller client uses a 410-second transport timeout; the server and plugin retain their 420-second per-call limits. Cleanup keeps its existing bounded grace periods.
- The scaffold fixes Java, SDK 35, package `org.openclaw.trial`, manifest and Gradle configuration. Source inputs are bounded Java basenames and complete class contents; custom XML/resources, dependencies and build scripts are outside this route.
- Taps are restricted to the observed screen and bounded bursts; an out-of-display tap is refused without input. APKs execute only in the official private emulator guest; generated app code is never run on macOS.
- Preparation checks the existing 520 MiB free-space floor and requires the measured offline cache plus 100 MiB build reserve. Failure does not authorize resizing, pruning or deleting evidence.

## Recovery and cleanup

`write_sources` retires an existing guest and invalidates old APK qualification. A verified compile failure permits a focused repair in the same job. Interrupted or unverifiable mutations request actor-bound cleanup rather than silently replaying work. A later chat timeout between completed calls has no active tool abort listener; the controller's independent deadline still applies. Model-issued stop and operator-assisted cleanup are different outcomes.

Final cleanup verifies the exact worker and workspace identities before retiring only the job's derived `.android-runtime` cache. Source, APKs, logs and reports remain. Receipts distinguish stopped processes from complete cleanup; uncertain cleanup blocks further work. A crashed controller does not adopt old processes or replay uncertain actions. Historical reconciliation markers remain bound to original evidence and require fresh read-only resource checks.

## Deployment and interpretation

The plugin registers one optional sequential tool and no conversation hooks. Existing tool permissions, main-agent capabilities, notes rules, model routing and global configuration remain unchanged. The guarded stage updater verifies the prior installed files, candidate manifest, idle resources and tests before replacing the five assigned files. `install.py` remains an original-install utility and must not be used as an update command.

Tests establish the specific fixture behaviors they exercise. A successful counter diagnostic would establish only its observed build, interaction and completion sequence, not a working bird game, arbitrary long-task reliability, improved accuracy from High thinking, or a comprehensive security guarantee. See the stage's plain-language results for the exact installed version and live evidence.
