# exp4: correction supported by submitted code, not a new guessed offset

Evidence file SHA256: `c8678ee0a3f8206966567f214b52cc987dcf21dc99c575498e04d1c531bfe90d`.
Report UTC: 2026-09-30T04:08:17.474565+00:00; complete=true, errors=[].
PE timestamp 1790705915; SizeOfImage 115027968; RTTI `.?AVCDOTA_Camera@@`.
Camera update method (vtable index 3): RVA 0x1c367c0 through return at
0x1c372d7 inclusive, 0xb18 / 2840 bytes. Its SHA256 is:
`b323a3bcf694d2cb9c32b1ef31240e5aee9e319144345c131db9a3e05a800c37`.

The code was decoded offline as x64, not executed. The longer captured window
also contains neighbouring functions; ONLY the above method is fingerprinted.

## Relevant data flow

- 0x1c367d5: `mov rdi, rcx` (this object).
- 0x1c367f4: `xorps xmm8, xmm8` (zero); xmm8 stays zero until epilogue.
- 0x1c36ee3: `movss xmm2, [rdi + 0x2e4]`.
- 0x1c36eeb: `comiss xmm2, xmm8`.
- 0x1c36eef: `jae 0x1c37001`.
- Negative values instead execute the intervening normal game-calculation branch.
- 0x1c37008: `movss [rdi + 0x15c], xmm2`.
- 0x1c3727b: `movss xmm3, [rdi + 0x15c]`, followed by camera-vector/position
  arguments, output address `[rdi + 0x38]`, and a call at 0x1c372b7.

Thus in this observed branch +0x2e4 is a nonnegative override feeding the
computed distance at +0x15c. The captured -1 is a usable automatic-mode sentinel;
requiring the override itself to contain 600..2600 at startup was incorrect.
This does NOT prove every special-camera branch consumes the override.

## Game-owned transition

At method entry, the value at +0xdc is compared with zero. Positive values
enter interpolation/completion logic which writes +0x2e4 at 0x1c36881 or
0x1c3689d. Nonpositive values skip that block. The new profile refuses control
while this timer is positive or nonfinite; it does not reset/patch the timer.

## Implementation decision

Keep the existing 4-byte write at +0x2e4. Permit exactly -1 as an original value
ONLY after matching this build, object identity and full method fingerprint.
Restore the exact original bytes (-1 normally), not a guessed default of 1200.
Do not write +0x15c (derived output), +0x270, nearby vectors, FoW/FarZ or code.
The ordinary UI now requires this verified profile; no legacy unknown-build
fallback is used. The read-only diagnostic modules remain read-only.

The original control's exact-byte ownership and identity checks remain in force.
This is an implementation repair based on observed code, not an assistant-run
live-game test or an anti-cheat/restore guarantee.
