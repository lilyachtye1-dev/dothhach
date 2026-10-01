# Alpha7 — targeted follow-up to manual-cast interference

Same authorized checkout: lilyachtye1-dev/dothhach, base 8739838. Release delta against the delivered alpha6 ZIP. No older application tree, no GitHub push. No new threat profiles, input threads or native UI changes.

## Evidence and its limits

User confirms Counterspell now activates even at extremely close range, but reports interference from manual ability/item use. Uploaded alpha6 log has 5 Counterspell SENT/CD pairs (4 phase protection, 1 launch), 1 Manta SENT/CD pair, and no quarantine. There are 9 Sniper cast starts; three have no SENT and lose their cast association before release. Two explicit modifier-list read races also occur. The old log did not record reset cause or continuous positions: it does NOT prove Blink caused those three missing commands. Nor does it show a sent command failing CD in this run.

WR target remains 0xffffffff. Three CK casts leave the probed header zero. Neither provides a verified future victim, so neither gets an invented new targeting path.

`tests/fixtures/alpha6_manual_casts.json` preserves the three missed begin/release pairs and explicit modifier races. Regression tests use real timestamps but modeled positions, Blink, input/readiness and intermediate polls. They are not a memory replay of the user's exact failure.

## Reproducible code defects corrected

1. **Phase association was erased by a position jump.** In alpha6, MarkedAssassinate applied the same displacement invalidation before and after launch. A local Blink or abrupt coordinate update could therefore turn an already tracked enemy cast into 'old mark without cast start'. Alpha7 forces a fresh owner-checked mark scan on displacement. Only the same complete hero/caster/ability identities, mark instance, cast start and continuously observed active phase can retain the episode. Travel bounds are recomputed at the new position; threat ID is unchanged. Missing/changed mark, foreign target, ambiguity and observation gap still reject. A jump after release still discards the old flight estimate: there is no verified projectile trajectory to reconstruct it from.

2. **Manual activity identification depended on an incomplete name list.** CombatReader inspected non-profile abilities/items but Dodger tried to recognize their busy reason against its narrower action list or a separate UI ability list. It now receives `_ks_busy_verified` plus the actual active cast identities from that same read. Known non-profile ability/item activity may be interrupted for defense without pretending that item is itself a supported defense. Unknown activity is sticky: a later known cast cannot overwrite an earlier read failure into permission. Existing offensive KS manual-input behavior is unchanged.

3. **Transient modifier-list mutation destroyed phase history.** Only the two explicit 'modifier changed during read' / 'modifier vector changed during read' race errors can now retain an already established active-phase episode for a bounded retry. No threat/input is emitted on the failed read. The next successful frame must again satisfy identity, same token, cast and freshness checks. The original 150 ms continuity deadline is not refreshed by retries. Owner mismatch, arbitrary read failures, missing/replaced mark and unknown reads after release still discard tracking. No recovery from a first observation in the middle of a cast.

## Diagnostics added

- `tracking_change`: phase reposition revalidated, displacement reset, observation gap, identity or cast/mark change.
- `tracking_retry` / `tracking_reset`: transient pause versus episode discard and its cause.
- `DODGER INPUT_STATE`: only configured game bindings while a verified threat is present, throttled; busy reason and reader verification. Not text recording or a global keyboard history.
- `DODGER INPUT_ATTEMPT` / `INPUT_SKIPPED`: selected source, evidence/deadline, held game keys and explicit final rejection.
- CD feedback includes elapsed time since submission. CD still does not prove reflection or uniquely attribute activation to injected rather than coincident manual input.

This separates 'tracking lost before input', 'sender declined', and 'submitted but no cooldown'. It is not evidence that game-side order arbitration is now universally solved.

## Verification

23 new tests in test_alpha7.py: phase displacement with forced mark refresh; unchanged threat ID and recomputed interval; changed/absent/cached mark rejection; bounded phase-only modifier retries; timeout, owner/foreign-target rejection; flight invalidation retained; sparse real timestamps with modeled movement; current reader activity for non-profile spells/items and sticky unknowns; service admission and explicit-false/truthy-string rejection. An integrated synthetic reader → actual Python WinInput/fake user32 test holds Q/R/item keys through a Blink and modifier-list mutation and observes one E key pair plus cooldown confirmation, without releasing those held keys.

Full suite: 518 discovered, 517 passed, 1 skipped (Mono unavailable; existing native compact-model smoke). 23.304 s source run. Python compileall and git diff --check passed. Native C# files are byte-identical to alpha6; no new native cross-build or WPF execution claimed.

No live Windows/Dota run of alpha7 here. A game-side command accepted by SendInput may still be rejected/overwritten by the game; no keyboard lock, guessed Stop/F1 command, automatic same-threat retry, or second input sender was introduced. Unknown builds still fail the existing schema/PE checks; this release is not an automatic porting system for arbitrary archived builds. Chaos Bolt remains diagnostic-only.
