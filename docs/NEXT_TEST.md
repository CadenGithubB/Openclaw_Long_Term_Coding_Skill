# Instructions for the next authorized Android trial

This is a proposed next-run procedure informed by the recorded counter failures. It was not injected into the completed or currently recorded diagnostic, and it does not establish that the underlying issues are fixed.

## Prepare the run

Check the current deployed version and idle owned resources. Use one fresh local-model chat, request High thinking before generation if supported, and explicitly set its time budget. For a 30-minute attempt, use `chat.send.timeoutMs = 1800000` and select `timeLimitMinutes: 30` on the first Android `prepare`. Leave time before the earlier deadline for stop and checkpoint readback.

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

The next controller investigation should distinguish an invalid tap rejected before input delivery from an actual guest failure. The recorded controller currently stops the guest after an out-of-display tap and requires a source write plus rebuild before testing can resume. Any change to that behavior needs focused regression coverage and a new deployment review; these instructions do not change it.
