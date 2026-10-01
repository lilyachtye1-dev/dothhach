# Alpha5: Counterspell arbitration and missing-CD recovery

## Evidence from the user's alpha4 run

Input: uploaded `killsteal.log` and `overlay.log`, 2026-09-29. The slim event fixture is `tests/fixtures/counterspell_alpha4.json`; full personal runtime logs are not redistributed.

| Wall time | Observed fact |
|---|---|
| 00:30:18.097 | Counterspell SENT, action identity `('antimage_counterspell', 3105068429568)` |
| 00:30:18.114 | New cooldown observed for that identity |
| 00:30:48.016 | Another Counterspell SENT |
| 00:30:49.530 | No cooldown feedback within the timeout; quarantine |
| 00:31:14.429, 00:32:07.675, 00:32:21.627 | Later generic blocked/disabled/CD reasons while the source remained quarantined |

The alpha4 Assassinate mark/phase/CD path repeatedly creates launches. This is not a repeat of the invalid-target diagnosis from alpha3. The user's report that Counterspell activated once agrees with the first cooldown observation, but neither SENT nor a cooldown proves reflection.

Manta and Lotus also have successful SENT/CD pairs; Manta and Eul have some missing-CD episodes. Therefore missing feedback is not proven to be a Counterspell-specific game mechanic. The second Counterspell command's lack of CD has no uniquely established cause in these logs. Actual selected unit and the game's consumption of the key pair are not recorded. A different selected unit/bind, game-side rejection/interruption or unavailable feedback are possibilities, not findings.

Repeated KS text “Настройки меню изменены…” is the fallback `off_reason` when KS is OFF and a transient mouse reason clears (`ks_service.py`, `ks_dashboard.py`). It is not evidence that every mouse click changed menu settings. Explicit `DODGER CONTROL` entries identify actual received controls.

The overlay log has focus transitions and a stale shutdown tail. Its coarse sampling does not identify why this particular SENT lacked cooldown. No focus/staleness checks are removed.

## Changes

- Same repository checkout, base 8739838; no import from another application tree.
- Counterspell bypasses ordinary LMB/RMB and their residual manual pause in the service and the actual WinInput guard. This is an independent permission policy on the single sender, not a second sending thread.
- During a mouse pause the planner may choose ONLY Counterspell. Other defenses and KS retain the previous mouse behavior. The no-cursor exception requires explicit service opt-in, E, the Counterspell action owned by the local living Anti-Mage, and no inventory slot. No cursor movement or mouse-button events are generated.
- Keyboard modifiers, hard-stop keys, physical E/other spell/item keys and their separate residual pause still block. Cast/channel/unknown activity, freshness, target/evidence, frame/viewport, focus and menu guards remain. An already pending/reserved KS command still prevents overlap.
- Missing CD is not success. First fully SENT Counterspell with no CD may be tried on one DIFFERENT verified threat after 0.5 seconds of continuously observed readiness, at least 0.5 seconds after its 1.5-second confirmation timeout, and after the old flight's latest estimate +0.25 seconds. Readiness gaps or unavailable resources reset the readiness interval.
- A second consecutive miss latches until fresh CD or explicit menu re-arm and the same new-threat/readiness checks. Initial arm does not pre-authorize a future reset. Partial/failed input is not eligible for automatic or menu-reset recovery. Hero switching does not erase pending uncertainty/quarantine; late feedback for the old hero stays out of the new hero's compact history.
- Other defenses retain their quarantine policy. Stale CD cannot release quarantine. Planner rejection reasons are separated; quarantined cards are not presented as input-ready. SENT/CD/no-CD events print immediately instead of relying on the slower heartbeat. SENT logs include policy and sampled LMB/RMB state.

## Verification and limits

430 tests pass, no skips in this environment. Alpha5 adds 42 Counterspell-specific tests plus one keyboard-block regression; the old RMB-block regression is intentionally reversed to match the new user request. Coverage includes real WinInput Python guard with fake user32/INPUT storage, shared-service arbitration, no cursor or mouse events, modifier/key races between guards, held E, focus/stale/arming controls, no repeated threat, the user's recorded SENT/CD/SENT/quarantine timing, new-threat recovery, two-miss latch, explicit reset, stale CD, partial input and hero switching.

The log fixture supplies only actual event order/timing. Unit snapshots, threat windows, intermediate polls, cooldown magnitude and OS input are synthetic. It is NOT a full gameplay replay. Previous alpha4 detector tests remain; detector code, mechanics profiles, schema and native C# sources are unchanged from the delivered alpha4 archive. Native C#5/net48/x64 cross-build passes with warnings-as-errors; actual DTO/compact-model smoke under Mono executes 27 assertions. No Windows WPF or Dota runtime was executed here.

There is no verified selected-unit reader, chat/menu-state reader beyond existing controls/focus, projectile manager, new guessed offset, or success-of-reflection detector. `C_DOTAPlayerController.m_nSelectedUnits` has a schema offset but that alone does not establish its container/layout/semantics. It is not guessed into an authorization check. In a demo, return selection to Anti-Mage after controlling Sniper. Normal click-driven movement/attacks on Anti-Mage need not be stopped for Counterspell. Held modifiers or other ability casts still can suppress the automatic E.
