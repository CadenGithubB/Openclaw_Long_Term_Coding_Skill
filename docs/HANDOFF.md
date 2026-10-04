# Handoff for the next coding session

## Start from the deployed source

The deployed implementation is the stage 4AO final2 snapshot; `android-chat-bridge/` now holds its publication copy plus the undeployed stage 4AP candidate changes described below. The original was installed on the existing Mac Studio on October 4, 2026. Public files replace service-account paths with `/CONFIGURE/...` placeholders and remove original volume identity values; the live installation is unchanged. The source manifest binds 45 files; the README records its fingerprint. The controller, plugin, emulator adapter and skill belong together. An older standalone skill copy does not include the complete Android route.

The live installation is operated through Apple Remote Desktop's UNIX command dialog using the configured service account. The local repository is a source and documentation checkout, not the live host. The original detailed evidence remains in the operator's private project archive. Do not infer a new live success from a saved transcript or fixture test.

This GitHub repository was empty when inspected for this publication. The initial commit contains the current source and reviewed summaries, not the private operational archive or the Android SDK/image binaries.

## What changed before the latest test

Receipts now distinguish the current source revision from the revision with a qualified APK, display remaining work/time limits, and explain the result in plain language. Failed builds report the recovered controller status. The plugin conservatively shortens recognized compiler output while preserving error context and the original controller logs.

The Android skill spells out checkpoint save/readback, an early build, a rebuild after each repair, actual behavior checks, and stop/final-checkpoint steps. It names the notes tools and requires the same job ID for every emulator action. These instructions improve the workflow contract but do not force the model to comply.

New jobs default to 30 minutes and can initially select 5–60 minutes. The selection cannot change an existing deadline. Fresh chats need their own explicit timeout; selecting a longer Android duration does not extend a running chat. Source writes, build attempts, work actions and isolation limits are unchanged.

## Changes after the latest test (stage 4AP candidate 1, not deployed)

The repository source now carries an undeployed candidate aimed at two recorded counter-trial failures. [SOURCE_VERSIONS.json](SOURCE_VERSIONS.json) lists its manifest fingerprint and changed files. It passed fixture tests only, in a Linux cloud container without access to the Studio. No live model, build worker, emulator or chat ran against it. The Studio still runs stage 4AO final2.

- **Out-of-display taps no longer cost a rebuild.** The controller records the display size measured by the adapter at `start_test`. A tap outside it is refused before the adapter is called, with `inputDelivered: false` and the allowed range. The guest keeps running and the APK stays qualified. In the recorded trial, the same mistake stopped the guest and consumed build 3. The refusal still uses one work action. Cancellation and deadline expiry still stop the job, and real guest failures during a tap keep the existing stop-and-repair path.
- **The model can read the screen as text.** Before this change, observations returned only the path of the view-tree XML, which the model cannot open. Its view of the screen depended on image delivery, which the trial showed arrived only as base64 inside JSON text. Observations now include `uiSummary`, and taps `afterUiSummary`. Each lists visible text and controls with bounds and actual-pixel tap centers. Parsing refuses XML declarations and bounds size, depth, nodes and listed elements; the raw XML is still retained. The tree is timed separately from its screenshot. That explains, rather than hides, the trial's splash-screenshot versus counter-tree disagreement.
- **Clearer envelopes.** The schema describes actual-pixel coordinates, and validation errors name the rejected field and the action's accepted fields. The trial's tap with unsupported parameter names returned only a generic message.

Known tradeoff: each tap now dumps the view tree after its final screenshot. With continuous animation, the dump can time out after 5 seconds and is recorded as unavailable. This adds latency to game taps but does not change the captured frames or stop the guest.

Not addressed: native image delivery through the deferred-tool wrapper, showing captures in the user's chat, checkpoint readback discipline, and the proposed larger build budget. Deploying the candidate needs a reviewed diff applied to the installed source through the guarded private updater, with fresh identity, baseline and idle checks. Then a fresh authorized trial is needed before any claim of improved behavior.

## Read the actual outcome before choosing more work

[TEST_RESULTS.md](TEST_RESULTS.md) records the latest counter diagnostic and distinguishes compilation, repair, app behavior, checkpoint handling and cleanup. The earlier eight-minute test was too short to assess the model's overall ability: it built an APK but timed out without completing interactions or cleanup. Its screenshot also contradicted its claim that the controls were visible.

The original bird-game request remains a separate requirement. A successful counter run would not establish scoring, collision detection, game-over/restart behavior or long unattended coding reliability. High thinking was requested on the local model; no controlled accuracy improvement has been demonstrated.

For a concrete next-run checklist, use [NEXT_TEST.md](NEXT_TEST.md). Its guidance was not inserted into the recorded diagnostic.

## Priorities for subsequent work

The three-build cap came from the earlier diagnostic setup, not a model or container requirement. The latest discussion identified it as too restrictive for a 30–60-minute local-model task. A proposed follow-up is a configurable initial build budget of 10, still bounded by the absolute job deadline and existing isolation/resource controls. This is a recommendation, not an implemented or deployed change. Review its interaction with the write/action budgets, and never extend an existing job to obtain more attempts.

1. Make emulator captures useful to both the model and the human. Confirm native image delivery through the deferred tool wrapper, and render each captured image inline in the originating user chat with a short plain-language caption. A base64 string in a text result establishes neither model image input nor a visible chat attachment. Show what was captured, why it was captured, and what it actually proves; label splash/loading screens honestly. The latest run's launch PNGs showed splash while their later UI trees described the counter, so preserve each capture's timing and avoid presenting them as simultaneous. This is a requested follow-up, not an implemented display feature. See the concrete acceptance checks in NEXT_TEST.md. The stage 4AP candidate's text `uiSummary` gives the model a screen description that does not depend on image delivery. It does not solve native image input or chat display.
2. Make checkpoint evidence complete: exact-title read, acknowledged write, readback, and a final outcome that matches the build/test/cleanup receipts. Do not count an acknowledged write alone as verified checkpoint recovery.
3. Keep the full app acceptance contract. For the bird game, require observed play, scoring, collision, game over and restart on the qualified APK, with the same source revision and confirmed cleanup.
4. Design a portable configuration and installer before advertising general setup. The publication copy retains explicit configuration placeholders, UID, model/image identity and baseline checks; it assumes separately prepared offline tools and a bounded volume. Do not remove those checks to make another host pass. Document an equivalent reviewed setup instead.

Choose work from the actual latest failures, rather than automatically repeating every historical diagnostic. Preserve unsuccessful runs and distinguish operator assistance from autonomous completion.

## Operator precautions for a future authorized run

Confirm the intended Studio, configured service-account identity, installed hashes, local model/provider, available bounded storage and idle owned resources. Check the complete session inventory before a Gateway refresh. Do not interrupt unrelated work, alter global routing or grant broader permissions to make a trial pass.

Start a fresh run only when authorized; never restart an ended job to reset its limits. Use the earlier of the user, chat and controller deadlines. After the run ends, verify the exact worker and guest are stopped and derived cache cleanup completed. If the operator has to issue stop, record that assistance. Keep source, APK identity and evidence even if the test failed.

The original `install.py` is historical, not the update procedure. The deployment-specific guarded updater and backups remain in the private archive. A future source change requires a reviewed diff, relevant fixture tests, a new version record, and an idle, baseline-bound update procedure. No background schedule or continuation loop is part of this handoff.
