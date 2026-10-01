# Alpha 4 — input-capable Assassinate correction

Base: lilyachtye1-dev/dothhach, commit 8739838. Same source checkout; no older
application tree used and no GitHub push.

## Actual user evidence

The uploaded alpha3 killsteal.log contains these four phase transitions:

| Phase begins (log clock) | start during phase | start after phase | CD at release |
|---|---:|---:|---:|
| 08:12:10.806 | 5345.8335 | 5347.8335 | 10 |
| 08:12:25.771 | 5360.8003 | 5362.8003 | 10 |
| 08:12:36.161 | 5371.1670 | 5373.1670 | 10 |
| 08:13:12.702 | 5407.7334 | 5409.7334 | 10 |

Every recorded target handle is 0xffffffff. Each beginning coincides in the
recorded observations with CDOTA_Modifier_Sniper_Assassinate present on the local
Anti-Mage. The modifier remains at phase end and is absent at the next diagnostic
sample ~1 s later. That 1 s poll does NOT measure its exact disappearance time.
The Trigger modifier is false in these samples.

Dodger is armed in the relevant periods. Manual-input pauses occur, but cannot
explain the complete lack of recognized threats. Killsteal sent a Dagon at
08:13:07.463 and observed cooldown feedback at 08:13:07.509. No need to repeat the
old general-input diagnosis. The overlay log's initial slow render is not an
explanation for every later zero-recognition period.

## Changed operational path

- Stable modifier instance tokens reuse the existing verified manager layout,
  owner links, RTTI, complete-vector validation and bracket rereads. No new offsets.
- For invalid target only, a unique eligible observed Sniper and a new local mark
  within +/-150 ms of the observed idle->phase edge establish a correlated episode.
- Full local/caster/ability identities include serials; valid different targets and
  serial mismatch are never overridden. Multiple possible observed casters block.
- Require stable phase + same mark token, release + new CD within 120 ms.
- Positive release-time shift is checked against observed continuous phase duration;
  equal / 0 / -1 values still work. Not a blanket allowance of arbitrary timestamps.
- Produce evidence=local_mark_launch, passed to existing planner/single sender.
- Cache TTL is not rejuvenated by frame copies. Mark proof age <=75 ms is checked
  both by planner and pre-input callback. One bounded scan per ~40 ms or phase edge.
- Lost mark, unknown read, identity changes, gap, teleport and lost source visibility
  clear the episode. Torn phase/start reads emit no threat for that frame, and may
  recover only within the existing 150 ms continuity bound.
- The direct-handle path also handles a matching positive Assassinate release clock
  with known phase beginning, and drops retained flights on ability serial changes.

## What remains inferred

The modifier's own caster handle is NOT decoded. Victim ownership is verified from
its local manager; association with one observed caster is temporal correlation.
This is not a claim that a snapshot proves absence of all unknown/hidden casters.
Multiple-caster, stolen-spell and incomplete-roster situations are not universally
solved. The detector is not a projectile manager. Distance/speed/flight windows are
still estimates, and can be too late at close range or too wide at long range.

## Test provenance

The four sparse raw edge sequences are in tests/fixtures/assassinate_alpha3.json.
Intermediate 40 ms polls, a 1500-unit distance, modifier object token, memory layout
and a ready Counterspell are SYNTHESIZED in tests/test_assassinate_mark.py.
All four select Counterspell. A separate reader -> service -> fake backend test
sends E exactly once; negative tests cover no mark, stale/changed mark, cancellation,
wrong target/serial, multiple casters, stale proof, no mana, manual input, focus,
PREVIEW, disabled defense and KS reservation. Existing modifier-vector replay
fixtures verify the token API's owner-checked traversal separately.

This proves a previously blocked software path now reaches the sender under those
modeled conditions. It is NOT a replay of unrecorded game frames, nor proof of a
successful reflection in the user's Dota client. Alpha4 has not been run in-game here.
