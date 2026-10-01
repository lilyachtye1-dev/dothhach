# Alpha8 probe FIX1

## Corrected recorder defects

1. First real capture stopped at 24MiB during ck_protected after 594 WR + 31 CK frames. FIX1 has a 12MiB independent budget per stage, 72MiB aggregate guard, and compact lossless inline interning of long raw hex strings. A full stage ends with an explicit partial status but does not terminate later stages. Existing read/frame bounds remain. Raw interning memory is limited to 4MiB of string characters (plus Python object/index overhead); after that new strings remain inline. A refused append does not commit dangling references. Original v1 and new v2 are decoded by iter_frames(). Original-size oversized frames still produce explicit stubs.
2. Particle output no longer takes the same sorted first 16. It prefers never-attempted live objects, then least-recently-attempted. Failed identities still advance selection, avoiding starvation. Replaced pointer/name/type identity candidates regain unseen priority. Dead entries are removed. Frame coverage reports discovered count, batch size and attempted count. This does not make discovery exhaustive: 96 rotating RTTI entries/frame and 0.5s entity discovery can miss brief objects, and >16 new objects per frame can overload the sample budget.
3. Announcer entities no longer enter the hero capture by prefix. Hero/controller/ability identity, one-hop limits, build pins and read-only restrictions stay intact.
4. Running stage counts update after each accepted frame and survive interruption/errors. Status distinguishes duration_complete, interrupted, stage_byte_limit and session_byte_limit. duration_complete denotes timer completion, NOT successful target proof. Short end beep added.
5. TARGET_PROBE_CK.bat runs three CK stages only. User's WR evidence is preserved; no need to repeat it unchanged. Original TARGET_PROBE.bat still runs both spells.

## Actual checks

12 new regressions: v1/v2 roundtrip, repeated-block elimination, bounded dictionary fallback, missing references fail explicitly, rollback on rejected writes, stage-budget isolation, fairness past 16 entities, newcomer/replacement priority, invalid identity non-starvation, announcer exclusion, all six guided stages surviving local size limits, interruption retaining running counters. Total collector tests 26; total suite 544 discovered / 543 pass / 1 skipped (Mono missing).

All 625 original uploaded frames were re-encoded and expanded back to exactly equal frame objects. JSONL 25,161,393 -> 15,660,818 bytes; ZIP 268,208 bytes. A synthetic six-stage replay of 400 frames/stage fits all budgets: 52,272,623 JSONL bytes, zero stubs. Synthetic replay reuses WR data, not fabricated evidence of CK behavior. Recorded-size reductions do not predict future dynamic capture size, particularly now that particle selection is broader.

These changes do NOT decode independently acting enemy commands. The first capture's order/target correlation belongs to the user's own controller while controlling demo enemy units; bot-controller order buffers were empty. No guessed target or new defensive input provider added. Main runtime, native UI and sender stay byte-identical alpha7. No assistant Windows/Dota run of FIX1.
