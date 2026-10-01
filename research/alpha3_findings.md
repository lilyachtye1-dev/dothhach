# Alpha 3: evidence and remaining unknowns

Base remains GitHub lilyachtye1-dev/dothhach, 8739838. No older source tree used.

## User evidence after alpha 2

- Repeated `[DODGER AUTO ON] hero=npc_dota_hero_antimage`.
- Every printed Assassinate observation: target 0xffffffff; local index 1021.
- Zero recognized local-target launches / SENT commands in the supplied log.
- Focus/menu/manual stops occur, but do not explain all armed intervals.
- The old reader skipped phase/CD/start when target was invalid. The heartbeat also
  throttled observations. Thus the log cannot establish those fields' live behavior,
  nor prove that no transient target was ever sampled between printed lines.

Do not repeat the earlier PREVIEW-only diagnosis. 0xffffffff is an invalid handle,
not an actual target at index 32767. It does not prove another hero was targeted.

## Changed in alpha 3

Read base cast fields before target rejection. Retain observed transitions in a
64-event journal; sender emits each retained event once, separately from heartbeat.
Overflow is explicit. Countdown ticks alone do not flood the journal. Same-index
serial mismatch remains a rejection; no cached/guessed target is substituted.

Record local modifier membership for schema-present RTTI names:
- CDOTA_Modifier_Sniper_Assassinate
- CDOTA_Modifier_Sniper_Assassinate_Trigger

This uses the pre-existing approved modifier-vector layout and owner/type/stability
checks, with a separate 15 ms / 600 read budget. A zero-stack member is still present.
A failed scan is UNKNOWN, never absence. Sampling is 1 Hz while idle/target unverified,
plus two attempts during an observed phase and one at its end. Unchanged results are
not logged repeatedly. No new projectile/buff/caster offsets have been guessed.

These observations do NOT link a mark to a particular caster or decode its semantics.
Presence is not input evidence. A mark can be observed even when phase remains zero;
this is intentional, to avoid tying the diagnostic alternative to an unproven field.

Compact HUD uses UI1 dashboard, not a changed ESP1 header. It separates arming,
waiting/blocking reasons and historical input. Stale data never show green ON.

## Not established / not fixed by assertion

- Whether m_hTarget is unreplicated, transient, layout-mismatched or otherwise unusable.
- Whether phase/CD/start are useful in this actual client.
- Meaning, caster association and timing of either modifier RTTI name.
- Successful Counterspell/reflection in Windows/Dota.

The target provider is not declared live-fixed. Synthetic memory, modifier-vector
replay, a fake input backend, C# DTO/model tests and HTML checks cannot establish this.
Next useful evidence is killsteal.log (and .1 if rotated) from the new automatic journal,
not another repeated check that AUTO was enabled.
