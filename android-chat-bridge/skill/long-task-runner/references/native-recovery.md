# Read a recorded native attempt after interruption

This reader is for attempts registered by the reviewed native controller. Ordinary
OpenClaw sessions do not automatically have this journal. The operator supplies
the exact private journal root and its device/inode, registration file and hash,
and independently retained latest head file and hash. Do not reconstruct those
values from a model reply or replace the expected head with whatever the database
currently contains.

Run through the authorized trusted host executor, with a Node runtime supporting
`node:sqlite` and `DatabaseSync.enableDefensive`:

```text
node <skill>/scripts/reconcile-native.mjs \
  --root <operator-journal-root> --root-dev <device> --root-ino <inode> \
  --registration <operator-registration-file> --registration-sha256 <sha256> \
  --head <operator-retained-head-file> --head-sha256 <sha256>
```

This command only reads. Exit 0 means the registered history matched the supplied
pins; it does not mean the task succeeded. Exit 2 means reconciliation was refused.
Keep the raw result with the parent checkpoint and retain its identity, revision,
phase, reason, and `nextPermittedAction`.

| Phase | What the record establishes |
| --- | --- |
| `registered` | No session-create intent is recorded. |
| `session-create-pending` | Creation was reserved; whether it completed is unknown. |
| `session-created` | The actual session ID was recorded; no launch intent is recorded. |
| `launch-pending` | A launch was reserved; its original outcome is unknown. |
| `observed-returned` / `observed-rejected` | The original controller recorded its native promise joining and capability retirement. This is a lifecycle result, not task verification. |

All phases permit inspection only. Claims remain retained. The reader provides no
launch, retry, release, cancellation, or publication API and never reconstructs a
native capability. A saved return/rejection does not prove that a provider is idle
or a worker stopped. Missing outcome evidence, stale heads, replacement Gateway
status, EOF, and process disappearance do not establish native completion.

The registration and head must be retained outside the journal before dependent
operations. A crash after a database commit but before the corresponding head is
retained makes the reader refuse the stale head. Preserve both artifacts for
review; do not repair them by guessing or clearing writer ownership.

The reader trusts operator-supplied identity and file pins. It is not an attestation
against a compromised trusted host or privileged writer. Historical outcome
records do not automatically authorize continuation of another native attempt.
