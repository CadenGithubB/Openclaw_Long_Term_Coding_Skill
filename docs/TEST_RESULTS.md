# Observed test results

The October 4, 2026 counter diagnostic did not complete its acceptance contract. More time allowed a compiler repair, two successful APK builds and two emulator launches, but the agent did not deliver a successful tap or finish checkpoint verification. This is evidence of partial progress, not autonomous app completion.

## Scope and conditions

One fresh chat ran the installed stage 4AO final2 source with local `ollama/qwen3.6:35b`, High thinking requested before generation, a 30-minute chat timeout and a separately selected 30-minute Android job deadline. The existing limits remained 3 builds, 8 source writes and 40 work actions. The operator inspected progress but supplied no Java, corrective prompt or mid-run repair. Installed source and configuration fingerprints were unchanged afterward.

The task required an original counter app, an actual APK, observed 0 → 1 → 0 behavior using Increment and Reset, checkpoint save/readback, and a model-issued stop with confirmed cleanup. The original bird-game requirement was not exercised by this diagnostic.

## What happened

| Requirement | Observed result |
| --- | --- |
| Early build | The first build began about 48 seconds after preparation completed, or 3 minutes 28 seconds after the chat started. |
| Follow the available framework | The agent initially attempted XML resources and AppCompat. XML input was rejected, and its Java-only retry still used unavailable resources and AppCompat. The first build failed with eight compiler errors. |
| Repair and rebuild | The agent rewrote the app using native Java views. Build 2 succeeded after about 5 minutes 28 seconds of chat time. Build 3 also succeeded after about 14 minutes 47 seconds. |
| Observe the initial state | Both launch PNGs showed Android splash. Subsequent observation PNGs showed the counter at 0 with Increment and Reset. All four later-captured UI trees described the counter controls. The launch screenshot and UI tree did not represent the same instant. |
| Increment and Reset | Neither check completed. One tap used unsupported parameter names. A later tap used coordinates outside the 480×800 display and was rejected before input delivery. No successful tap was recorded. |
| Recover from rejected input | The controller stopped the guest after the out-of-display tap and required a source write plus rebuild before another launch. This consumed build 3. The agent briefly misunderstood the qualified APK status, then corrected itself after inspecting status and relaunched successfully. |
| Checkpoints | The exact-title initial read returned not found, and one write was acknowledged. There was no readback after the write and no final checkpoint. |
| Finish within the chat | The chat timed out after 30 minutes. The last completed model tool action was another observation at about 25 minutes 21 seconds. |
| Cleanup | The model issued no stop. The controller's independent deadline expired about 2 minutes 28 seconds after chat timeout and completed cleanup about 4 seconds later. Subsequent inspection confirmed no owned running worker, no private guest listeners and no derived runtime cache. No operator stop was required. |

The final controller counters were 3 builds, 3 accepted writes and 13 work actions. The chat ended on time, not on a fourth-build refusal. Increasing the build allowance alone would not correct the observed image interpretation, tool arguments, checkpoint omissions or delay between actions.

## Image delivery and human visibility

The plugin returns native image blocks, but all 16 recorded outer deferred-tool results were text blocks. Four images appeared nested inside JSON text. This transport record does not establish that the model received native image input or that the user saw inline images in the web chat. The four saved PNGs were independently viewed after the run.

The requested follow-up is to show the user the same captures with short human captions, while separately verifying supported model image input. [NEXT_TEST.md](NEXT_TEST.md) defines the acceptance checks. That display feature has not been implemented or demonstrated by this trial.

## Fixture validation and publication

The installed source previously passed 198 Python tests and 44 plugin tests on the Studio. The publication copy also passed 198 Python tests and 44 Node tests after replacing machine-specific paths and identity values. An initial publication-only Node fixture failed because the platform temporary directory resolved through a symlink; the fixture now canonicalizes its temporary root, and all 44 tests passed. These fixture results do not establish actual model behavior or chat image rendering.

The published source differs from the installation only as recorded in [SOURCE_PROVENANCE.json](SOURCE_PROVENANCE.json). No live update was performed during this trial or publication. Raw histories, notes, machine paths, screenshots, APKs and resource-check receipts remain in the private archive; this repository contains reviewed summaries.

## Implications for the next change

Prioritize native image transport and visible chat captures, actual-pixel input guidance, and recovery from an input validation rejection without an unnecessary rebuild. Complete checkpoint readbacks and finalization before the earlier deadline. A configurable initial build allowance of 10 is proposed, with compatible write/action budgets and the existing deadline and isolation controls retained; it is not implemented. None of these proposed changes should be recorded as fixes already deployed.

## Follow-up source candidate (fixture results only)

Stage 4AP candidate 1 implements two of these items in source. Out-of-display taps are refused without stopping the guest or requiring a rebuild. The coordinate guidance now says actual pixels, and observations include a text summary of the screen's visible text and controls with tap centers. It passed 212 Python fixture tests on Python 3.9.23 through 3.13.14, and 46 Node fixture tests, in a Linux cloud container. The new tap regression test was also run against the stage 4AO final2 controller, where it fails as expected. No Studio, model, build worker, emulator or chat was used. The candidate is not deployed, and this trial's recorded results above are unchanged. Native image transport, chat captures, checkpoint readback and the build allowance remain open.

Candidate 2 raises the default job time to 60 minutes and adds a progress-gated `extend` action, with a 120-minute total cap. It passed 227 Python fixture tests on the same Python versions, and 47 Node tests. It does not change the chat timeout, which ended the recorded trial first. Neither candidate has been exercised by a live model.

## Transcript analysis of the October 4 run

A later session read the run's private chat history, controller receipts and saved captures. It explains where the 30 minutes went and why the agent never delivered a valid tap.

**Most of the time was the model rereading its own context.** Controller calls (prepare, writes, three builds, two guest launches, observations) took about 4.3 minutes in total. The other 25.7 minutes were model turns. Turns took about 30 seconds at 20,000 context tokens, but about 185 seconds at 65,000 tokens and more than 280 seconds at about 110,000 tokens. The last turn never finished before the chat timeout.

**Screenshots were the main cause of that growth.** The run reached `android_project` through Tool Search's `tool_call` bridge. The bridge serialized the plugin's image blocks into JSON text, so each 15–16 KB PNG became about 20,000 characters of base64 inside the model's context. The four captures added roughly 65,000 tokens. The model could not view any of them, yet it had to reprocess them on every later turn. The local model does support images (`ollama show` lists vision, and the OpenClaw configuration declares image input).

**The agent reported checks it could not have observed.** After the first launch it wrote “CHECK_1 confirmed: the emulator screenshot shows "0" displayed with "Increment" and "Reset" buttons”. That launch capture was Android's splash screen. Before its out-of-display tap, it said the button position came “from the UI hierarchy”, but it had only received the hierarchy's file path. It then converted its guess into a 0–8192 range taken from the schema's upper bound and tapped (3840, 982) on a 480×800 screen. The real Increment button was centered at (240, 537); the app itself was working.

**Thinking level could not be confirmed.** The session reported `thinkingLevel: high`, but every stored thinking block was empty and each turn produced only 61–967 output tokens. This record cannot show whether extended reasoning took effect.

Stage 4AP candidate 1 addresses the coordinate guidance and adds a text `uiSummary`. The stage 4AQ candidate addresses image delivery: `android_project` is declared direct-only, which OpenClaw's own `view_image` tool also uses for vision models. OpenClaw's Ollama route then attaches tool-result images natively. Both were deployed and tried the same afternoon; see below.

## Stage 4AQ trial (October 4, afternoon)

After stage 4AQ was deployed, one fresh chat ran the same counter request. The only changes to the request were the 60-minute controller default, a note that one progress-gated extension was available, a two-hour chat timeout and a new checkpoint title. High thinking was requested. The operator supplied no code, prompt or repair during the run. Afterwards the evidence was audited independently: five auditors, two skeptics per conclusion, and a completeness critic.

**The app passed all three behavior checks, in about 14 minutes.** Under the old setup, the chat had timed out at 30 minutes without one valid tap.

| Requirement | Observed result |
| --- | --- |
| Early build | The first build began 80 seconds after `prepare` was called (2 m 40 s into the chat) and failed with 12 compiler errors. |
| Repair and rebuild | The model repaired the source in the same job. One write was rejected for a malformed `files` entry and resent unchanged in content. Build 2 succeeded at 7 m 44 s. |
| Initial 0 | Proven by the launch UI tree and by the frame captured just before the first tap. The launch screenshot itself was the Android splash screen, as in the morning run. |
| Increment → 1 | Proven. The tap at (240, 239) landed inside the INCREMENT bounds; the after-frame and UI tree both show 1. |
| Reset → 0 | Proven. The tap at (240, 287) landed inside the RESET bounds; the after-frame and UI tree both show 0. |
| Budgets used | 2 writes, 2 builds and 8 actions; no extension and no refused tap. |
| Stop and cleanup | The model stopped the job at 10 m 32 s. The receipt confirmed cleanup, and an operator check on the Studio afterwards found no emulator, worker, controller socket, private listener or runtime cache. Configuration was unchanged. |
| Checkpoint | The final checkpoint was written and read back under the exact title. The initial checkpoint was neither read before writing nor read back. |

**Image delivery.** All ten `android_project` calls were direct; `tool_call` was used only for notes. Screenshots reached the history as five native image blocks with no base64 text. Context grew about 3,000–3,200 tokens per capture, compared with 15,600–17,600 before. Peak context was 54,000 tokens instead of more than 94,000. Model turns had a median of about 30 seconds. The history omits image bytes, so it cannot prove the model perceived the pixels; the token growth is consistent with roughly 375 tokens per image. In a separate direct probe, the same model shown only the launch frame called it "a loading or splash screen", and correctly read 1 in the counter frame.

**What the model got wrong.** The correct taps came from the text `uiSummary` centers; nothing the model said required seeing an image. It described the splash launch frame as showing the counter and called the initial state "visually confirmed by the screenshot". It did not observe again, although the skill says to when frame and tree may disagree. Its final checkpoint repeats that claim. It also reports 8 compiler errors instead of 12, and invents a cause for a crash marker, which was a Bluetooth service abort, not the app. Other deviations:
- It tried to save the checkpoint with the generic workspace `write` tool, which the sandbox refused.
- It called `sessions_yield` with no subagent.
- It read the previous trial's note instead of reading its own title first.
- It never checked its thinking mode.
- Its thinking once refers to an empty user message that does not exist.

Thinking blocks were recorded this time (18 non-empty blocks), unlike the morning run.

**What this does not establish.** It is one run of a small task. It did not use `observe`, tap bursts, `extend`, the out-of-display refusal or most of the raised budgets. It does not show whether the normal web chat displays the captures to the human (the session was created with `deliver: false`). It does not cover the bird-game requirements. The job `report.md` also still headlines the first compiler failure after the later successful build.


## Stage 4AR bird-game trial (October 4, evening)

After stage 4AR was deployed, one fresh chat ran the original Flappy-Bird-style request. It used a 60-minute controller job, a progress-gated extension, a two-hour chat timeout and High thinking. The operator supplied no code or repair. **The game requirements were not met.** The run did show what the next changes need to address.

| Requirement | Observed result |
| --- | --- |
| Launch frame | The new settle step saw the splash window close after about one second. All three launch frames show the game, not the Android splash. |
| Builds and repairs | Revision 1 failed with 12 compiler errors. Revision 3 built (revision 2 was overwritten without a build, against the skill). Revisions 4 and 5 added a game loop and a sizing guard. 5 writes, 4 builds and 19 actions were used of 20, 10 and 120. |
| Tap to flap | Revision 3 ignored taps (no game loop). In revisions 4 and 5 the frame just after the tap shows play: bright sky, bird mid-screen, start text gone. |
| Motion, pipes, score, collisions, game over, restart | NOT established. Each later observation, taken 30 or more seconds after the single tap, shows the bird on the ground with a small red "Game Over" and "Score: 0". |
| Extension | Requested about a minute after the 60-minute deadline had passed, so it was refused. The controller then expired the job and confirmed cleanup. |
| After expiry | The model tried to prepare a new job three times. The one-job-per-session rule refused each try, and the session ended with status `killed` at about 79 minutes. |

**The model misread its own evidence.** The game appears to work as written: a bird that gets one flap falls to the ground within a second or two. The model observed the board half a minute later and concluded that the tap "isn't transitioning to PLAYING". It spent its last revision on a guard that was not the problem. It never used a tap burst (`count` with `intervalMs`), which keeps playing inside one action and returns frames from during the burst.

**Vision did work.** The game draws on a single surface, so every UI tree was empty. The model's descriptions came from the images: "nearly identical" before and after frames for revision 3, the bird's movement, and the small "Game Over" and "Score: 0" text, which is really on screen. Its claim that a launch frame showed a "bobbing" bird could not come from one frame.

**Turns slowed sharply as the context grew.** Turns took about 30 seconds early on. They took 4–7 minutes at 76,000–89,000 context tokens, including turns that produced only a hundred or so output tokens. That suggests the prompt was being reprocessed in full rather than reused from Ollama's cache, but the cause has not been investigated. The 60-minute job allowed only about a dozen turns at that pace.

**The human saw no screenshots.** In the Control UI, every tool result showed its JSON receipt but no image. `chat.history` drops tool-result image data, and OpenClaw only accepts http(s) media URLs from plugin tools.
