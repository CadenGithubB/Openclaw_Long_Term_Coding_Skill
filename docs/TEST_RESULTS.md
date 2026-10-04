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
