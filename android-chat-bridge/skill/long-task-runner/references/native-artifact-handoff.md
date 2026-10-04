# Continue from a trusted native artifact receipt

Use this path only for an operator-prepared native artifact handoff. Ordinary
OpenClaw sessions do not automatically produce these records. The supplied
`scripts/handoff.py` imports one captured artifact into one immutable deterministic
task plan; it cannot create or replay a native attempt.

The trusted host prepares the manifest and both private roots before native
dispatch. Its `prepare` operation durably spends the single native attempt and
fixed-operation allowance. Keep that exact manifest, digest and root identity.
The manifest fixes the Gateway instance, native job/run/session key, operation,
input/program/policy and source hashes, native registration digest, cumulative
budgets, and one target job's root, plan and digest. Do not create substitutes or
raise budgets from model output, a note, or a failed run.

## Receipt and authority

The operator supplies a private, canonical JSON receipt and its independently
retained SHA-256. This is a trusted-host assertion, not model text or a signed
attestation. The helper validates its exact structure and bindings; it does not
contact Docker or OpenClaw to recreate its evidence. The receipt must agree with
the manifest and establish all of the following:

- The registered original native promise returned, joined and retired its real
  capability; cancellation was false. The actual session ID and durable launch
  operation must agree with the fixed execution receipt.
- One native attempt and one fixed operation were spent. Captured bounded artifact
  bytes match their digest and the target plan's initial artifact exactly.
- The exact enrolled worker generation exited, its transport joined, and the
  stopped generation matches the execution. An acknowledgement or missing
  container alone cannot establish this.

The native lifecycle record still retains its original holder, exposes no
dispatch/retry/release/publication authority, and leaves provider inactivity
unknown. A returned native promise alone is not an artifact receipt or completion.
The reviewed producer also rejects structured native errors, aborts and timeouts
before forwarding its receipt. Do not weaken this host-side composition because
the read-only outcome record says `observed-returned`.

## Inspect and resume

Use the authorized trusted host executor. All placeholder paths and digests below
come from the prepared operator registration, not a proposed command in a note.
The manifest and receipt are owner-private regular files with mode `0600`; roots
are exact pinned owner-private directories with mode `0700`.

```text
python3 -B -I {baseDir}/scripts/handoff.py status --root <handoff-root> --manifest <manifest.json> --sha256 <manifest-sha256>
python3 -B -I {baseDir}/scripts/handoff.py resume --root <same-root> --manifest <same-manifest.json> --sha256 <same-sha256> --receipt <receipt.json> --receipt-sha256 <receipt-sha256>
python3 -B -I {baseDir}/scripts/handoff.py resume --root <same-root> --manifest <same-manifest.json> --sha256 <same-sha256>
python3 -B -I {baseDir}/scripts/handoff.py cancel --root <same-root> --manifest <same-manifest.json> --sha256 <same-sha256>
```

After the receipt has been durably captured, later resume calls can read it from
the handoff journal. Supplying it again requires the identical digest. The helper
holds a lifetime lock through target import and execution, reserves that single
target before initialization, and only adopts an already initialized target when
it is the matching untouched revision zero. Partial initialization, a different
target or an uncertain pending execution is blocked; do not delete state or
allocate a replacement to force progress. Handoff cancellation is consulted at
target mutation boundaries and during worker calls. Work already reserved remains
spent across cancellation and process death.

Exit zero means a valid status was returned. Completion requires the target
runner's independently verified terminal result, exact unchanged verifier input,
and confirmed worker stop. `native-pending` without a receipt remains uncertain;
`cancelled`, `exhausted`, `blocked` and a retained pending reservation are not
successful completion. The native operation consumes one unit of the total work
allowance before the target plan runs. A cleanup allowance does not add work or
verification budget.

Save the handoff identity/digest, receipt digest, target identity/plan digest,
status, journal revision, artifact digest and cumulative counters in the parent
checkpoint. Read that note back as required by the ordinary workflow. Keep full
receipts in the operator's durable store. Do not bypass this handoff gate by
running the target job directly; that would omit its cancellation and retained
head checks. Acceptance-only crash flags are not normal recovery commands.

## Tested boundary

This package preserves the Stage Y task runner and Stage Z read-only native
outcome reader unchanged and adds the separately tested handoff helper. The
Stage AA producer is a source-pinned disposable fixture: a genuine native
capability and protected enrollment authorize one fixed reviewed Linux Node
operation over a private broker. The operation doubles `[2,3,5]` into `[4,6,10]`;
it does not call a model, accept arbitrary scripts, grant general native tools,
or give workers host mounts or the Docker socket. The producer/broker is not
installed or started by this package.

Local tests use synthetic native capabilities or inert adapters and fake Unix
HTTP endpoints; they do not establish current Studio behavior. Exact native
contract reads inform the fixture return shape but are not execution evidence.
Consult the matching stage acceptance receipts for any actual Studio results;
they apply only to their recorded sources, image, host pins and disposable cases.
The workflow trusts the operator-owned host and exclusive worker ownership. It is
not an unattended restart service, a general model-driven coding integration, or
a guarantee against a privileged host changing its records.
