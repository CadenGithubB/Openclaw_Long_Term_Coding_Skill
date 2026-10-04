# Project instructions

Read README.md, docs/HANDOFF.md and docs/TEST_RESULTS.md before changing the integration. The deployed implementation is under android-chat-bridge; the skill alone is not the complete tool.

Keep the distinction between guidance, controller enforcement, fixture tests and actual model outcomes. Do not label builds or launches as behavior verification. Retain known failures in reports.

Run the Python and Node commands in README.md for relevant changes. These are fixture tests; do not execute generated Android code, deployment scripts or downloaded project hooks on the host. Android source runs only in the restricted Linux build worker and official disposable emulator guest.

Treat install.py as a historical first-install script tied to one deployment, not a general installer or update command. Live Studio changes require the user's task authorization and current identity, baseline and inactivity checks. Preserve existing jobs, evidence, limits, local-only model routing and unrelated work.

Do not commit private histories, notes, configuration, credentials, installation backups, screenshots, APKs or emulator disks. Update public documentation with reviewed outcome summaries. A source snapshot manifest identifies its exact original bytes; create a new version record if source changes.
