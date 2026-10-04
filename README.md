# OpenClaw Long-Term Coding Skill

A checkpoint workflow and restricted Android build/test integration for a local OpenClaw agent. The skill explains what the agent should do; the controller enforces the available operations and their limits.

This repository contains source, tests, and a handoff for continued work. The current source is **stage 4AQ**, deployed on the Mac Studio on October 4, 2026. Compared with stage 4AO final2, it fixes the morning trial's tap and observation failures, raises the default job time to 60 minutes, and adds progress-gated time extensions. It also delivers screenshots to the model as images instead of base64 text, and raises the per-job budgets. In its first live trial, the local model built and verified a small counter app in about 14 minutes, though it overstated part of its evidence; see [TEST_RESULTS.md](docs/TEST_RESULTS.md). It is a development snapshot of a specific Mac Studio installation, not a portable one-command installer. Read [the handoff](docs/HANDOFF.md), [observed test results](docs/TEST_RESULTS.md), and [next-test instructions](docs/NEXT_TEST.md) before continuing.

## Contents

- `android-chat-bridge/bridge.py`: owns Android jobs, the offline build worker, source revisions, budgets and cleanup.
- `android-chat-bridge/emulator_adapter.py`: installs and exercises APKs in a separate disposable Android guest.
- `android-chat-bridge/plugin/`: exposes the bounded `android_project` tool to the trusted OpenClaw main session.
- `android-chat-bridge/skill/long-task-runner/`: the skill, checkpoint helpers and supporting references.
- `android-chat-bridge/INTEGRATION.md`: workflow and enforcement boundaries.
- `android-chat-bridge/transfer-manifest.json`: SHA-256 fingerprints for the 45 published source files in this snapshot.

The current publication source-manifest fingerprint is `8c65087d0443d5f629481c298744dc4a424b91ed64e93c2673f9cedd96a75026` (stage 4AQ). The earlier stage 4AO final2 publication copy had `e088143b83c78e5e1c6844cd37d82893ebc91584b51e565dfd1974014896f835`. [Source versions](docs/SOURCE_VERSIONS.json) records every version with its changed files. Machine-specific paths and account names have been replaced with `/CONFIGURE/...` placeholders, and original volume identity values removed. This publication copy is intentionally not ready to deploy. [Source provenance](docs/SOURCE_PROVENANCE.json) records how the publication copy was derived from the installed stage 4AO final2 source; the installed stage 4AQ fingerprint is in the source versions record. The repository documentation is additional to that source snapshot. Private trial histories, notes, machine configuration, installation backups, screenshots, generated APKs and emulator disks are not included.

## Workflow

Read the skill, preserve the complete requested outcome, prepare one job, and save/read back its checkpoint. Write a compact first source revision and build it early. Use compiler errors to repair within the same job, then build again before calling a repair verified. Install the qualified APK, observe the actual screen, exercise the requested behavior, and retain the results. Finish with a verified stop and final checkpoint readback.

Every work action reuses the `jobId` returned by `prepare`. Source inputs are complete Java classes under the fixed `org.openclaw.trial` scaffold. Build the UI in Java using the prepared Android framework APIs; custom XML resources, AppCompat and new external dependencies are not supported by this route.

`android_project` is a direct tool: call it by name, not through Tool Search's `tool_call`, so screenshots reach the model as images. Tap coordinates are actual pixels of the display reported by `start_test`. Each observation also lists the visible text and controls with their tap centers (`uiSummary`). An out-of-display tap is refused without stopping the guest or requiring a rebuild.

## Time budgets

The first `prepare` accepts `timeLimitMinutes` from 5 to 60. New jobs default to 60 minutes (stage 4AO final2 defaulted to 30). That selection fixes the initial job deadline; repeated preparation cannot extend it or reset its counters. Each job permits 20 source writes, 10 builds and 120 work actions (stage 4AO final2 permitted 8, 3 and 40).

The agent can call `extend` to add 5–30 minutes when it needs more time, with a reason. The controller grants this only after a new successful build since the previous grant, at most three times and up to 120 minutes in total, including the initial limit. Extensions never add writes, builds or chat time; each request uses one work action.

The OpenClaw chat has a separate budget, and in the October 4 morning trial it ran out first. A fresh operator dispatch must request an appropriate `chat.send.timeoutMs`: `3600000` for the 60-minute default, or `7200000` if the run may use extensions up to 120 minutes. Changing the job duration does not extend a chat already running or change OpenClaw's global default. Chat and job timers begin at different times; leave cleanup and checkpoint time before the earlier deadline.

Individual calls retain their shorter limits: build and guest boot are each capped at 180 seconds, with transport bounds of 410–420 seconds. More wall-clock time does not add build attempts or relax isolation.

## Run fixture tests locally

These tests use controlled fixtures and mocked external operations. They do not launch generated app code, a real build worker or a live model.

```sh
python3 -B -m unittest discover -s android-chat-bridge -p 'test_*.py' -v
node --test android-chat-bridge/plugin/test_plugin.mjs
```

The deployed stage 4AO final2 suites passed 198 Python and 44 Node tests on the Studio; detailed scope and the fresh model trial are in [TEST_RESULTS.md](docs/TEST_RESULTS.md). Stage 4AQ passes 230 Python tests (Python 3.9 and 3.14) and 47 Node tests, both locally and in the staged and installed suites on the Studio. Passing fixtures does not establish autonomous app completion.

## Deployment prerequisites and limits

The existing installation uses local Ollama/Qwen, a pinned offline Linux Android build image, a bounded project volume, and an official Android SDK/emulator prepared separately. Those tools and licensed SDK files are not distributed here. Paths, account identity, model/image fingerprints and baseline checks are specific to that installation and must be reviewed before any port.

**Do not run `android-chat-bridge/install.py` as a setup or update command.** It is the retained original first-install script with historical baseline checks, not a general installer for this snapshot. Installing the skill text alone also does not install its controller, plugin, SDK or offline image. The [handoff](docs/HANDOFF.md) explains the current installation state and what a portable installer would still need.

The worker is non-root, offline, has a read-only root filesystem, drops capabilities, and receives one project mount without the Docker socket. Generated Java runs only in the worker and Android guest. Existing host OpenClaw/model services remain separate trust surfaces. The recorded tests establish their exercised checks, not a comprehensive security guarantee.
