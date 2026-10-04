# Handoff for the next coding session

## Start from the deployed source

The deployed implementation is **stage 4AQ**, installed on the existing Mac Studio on the afternoon of October 4, 2026. It is the stage 4AO final2 source plus the stage 4AP candidates 1–2 and the 4AQ changes described below. `android-chat-bridge/` holds its publication copy. Public files replace service-account paths with `/CONFIGURE/...` placeholders and remove original volume identity values; [SOURCE_VERSIONS.json](SOURCE_VERSIONS.json) records both the publication and the installed manifest fingerprints. The source manifest binds 45 files; the README records its fingerprint. The controller, plugin, emulator adapter and skill belong together. An older standalone skill copy does not include the complete Android route.

The live installation is operated through Apple Remote Desktop using the configured service account. Its UNIX command task is also scriptable from the operator Mac with AppleScript (`send unix command task` with a `user` property; `execute` returns the command output), which avoids driving the window interface. The local repository is a source and documentation checkout, not the live host. The original detailed evidence remains in the operator's private project archive. Do not infer a new live success from a saved transcript or fixture test.

This GitHub repository was empty when inspected for this publication. The initial commit contains the current source and reviewed summaries, not the private operational archive or the Android SDK/image binaries.

## What changed before the October 4 morning test

Receipts now distinguish the current source revision from the revision with a qualified APK, display remaining work/time limits, and explain the result in plain language. Failed builds report the recovered controller status. The plugin conservatively shortens recognized compiler output while preserving error context and the original controller logs.

The Android skill spells out checkpoint save/readback, an early build, a rebuild after each repair, actual behavior checks, and stop/final-checkpoint steps. It names the notes tools and requires the same job ID for every emulator action. These instructions improve the workflow contract but do not force the model to comply.

New jobs default to 30 minutes and can initially select 5–60 minutes. The selection cannot change an existing deadline. Fresh chats need their own explicit timeout; selecting a longer Android duration does not extend a running chat. Source writes, build attempts, work actions and isolation limits are unchanged.

## Stage 4AP candidates (deployed as part of stage 4AQ)

Candidate 1 addressed two failures recorded in the October 4 morning counter trial. Candidate 2 adds the time-budget changes requested after it, described at the end of this section. Both were written and fixture-tested in a Linux cloud container without access to the Studio, then deployed together with stage 4AQ. [SOURCE_VERSIONS.json](SOURCE_VERSIONS.json) lists their manifest fingerprints and changed files.

- **Out-of-display taps no longer cost a rebuild.** The controller records the display size measured by the adapter at `start_test`. A tap outside it is refused before the adapter is called, with `inputDelivered: false` and the allowed range. The guest keeps running and the APK stays qualified. In the recorded trial, the same mistake stopped the guest and consumed build 3. The refusal still uses one work action. Cancellation and deadline expiry still stop the job, and real guest failures during a tap keep the existing stop-and-repair path.
- **The model can read the screen as text.** Before this change, observations returned only the path of the view-tree XML, which the model cannot open. Its view of the screen depended on image delivery, which the trial showed arrived only as base64 inside JSON text. Observations now include `uiSummary`, and taps `afterUiSummary`. Each lists visible text and controls with bounds and actual-pixel tap centers. Parsing refuses XML declarations and bounds size, depth, nodes and listed elements; the raw XML is still retained. The tree is timed separately from its screenshot. That explains, rather than hides, the trial's splash-screenshot versus counter-tree disagreement.
- **Clearer envelopes.** The schema describes actual-pixel coordinates, and validation errors name the rejected field and the action's accepted fields. The trial's tap with unsupported parameter names returned only a generic message.

Known tradeoff: each tap now dumps the view tree after its final screenshot. With continuous animation, the dump can time out after 5 seconds and is recorded as unavailable. This adds latency to game taps but does not change the captured frames or stop the guest.

**Candidate 2: longer default and progress-gated extensions.** The operator reported that runs kept timing out after producing partial results. New jobs now default to 60 minutes; the initial selection range stays 5–60. A new `extend` action lets the agent request 5–30 more minutes with a reason. The controller grants it only after a new successful build since the previous grant, which is the progress event it can verify. Grants are limited to three, and initial plus extended time to 120 minutes. A running guest's deadline moves with the job. Extensions add no writes, builds or actions, cannot revive an expired, cancelled or ended job, and are recorded with their reasons. Repeated `prepare` still cannot change the deadline.

The controller cannot extend the chat. In the recorded trial, the 30-minute chat timeout ended the run about 2.5 minutes before the controller deadline. A longer job therefore helps only if the operator also dispatches the chat with a longer `chat.send.timeoutMs`: `3600000` for 60 minutes, or `7200000` to leave room for extensions.

Not addressed by candidates 1–2: native image delivery through the deferred-tool wrapper, showing captures in the user's chat, checkpoint readback discipline, and the larger build budget. Stage 4AQ addresses image delivery and the build budget. Each extension grant still needs its own new successful build.

## Stage 4AQ (deployed October 4, 2026)

A transcript analysis of the October 4 morning run ([TEST_RESULTS.md](TEST_RESULTS.md#transcript-analysis-of-the-october-4-run)) found that the chat timed out mainly because of screenshots. Behind Tool Search's `tool_call` bridge, each capture arrived as about 20,000 characters of base64 text that the model could not view and had to reprocess on every turn.

- **Direct-only tool.** `android_project` declares `catalogMode: "direct-only"`, the mechanism OpenClaw's own `view_image` tool uses for vision models. The skill calls the tool directly and falls back to `tool_call` only if it is missing from the direct list.
- **Larger budgets.** Jobs allow 20 writes, 10 builds and 120 actions (previously 8, 3 and 40), as named constants in `bridge.py`.
- **Review fixes.** A pre-deployment review found no blockers. Its minor findings were fixed: receipts stop offering extensions once the 120-minute total is used, a granted extension says it used one action, the per-guest artifact cap covers the full action budget, system dialogs are labelled as package `android`, and the tap validation error no longer quotes the 0–8192 transport bound.

The guarded updater verified the installed files were exactly stage 4AO final2, ran the staged and installed fixture suites, and replaced nine files with backups. A Gateway refresh loaded the plugin; configuration and retained jobs were unchanged.

## Read the actual outcome before choosing more work

[TEST_RESULTS.md](TEST_RESULTS.md#stage-4aq-trial-october-4-afternoon) records the first 4AQ trial. The counter app passed all three behavior checks in about 14 minutes, with the model's own stop, confirmed cleanup and checkpoint readback. That establishes one successful small task. It does not establish the bird game, long tasks, repeatability, or that captures are shown to the human. The model also overstated its evidence: it called the initial state "visually confirmed" from a launch frame that was the Android splash screen.

The original bird-game request remains a separate requirement: observed play, scoring, collision, game over and restart.

## Priorities for subsequent work

1. **Make the launch frame trustworthy.** Both trials captured the splash screen at launch while the UI tree, taken about two seconds later, showed the app. Have the adapter take a settled launch frame once the tree shows the app package (or retake after the dump), so frame and tree describe the same moment. In a direct probe the local model correctly identified the splash frame when asked, so this is a grounding problem, not a vision limit.
2. **Checkpoint discipline.** The 4AQ run skipped the exact-title read and readback of its initial checkpoint, read the previous trial's note instead, and wrote three false details into the final checkpoint (error count, crash cause, timestamps). Consider controller- or plugin-side help, such as a checkpoint template filled from receipts.
3. **Show captures to the human.** The model now receives native image blocks, but whether the normal web chat displays them was not checked (the trial session was created with `deliver: false`). Verify in the Control UI before building a caption feature.
4. **Bird-game trial.** The 4AQ budgets and native images make the original request feasible to retry; it will also exercise `observe`, tap bursts, `extend` and the out-of-display refusal, none of which the counter run used.
5. **Controller report and disk.** The job `report.md` still headlines the first compiler failure after a later successful build. With 10 builds, a job can keep up to 10 emulator disks; host free space is checked only at prepare.
6. **Portable setup.** The publication copy retains configuration placeholders, UID, model/image identity and baseline checks. Do not remove them to make another host pass; document an equivalent reviewed setup instead.

Choose work from the actual latest failures, and preserve unsuccessful runs and any operator assistance.

## Operator precautions for a future authorized run

Confirm the intended Studio, configured service-account identity, installed hashes, local model/provider, available bounded storage and idle owned resources. Check the complete session inventory before a Gateway refresh. Do not interrupt unrelated work, alter global routing or grant broader permissions to make a trial pass.

Start a fresh run only when authorized; never restart an ended job to reset its limits. Use the earlier of the user, chat and controller deadlines. After the run ends, verify the exact worker and guest are stopped and derived cache cleanup completed. If the operator has to issue stop, record that assistance. Keep source, APK identity and evidence even if the test failed.

The original `install.py` is historical, not the update procedure. The deployment-specific guarded updaters, receipts and backups remain in the private archive and on the Studio. A future source change requires a reviewed diff, relevant fixture tests, a new version record, and an idle, baseline-bound update procedure. No background schedule or continuation loop is part of this handoff.
