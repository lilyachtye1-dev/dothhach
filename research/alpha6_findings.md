# Alpha6: manual-input priority, menu persistence, close-cast protection

Base remains `lilyachtye1-dev/dothhach` commit 8739838. All application changes were made in the same checkout; no older application source and no GitHub push.

## New user evidence (alpha5)

The uploaded `killsteal.log` has 15 Dodger SENT events and 15 CD confirmations: 12 Counterspell, 2 Manta, 1 Lotus. There are no Dodger quarantine events in this log. This is observed activation feedback, not reflection/dodge verification.

The visible pauses include R, W, C, Shift, Shift+R, Alt, and the old residual manual-input timer. Other defenses still had the intentionally retained alpha5 mouse gate. These gates are now removed for priority Dodger input, not for offensive KS.

Five observed close launches have an early-bound delta below the existing 0.18-second activation allowance:

| Logged launch | earliest − observed, seconds |
|---|---:|
| 01:37:39.727 | -0.012870 |
| 01:37:50.402 | +0.068642 |
| 01:38:26.258 | -0.022455 |
| 01:38:38.641 | +0.043225 |
| 01:41:08.282 | +0.103894 |

Each associated cast-start changes by +2.0 seconds at release. Sparse fields are preserved in `tests/fixtures/assassinate_alpha5_close.json`. The launch bounds were estimates, not measured projectile arrival times. Tests preserve the real cast timestamps but **synthesize 40 ms polls, positions at 1100 units, resources, and a -1 cast-point override**. That override value was NOT present in the user's log. This is not a full live replay.

## Implemented

### Priority defensive input

The service no longer uses an ordinary keyboard/mouse pause or its residual timer for Dodger. It may interrupt an explicitly identified local cast/channel; unknown activity remains blocked. The planner excludes only a held source key and can choose another defense. One sender and the KS ledger remain authoritative: an already reserved KS command is not overlapped. KS retains its own offensive manual-pause policy.

The actual WinInput backend has an explicit, source/key/profile-validated defensive path. Physical LMB/RMB and unrelated letters are not vetoes. It never sends mouse-button events. No-target defenses do not move the cursor. Legacy targeted input retains its aim after sending, avoiding an unverified immediate cursor restoration before game consumption.

For Shift/Ctrl/Alt, a separate read-only WH_KEYBOARD_LL observer retains only six left/right modifier states. It ignores injected events and never stores text or sends input. The sender temporarily neutralizes observed modifiers, sends ONE action key pair and restores current physical modifier state, with bounded reconciliation for a physical release during restoration. Unknown/stale/disagreeing modifier state skips input; partial input or unconfirmed cleanup fails closed and turns both modules OFF. This is not a guarantee against all hardware/game-input races. Win, Enter/Tab/End in game, native menu, focus and freshness guards remain; the source key itself is not re-pressed while physically held.

Lotus/Eul/Wind Waker retain cursor self-targeting by default. A new optional `Alt self-cast` checkbox uses Alt+slot without cursor movement, only when explicitly enabled by the user and a fresh physical observer is available. It requires a matching in-game Alt/Smart Double Tap setup. Function/numpad keys are rejected for this option, especially Alt+F4. The program does not change game settings or guess those bindings. Historical user guidance describes Alt self-cast as dependent on Smart Double Tap and documents custom alternatives; it is not proof of this user's configuration: [2](https://www.reddit.com/r/learndota2/comments/6tlpgc/can_you_self_cast_with_quick_cast/).

### Insert and native menu

Native controls now send tri-state module commands: missing means preserve, true means explicitly request arm, false means explicitly switch off. Visibility/settings changes preserve both states, while opening the native menu suspends input. Both module buttons can explicitly switch their own module off without changing the other. The new KS pending state is included in the DTO.

The authenticated session control supplies the native window HWND and process ID. Foreground identity is checked against both to cover focus arriving before the visibility message. Insert itself is a temporary guard, not a hard OFF. Closing gets a bounded 0.5-second focus-return grace even if the open message was coalesced; this does not turn an OFF module ON. Failure to regain focus, stale data, hero changes and End still disarm. Old control documents without `preserve_armed` retain their legacy fail-closed behavior; do not mix release files.

### Close Assassinate

A new `local_mark_phase` evidence path uses the existing owner-checked fresh mark and single-observed-caster correlation, after a continuously observed idle→phase transition and at least two matching samples. It is a **cast prediction**, not a launched projectile. It is considered only within 1400 units and only for Counterspell; Manta/disjoint and other defenses are never fired from this pre-launch evidence.

Timing uses readable `m_flOverrideCastPoint` (schema field, no guessed offset): -1 selects the pinned 2-second base, positive 0.25–5 seconds is an override. Zero/unreadable/other values are not assumed to be two seconds. If direct timing is unknown, two consistent completed, continuously observed casts can calibrate it; gaps/identity loss clear this history. The prediction includes phase/travel uncertainty and still must fit the full Counterspell duration. A cast-point change within the phase rejects the phase prediction. The first close cast can still be skipped if timing is unknown or the safe window does not exist.

Phase and release share exactly one threat ID, including after observed defense CD/forced cooldown reset in tests. Mark proof retains the 75 ms limit at planning and final callback. Cancellation, unknown/changed mark, known foreign target, serial changes and ambiguity continue to reject. **A cancelled enemy cast can waste an already activated Counterspell**, and a changed game-side cast duration can defeat the estimate. This is the explicit cost of acting before launch.

## Chaos Bolt: requested but NOT automated

Pinned schema class `C_DOTA_Ability_ChaosKnight_Chaos_Bolt` has `hAlreadyHitList` at 1704, **not** `m_hTarget`. Neither that list's container layout nor its relationship to the future target has been established. Treating its first integer as a target or firing because CK is nearby would be fabricated targeting evidence.

Added bounded cast/header diagnostics with verified ability/caster identities, phase/CD/start and 24 raw in-object bytes at the named field. No pointer following or guessed vector interpretation. Events explicitly say `target_state=unsupported_target`, `use_for_input=false`; the UI says the target is unknown. This does not require any mark above the ability bar, but it does NOT authorize a dodge. There is no fake working Chaos Bolt toggle. A subsequent CK cast log may help establish field timing; it cannot by itself guarantee a generic projectile reader.

## Verification boundary

495 unittest tests pass with no skips in this environment (65 new tests in `test_alpha6.py`). Coverage includes actual Python WinInput policy with fake user32, modifier release/restore races, synthetic Alt cleanup, forbidden Alt+F4, default legacy self-targeting, native-menu identity/control races, state-preserving Insert, explicit independent OFF, close-phase timing, five sparse user episodes with modeled gaps, phase/release dedup after CD reset, unknown-timing calibration, and Chaos Bolt rejection.

Actual native sources compile as C#5/.NET 4.8/x64 with warnings-as-errors; DTO/compact-model smoke runs under Mono. **No Windows keyboard hook, Windows WPF interaction, Dota execution or successful reflection has been verified here.** User alpha5 CD evidence must not be presented as an alpha6 live test. The schema, damage profiles, KS damage/stack logic and the five blocked defensive profiles remain unchanged.
