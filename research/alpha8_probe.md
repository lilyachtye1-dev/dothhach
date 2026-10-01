# Alpha8 probe: change of evidence source, not another working-dodge claim

Only the same dothhach checkout is used. No runtime sender/detector/native code was modified from alpha7. Previous WR/CK logs show changing phase/CD but a consistently invalid WR target and zero CK hAlreadyHitList header, including user cancellation trials. Repeating those old fields cannot reveal a new target.

## New artifact

`TARGET_PROBE.bat` starts `collect_targets.py` independently of automation. It guides six operator-labelled WR/CK episodes: protected Anti-Mage, other target, cancellation. No manual target label becomes machine-verified evidence. The recorder stores:

- Full identity handles/backlinks and identity rechecks, hero positions explicitly potentially stale, teams, dormant states.
- WR base phase/CD/timing fields plus schema-named arrow vectors and shackle count, not interpreted as projectile coordinates.
- CK named header, still not assumed to contain a target.
- Controller assigned-hero handle/player slot, outgoing/server sequence numbers, send time, opaque unitorders/selected-unit headers.
- Hero particle-property headers and at most two 384-byte one-hop address candidates per named container. No layout/count assumption, recursive pointer chase, global memory scan, or foreign-build RVA.
- Rotating RTTI search of entity records for known particle classes, raw schema-backed fields and exact full-handle integer matches explicitly tagged unverified.

Module addresses and known hero/controller self pointers are excluded from the one-hop candidate reads. This does not establish that other readable candidates are valid containers. Handle coincidences, local control/selection, attacker orientation, and proximity never authorize a defensive command.

Dot's existing standalone entity discovery runs at most once per 0.5 seconds after discovery completes; target-ability enumeration is limited to the WR/CK hero type, not all unrelated abilities. Both may be slower than the target 20Hz. Per-frame Probe work has 80ms/1600-read bounds, while direct existing Dot helper/discovery work is measured but not falsely claimed to be covered by that budget. RTTI rotates through 96 entries/frame; max 40 hero/controller objects and 16 particle entities. Short-lived events may be missed. Warm-up occurs before each start cue.

Report creation is exclusive with a unique timestamp/UUID name. JSONL streams to ZIP rather than accumulating a full session in RAM. Limits: 24MiB uncompressed frames, 96KiB/frame, <=400 frames/stage; oversize/partial/interrupted outcomes are explicit. Manifest includes schema SHA/PE identity, declared instructions and sample counts. Original logs and user session controls are not packed into releases.

## Verification boundary

14 new synthetic tests; full suite 532 discovered, 531 pass, 1 skipped because Mono is absent. No new native build: C# is unchanged. No Windows/Dota execution of this collector here. This is useful new diagnostic plumbing, NOT a decoded generic projectile manager, a working Chaos Bolt/WR defense, or automatic adaptation to another archived build. Schema or PE mismatch refuses recording before collection with these offsets.

## Workspace cleanup

User explicitly authorized deleting old generated artifacts. Removed alpha1–6 release archives/previews/checksums, old pre-authoritative application copies/ZIPs and obsolete intermediate reports (65 top-level paths, about 92.8MiB). Kept authoritative dothhach, latest uploads, alpha7 release/checksum, and its verification material. A root workspace-cleanup.txt lists removed paths. This cleanup did not import old code into the current project.
