# OpenClaw Long-Term Coding Skill

A checkpoint workflow and restricted Android build/test integration for a local OpenClaw agent. The skill explains what the agent should do; the controller enforces the available operations and their limits.

This repository contains source, tests, and a handoff for continued work. The current source is **stage 4AS**, deployed on the Mac Studio on October 4, 2026. Compared with stage 4AO final2, it delivers screenshots to the model as images instead of base64 text, lists the screen's text and controls with actual-pixel tap centers, waits for the splash window to close before the launch frame, and shows each screenshot in the operator's Control UI chat. It also gives jobs 120 minutes by default, lets the model extend them after any new successful work, and lets a chat session continue with a new job once a job ends, within shared session limits. Trial results so far: the local model built and verified a small counter app, but did not complete the bird game; see [TEST_RESULTS.md](docs/TEST_RESULTS.md). It is a development snapshot of a specific Mac Studio installation, not a portable one-command installer. Read [the handoff](docs/HANDOFF.md), [observed test results](docs/TEST_RESULTS.md), and [next-test instructions](docs/NEXT_TEST.md) before continuing.

## Contents

- `android-chat-bridge/bridge.py`: owns Android jobs, the offline build worker, source revisions, budgets and cleanup.
- `android-chat-bridge/emulator_adapter.py`: installs and exercises APKs in a separate disposable Android guest.
- `android-chat-bridge/plugin/`: exposes the bounded `android_project` tool to the trusted OpenClaw main session.
- `android-chat-bridge/skill/long-task-runner/`: the skill, checkpoint helpers and supporting references.
- `android-chat-bridge/INTEGRATION.md`: workflow and enforcement boundaries.
- `android-chat-bridge/transfer-manifest.json`: SHA-256 fingerprints for the 45 published source files in this snapshot.

The current publication source-manifest fingerprint is `4eec9ce2d46b092ac93ed8273afee4a12f7d3052c9c752e1348457a10550fb03` (stage 4AS). The earlier stage 4AO final2 publication copy had `e088143b83c78e5e1c6844cd37d82893ebc91584b51e565dfd1974014896f835`. [Source versions](docs/SOURCE_VERSIONS.json) records every version with its changed files. Machine-specific paths and account names have been replaced with `/CONFIGURE/...` placeholders, and original volume identity values removed. This publication copy is intentionally not ready to deploy. [Source provenance](docs/SOURCE_PROVENANCE.json) records how the publication copy was derived from the installed stage 4AO final2 source; the installed fingerprint of each deployed stage is in the source versions record. The repository documentation is additional to that source snapshot. Private trial histories, notes, machine configuration, installation backups, screenshots, generated APKs and emulator disks are not included.

## Workflow

Read the skill, preserve the complete requested outcome, prepare one job, and save/read back its checkpoint. Write a compact first source revision and build it early. Use compiler errors to repair within the same job, then build again before calling a repair verified. Install the qualified APK, observe the actual screen, exercise the requested behavior, and retain the results. Finish with a verified stop and final checkpoint readback.

Every work action reuses the `jobId` returned by `prepare`. Source inputs are complete Java classes under the fixed `org.openclaw.trial` scaffold. Build the UI in Java using the prepared Android framework APIs; custom XML resources, AppCompat and new external dependencies are not supported by this route.

`android_project` is a direct tool: call it by name, not through Tool Search's `tool_call`, so screenshots reach the model as images. Tap coordinates are actual pixels of the display reported by `start_test`. Each observation also lists the visible text and controls with their tap centers (`uiSummary`). An out-of-display tap is refused without stopping the guest or requiring a rebuild. For real-time games, play inside one `tap` burst (`count` up to 30, `intervalMs` 80–1500); a burst of 4 or more returns frames from during the burst. Each screenshot is also shown to the operator in the Control UI chat under the tool call that took it.

## Time budgets

`prepare` accepts `timeLimitMinutes` from 5 to 240, and jobs default to 120 minutes. That selection fixes the job's initial deadline; repeated preparation cannot extend an active job or reset its counters.

The agent can call `extend` to add 5–30 minutes, with a reason, whenever new successful work (a source write, build, launch, tap or observation) has happened since the previous grant. A job allows up to 8 grants and 240 minutes in total, including the initial limit. Receipts warn once less than 15 minutes remain; the model's turns can take several minutes, so it should ask early. Extensions never add writes, builds or chat time; each request uses one work action.

After a job ends with confirmed cleanup (the model's own stop, an expiry or a failure), a new `prepare` starts a continuation job. A chat session's jobs share 20 source writes, 10 builds, 120 actions and 240 controller minutes, so a continuation receives only what is left. A cancelled run, uncertain cleanup or an exhausted session refuses continuation.

The OpenClaw chat has its own time limit, set per dispatch with `chat.send.timeoutMs`. Set it longer than the work you expect, for example `16200000` (4.5 hours) to cover a full 240-minute session. Changing the job duration does not extend a chat already running or change OpenClaw's global default.

Individual calls retain their shorter limits: build and guest boot are each capped at 180 seconds, with transport bounds of 410–420 seconds. More wall-clock time does not add build attempts or relax isolation.

## Run fixture tests locally

These tests use controlled fixtures and mocked external operations. They do not launch generated app code, a real build worker or a live model.

```sh
python3 -B -m unittest discover -s android-chat-bridge -p 'test_*.py' -v
node --test android-chat-bridge/plugin/test_plugin.mjs
```

The deployed stage 4AO final2 suites passed 198 Python and 44 Node tests on the Studio; detailed scope and the fresh model trial are in [TEST_RESULTS.md](docs/TEST_RESULTS.md). Stage 4AS passes 250 Python tests (Python 3.9 and 3.14) and 54 Node tests, both locally and in the staged and installed suites on the Studio. Passing fixtures does not establish autonomous app completion.

## Deployment prerequisites and limits

The existing installation uses local Ollama/Qwen, a pinned offline Linux Android build image, a bounded project volume, and an official Android SDK/emulator prepared separately. Those tools and licensed SDK files are not distributed here. Paths, account identity, model/image fingerprints and baseline checks are specific to that installation and must be reviewed before any port.

**Do not run `android-chat-bridge/install.py` as a setup or update command.** It is the retained original first-install script with historical baseline checks, not a general installer for this snapshot. Installing the skill text alone also does not install its controller, plugin, SDK or offline image. The [handoff](docs/HANDOFF.md) explains the current installation state and what a portable installer would still need.

The worker is non-root, offline, has a read-only root filesystem, drops capabilities, and receives one project mount without the Docker socket. Generated Java runs only in the worker and Android guest. Existing host OpenClaw/model services remain separate trust surfaces. The recorded tests establish their exercised checks, not a comprehensive security guarantee.
