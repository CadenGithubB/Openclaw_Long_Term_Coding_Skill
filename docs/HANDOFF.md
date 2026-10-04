# Handoff for the next coding session

## Start from the deployed source

The deployed implementation is **stage 4AS**, installed on the existing Mac Studio on the evening of October 4, 2026. It is the stage 4AO final2 source plus the stage 4AP candidates 1–2 and the 4AQ, 4AR and 4AS changes described below. `android-chat-bridge/` holds its publication copy. Public files replace service-account paths with `/CONFIGURE/...` placeholders and remove original volume identity values; [SOURCE_VERSIONS.json](SOURCE_VERSIONS.json) records both the publication and the installed manifest fingerprints. The source manifest binds 45 files; the README records its fingerprint. The controller, plugin, emulator adapter and skill belong together. An older standalone skill copy does not include the complete Android route.

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

## Stage 4AR (deployed October 4, 2026)

- `start_test` waits up to 10 seconds for the app's splash window (`Splash Screen <package>` in `dumpsys window windows`) to close before the launch frame, and records `launchSettle`. In the bird trial it worked every time: about one second of waiting, and the launch frames show the game.
- The job report no longer headlines an earlier compiler failure once a later build succeeded.
- The skill asks the model to cite captures only for what they show and to copy counts and causes from receipts.

## Stage 4AS (deployed October 4, 2026)

Requested after the bird trial: more time, no dead end after a job ends, and screenshots visible to the human.

- **Time.** Jobs default to 120 minutes (5–240). `extend` is granted after any new successful work since the previous grant, up to 8 grants and 240 minutes in total. Receipts warn under 15 minutes only when an extension is currently grantable.
- **Continuation.** After a job ends with confirmed cleanup, `prepare` starts a continuation job. The session's jobs share 20 writes, 10 builds, 120 actions and 240 minutes, so limits are never reset. Only the plugin's cleanup after an interrupted call (`:abort`) cancels a session. A late work call on an ended job is answered from the record instead of failing, because the plugin would otherwise turn the failure into a cancellation.
- **Screenshots for the human.** Each capture is saved once into OpenClaw's inbound media store through `runtime.channel.media.saveMediaBuffer`. Its image block gains a `media://inbound/...` url and a caption, which the Control UI loads through its authenticated media route. Plugin tools cannot use `details.media` for this: OpenClaw keeps only http(s) media URLs from non-core tools, and `chat.history` drops image data. The model still receives the image data.
- **Games.** The skill tells the model to exercise real-time games inside one tap burst rather than a single tap followed by a later observation.

Two pre-deployment reviews ran with adversarial verification. They found one major defect in the first draft, the late-call cancellation described above, which was fixed with tests. The first installation attempt was refused by the updater's own staged tests. Live confirmation of the screenshot display is pending.

## Read the actual outcome before choosing more work

[TEST_RESULTS.md](TEST_RESULTS.md#stage-4aq-trial-october-4-afternoon) records the first 4AQ trial. The counter app passed all three behavior checks in about 14 minutes, with the model's own stop, confirmed cleanup and checkpoint readback. That establishes one successful small task. It does not establish the bird game, long tasks, repeatability, or that captures are shown to the human. The model also overstated its evidence: it called the initial state "visually confirmed" from a launch frame that was the Android splash screen.

The original bird-game request remains a separate requirement: observed play, scoring, collision, game over and restart.

## Priorities for subsequent work

1. **Turn latency as context grows.** In the bird trial, turns slowed from about 30 seconds to 4–7 minutes at 76,000–89,000 tokens. That looks like the prompt being reprocessed instead of reused from Ollama's cache. Find what changes the prompt prefix between turns (for example history image pruning) and fix it without changing global routing.
2. **Confirm the screenshot display live**, including after a page reload. Check the stored image blocks for the `media://inbound` url.
3. **Gameplay testing method.** Check that the model now uses tap bursts and judges motion, scoring and collisions from the burst frames.
4. **Checkpoint discipline.** Read and read back the initial checkpoint, and copy facts from receipts. Both trials skipped part of this.
5. **Bird-game acceptance.** Observed play, scoring, collision, game over and restart on the qualified APK, with confirmed cleanup.
6. **Housekeeping.** With longer sessions, a job can keep several emulator disks, and screenshot copies accumulate in OpenClaw's inbound media store. Host free space is checked only at prepare.
7. **Portable setup.** Keep the configuration placeholders and baseline checks; document an equivalent reviewed setup rather than removing them.

Choose work from the actual latest failures, and preserve unsuccessful runs and any operator assistance.

## Operator precautions for a future authorized run

Confirm the intended Studio, configured service-account identity, installed hashes, local model/provider, available bounded storage and idle owned resources. Check the complete session inventory before a Gateway refresh. Do not interrupt unrelated work, alter global routing or grant broader permissions to make a trial pass.

Start a fresh run only when authorized; never restart an ended job to reset its limits. Use the earlier of the user, chat and controller deadlines. After the run ends, verify the exact worker and guest are stopped and derived cache cleanup completed. If the operator has to issue stop, record that assistance. Keep source, APK identity and evidence even if the test failed.

The original `install.py` is historical, not the update procedure. The deployment-specific guarded updaters, receipts and backups remain in the private archive and on the Studio. A future source change requires a reviewed diff, relevant fixture tests, a new version record, and an idle, baseline-bound update procedure. No background schedule or continuation loop is part of this handoff.
