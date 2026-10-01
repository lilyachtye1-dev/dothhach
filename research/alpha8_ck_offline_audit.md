# CK: completed offline audit of FIX1 user capture

Date: 2026-09-29. No new user experiment requested or assistant Windows/Dota run performed. Input: latest uploads/frames.json (v2 JSONL renamed by user) and manifest.json. Full machine-readable report: /home/user/CK_probe_analysis.json, including input SHA256. Existing FIX1 release files remain byte-identical. Added only analyze_target_probe.py, tests/test_probe_analysis.py, sparse real fixture and this research note.

## Actual observations

594 frames: 198 per stage, all complete, no byte-limit/oversize error. All recorded hero/controller/ability/particle post-read identity checks are true. Active enemy CK caster handle 0x160016f, Chaos Bolt ability 0x13d011a (index 282). Second CK ability 0x82d4 stayed idle; it must not be merged with the actual cast.

| User-labelled stage | local cast-command candidate | first sampled phase | first sampled exit | CD on exit | selected target index candidate |
|---|---:|---:|---:|---|---|
| ck_protected | 0.5550765s | 0.6559643s | 1.0616837s | 10 | 631, AM 0xd8277 |
| ck_other | 1.0103783s | 1.0609420s | 1.4149561s | 10 | 897, Bloodseeker 0xbf8381 |
| ck_cancel | 1.1619556s | 1.2633050s | 1.4649579s | 0 | 631, AM 0xd8277 |

Phase samples: 8/7/4; CD-positive samples of active ability: 177/170/0. These are sampled boundaries, not precise game start/release times or verified hit outcomes. Known CK hAlreadyHitList bytes stay all zero in both CK instances throughout.

### Order buffers

Only controller 0xbf0001, slot 0, assigned AM 0xd8277 contains changing orders (next sequence 1835–1856). Observed block words at +40/+44/+48/+52 correlate with sequence/kind/target index/ability index. Kind 6 aligns with each cast; kind 10 appears at the sampled canceled phase exit. This is the user's issuing controller while controlling demo enemy units. Enemy CK controller 0x12f8003 (slot 1, assigned 0x160016f) has zero order headers and next sequence 1 throughout. Controllers for Bloodseeker/Void likewise have zero headers. No independent enemy command stream established; index matches are not a validated serial-bearing target decoder.

### All 112 particle identities

9,466 particle-entity observations covering the same 112 identities (63 C_DOTAWorldParticleSystem, 49 C_ParticleSystem). None of their recorded fields changed, including across stages. Every identity was sampled more than once; this is not simply a one-sample comparison. No exact known full-handle matches in the recorded candidate blocks/particle fields. Thus the expanded entity sample supplied no observed dynamic target/flight signal. This does NOT establish that no projectile exists: unrecorded fields, non-entity particle-manager objects and brief unsampled objects remain outside coverage.

### Promising-looking early target header pulse, rejected as defensive proof

Opaque hero particle-header u32 at byte 16 changed 1→2 on the chosen target in exactly the same captured frame as each local order candidate, including cancel. It returned to 1 after:

- protected: 0.4557122s (at 1.0107887, before the sampled CD transition);
- other: 0.4045778s (at 1.4149561, the same sampled frame as CD transition);
- cancel: 0.4545899s (at 1.6165455, despite no CD and after Stop candidate).

The observed behavior is compatible with a local command/target marker. The particle's name/semantics are not known: the captured one-hop entries contain further pointers, whose pointed-to objects were not recorded. This pulse cannot be called a projectile. Cancellation alone does NOT disqualify a genuine phase-target marker (Counterspell can use verified phase proof); the decisive missing evidence here is independent provenance/replication and validated target association. Do not enable phase Counterspell from this count-like word, matching local order or nearest hero.

Later target header changes 1→3 appear at 1.5160246s (protected) and 1.9189098s (other), 0.4543409s and 0.5039537s after sampled CD. No such later rise is recorded in cancel. They are consistent with post-release effects, potentially hit-related, but the unnamed raw change does not establish effect identity, projectile position, collision time or advance-warning reliability. Do not advertise these as confirmed hit detectors or flight trackers.

### Static schema boundary

The pinned schema includes particle entity classes and CEffectData with entity handles, but no validated generic tracking-projectile-manager root/layout. A CEffectData class declaration alone does not provide an actual live instance/root address. There is no basis to invent such a root or reinterpret an unrelated game-mode projectile class. Existing one-hop dumps cannot reconstruct memory never captured. Additional broad casts of other abilities would not resolve that structural gap by themselves.

## Work completed without another game run

Added analyze_target_probe.py: offline v1/v2/ZIP reader and reproducible audit of phase/CD transitions, particle field variability, raw header changes, unverified index-order correlations and command-aligned pulses. It imports no process reader/automation modules and never authorizes input. CLI rejects overwriting an existing report. No change to live sender, hooks, detector, schema or collector.

Saved tests/fixtures/ck_probe_fix1_sparse.json: 46 REAL selected observations, from 594 source frames, with source SHA and explicit sparse-selection description. Includes phase/header/order changes plus first/last particle observations. No fabricated timing or interpolation. Order blocks trimmed to 64 observed bytes. Sparse counts must not be mistaken for the full trace.

10 offline regressions cover cancellation counterexample, correct other-target identity, old-order baseline rejection, unchanged/changed/replaced particle identities, incomplete header/identity rejection, manifest mismatch, fixture provenance and absence of live process imports. Full suite: 554 discovered, 553 PASS, 1 SKIP (Mono absent), 17.293s. compileall and git diff --check pass. Existing FIX1 archive entries compared byte-for-byte to source: unchanged.

## Decision

Keep alpha7 runtime as the working baseline. Do not publish another nominally working CK/WR release, enable count-based target guesses, or ask the user to repeat captures of additional heroes. Reliable generic recognition remains blocked on the actual client-side projectile/effect object layout and independently valid target provenance, not a missing skill JSON entry or extra input thread. Current evidence is preserved and can be analyzed again offline.
