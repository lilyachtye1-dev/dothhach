# Development base

Repository: https://github.com/lilyachtye1-dev/dothhach

Commit: `8739838f696b908381cfe0b9d88c740b275c0206`

Base application files originate from this commit. Older local HACHBOB/Killsteal trees were not used.
Baseline on this checkout: 244 unittest tests passed.
This is an experimental, partial Dodger implementation; see DODGER.md for exact coverage.
No changes pushed to GitHub.

Alpha 3 follow-up: compact independent Dodger activity, cast-field transition journal,
and read-only modifier presence diagnostics. User AUTO test showed invalid Assassinate
target and no reactions. No claim of a live-fixed target provider. Work remains on
this same checkout; no fallback to older release sources and no push to GitHub.

Alpha 4: user-provided alpha3 log supplies four Assassinate phase/CD/mark transitions.
Added local-mark + unique observed cast correlation and matching positive release-time
handling. Runtime input path is connected; tests synthesize missing polls/positions/input.
No claim of a newly verified in-game reflection. Source/caster binding limitations are
explicit in DODGER.md. Still the same repository checkout, no push.

Alpha 5: user's alpha4 log contains real Counterspell SENT/CD then a second SENT
without CD and persistent quarantine. Added a Counterspell-only mouse-independent
policy inside the existing sender, bounded recovery on a new threat, and explicit
feedback/rejection reasons. No added sender thread, guessed offsets or claims of
knowing why the game did not expose CD. All changes stay on this checkout.

Alpha 6: the alpha5 user log proves 15 SENT/15 CD feedback, including 12 E,
with five close launches inside the input budget. Priority input now covers
all available defenses and ordinary keyboard/mouse input; a read-only physical
modifier hook supplies bounded neutralization/restoration to the SAME sender.
Native Insert preserves module states, with HWND/PID foreground and grace checks.
Close local-mark Assassinate gains Counterspell-only pre-launch prediction with
cast-point/calibration caveats. Optional Alt self-cast preserves cursor default.
Chaos Bolt is diagnostic-only: no verified future target, no fake working toggle.
495 tests and native cross-build, not Windows/Dota execution. Details and evidence:
research/alpha6_findings.md. Still this checkout; no push or old-tree import.

Alpha 7: targeted fix of phase association after a revalidated local displacement,
bounded phase-only modifier-list retry, and reader-verified manual activity for
non-profile spells/items. 23 new tests; 517 PASS / 1 SKIP (Mono unavailable).
No new input thread, keyboard lock, supported enemy spell or native UI change.
No live alpha7 Windows/Dota verification; research/alpha7_findings.md documents
what is reproduced in code versus not established by the user's sparse log.

Alpha8 probe: standalone read-only WR/CK comparison collector and batch entry.
Existing runtime Python/C# files unchanged from alpha7. No automatic new target
provider and no unsupported-build guessing. 14 new tests, 531 PASS/1 SKIP.
See TARGET_PROBE_README.md and research/alpha8_probe.md. Same authorized checkout.

Alpha8 probe FIX1: bounded lossless inline raw-blob interning, isolated stage byte
budgets, fair/new-first particle sampling, announcer exclusion, progress counters
retained on interruption, CK-only entry point. Existing alpha7 runtime unchanged.
543 PASS / 1 SKIP; 12 additional FIX1 tests. User v1 data losslessly roundtripped;
no new live Windows/Dota execution claimed. See research/alpha8_probe_fix1.md.

KS input FIX (2026-09-29): Camera reverted/removed at user's request. Native menu
and overlay build are byte-identical alpha8 FIX1, retaining KS/Dodger pages.
KS Enter/Tab no longer latch OFF; ordinary gameplay input uses the existing shared
priority transaction with live physical modifier restoration. Disabled Dodger
status heartbeat/event replay muted; pending feedback and errors preserved.
No new entity/damage/schema/Dodger detection changes.
