# Instructions for the next authorized Android trial

This is the procedure for the next authorized trial, informed by the recorded counter runs. Stage 4AQ is deployed; its first counter trial passed, with the evidence problems listed in [TEST_RESULTS.md](TEST_RESULTS.md#stage-4aq-trial-october-4-afternoon). The next valuable trial is the original bird game, which exercises observation, tap bursts, longer work and possibly `extend`.

## Prepare the run

Check the current deployed version and idle owned resources. Use one fresh local-model chat, request High thinking before generation if supported, and explicitly set its time budget. Stage 4AQ defaults new jobs to 60 minutes. Dispatch the chat with `chat.send.timeoutMs = 7200000` so the progress-gated `extend` can actually be used. Record each extension request, whether it was granted, and its stated reason. Leave time before the earlier deadline for stop and checkpoint readback.

Use a new exact checkpoint title for the test, read it before writing, then verify each saved checkpoint with a readback. Do not reuse an ended job or reset its counters. Keep the full acceptance contract even if implementation is split into smaller milestones.

## App acceptance contract

Create an original Android counter that displays 0, changes to 1 after one Increment tap, and returns to 0 after one Reset tap. Build the actual APK, install that exact qualified artifact, and verify each state in the guest. Use native Java framework views; this route does not provide AppCompat, custom XML layouts or new dependencies.

Read the tool schema and retain the job ID for every work action. Reach a compact first build early, and rebuild after each source repair before calling it fixed. Report unperformed checks as NOT RUN.

## Observe before claiming success

A launch receipt or Android splash frame does not prove the counter screen was rendered. Take another observation when necessary. Cross-check visible text and the UI hierarchy against the requested state; do not invent controls or count values from the intended source.

Tap coordinates are actual pixels of the observed display. For a reported width and height, use `0 <= x < width` and `0 <= y < height`. The schema's broad upper bound is not a normalized coordinate system: do not scale a 480×800 screen into 0–8192. Use the center of the observed button bounds, retain the same job ID, and inspect the resulting state after each tap.

If the wrapper exposes an image only as base64 inside a text result, record the image-delivery uncertainty. Do not claim native visual verification merely because image bytes exist in a saved record.

## Show the human the same captures

The requested presentation feature is not implemented or verified yet. For each emulator screenshot returned to the agent, display that same captured image inline in the originating user chat, with one to three plain-language sentences explaining the action, the observed result and any uncertainty. For example: “The app is showing 0 with Increment and Reset buttons. I captured this before interacting so we have a starting state.” A loading frame should instead be described as loading, without claiming that controls are already visible.

Preserve chronological order and associate each image with its action and source/APK revision. Keep technical paths and image encoding out of the human caption. If several captures accompany one action, a labeled gallery is acceptable; do not substitute a newer screenshot for the one the agent inspected. Retain the full existing capture privately, without publishing it to the source repository or forwarding it to another chat.

Verify both delivery paths end to end: the model receives supported native image input, and the user sees the same capture in the normal web chat after reload. Check launch, observe, tap and multiple-image results. Check that a failed capture is explained without showing an old image as new evidence. A plugin fixture that returns an image block alone does not satisfy these checks. Screenshot and UI-tree capture happen separately; record their timing and obtain another observation if they disagree.

## Finish and assess

Stop the job, inspect its cleanup receipt, and write/read back the final checkpoint with the actual results. Preserve failed attempts. Separate build, app behavior, checkpoint and cleanup outcomes, and describe operator assistance explicitly.

Record which version the trial ran. Stage 4AQ checks, with their status after the first 4AQ counter trial:

- **Observed:** `tools.effective` lists `android_project` after the Gateway refresh, and the model called it directly rather than through `tool_call`.
- **Observed:** `start_test` and `tap` results carry native image blocks with no base64 text. Context grew about 3,000 tokens per capture, mostly JSON receipt text; the image itself is roughly 375 tokens.
- **Observed:** launch receipts include `uiSummary` with the counter text and both buttons, and tap centers inside the display. Each tap's `afterUiSummary` showed the new count, matching its after-frame.
- **Failed:** the model's description of each screen matches the saved capture. It described the splash launch frame as showing the counter and did not observe again.
- **NOT RUN:** an out-of-display tap reports `inputDelivered: false` and the next in-range tap works on the same guest without a write or build.
- **NOT RUN:** `extend` is requested with a reason after a new successful build, and granted or refused as documented.
- **NOT RUN:** the normal web chat shows the captured images to the human after a reload.

Stage 4AS checks, to report as observed or NOT RUN after its first trial:

- Expanding an `android_project` tool output in the Control UI shows the screenshot itself, before and after a page reload. In `chat.history`, the image block carries a `media://inbound/androidshot-...` url and no data.
- The model still describes the screens correctly from the images.
- For the game, the model plays inside one `tap` burst and judges motion, pipes, score and collisions from the burst frames.
- The model requests `extend` before the deadline and after new work, and the grant is recorded with its reason.
- If a job ends with work remaining, `prepare` starts a continuation job whose limits are the session remainder. If the session's limits are used, the refusal says so and the model reports instead of retrying.

