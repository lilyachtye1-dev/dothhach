# exp6 FarZ data-path correction

Source report SHA256: `c8678ee0a3f8206966567f214b52cc987dcf21dc99c575498e04d1c531bfe90d`.
The report contains build 1790705915 / 115027968 and camera virtual method 21,
RVA 0x1c07d50. Its observed 0x245-byte window is pinned by SHA256:
`f2e19a3151ffae295bf598be1f7f3097de42f1943cd1b415bf564438f3ef52c5`. It was decoded offline, not executed.

## Observed default path

At 0x1c07d56: load a global pointer; read signed selector at pointer+0x58.
At 0x1c07d73: load a second pointer; test byte at pointer+0x58.
If gate != 0 and 0 < selector < 2, select table + selector*24.
Otherwise, use a guarded static local slot. Relevant decoded RVAs:

- selector pointer reference: 0x61e7dd0
- gate pointer reference: 0x6163878
- alternative table base: 0x48b5cec
- static initialization guard: 0x61ff4a0
- default float slot: 0x61ff49c

At 0x1c07dcb the selected float is loaded into xmm0.
At 0x1c07dd2 it is compared with zero; a nonnegative value overrides xmm8.
The following optional GameMode lookup can supersede that value; otherwise it
continues through the later camera calculation. Thus the code has a usable
FarZ-override path without finding a GameMode entity in the entity table.
A GameMode/special-camera override may still take precedence; no universal
rendering outcome is claimed.

The static local is initialized via the MSVC guard sequence at 0x1c07f34:
Header call; comparison with -1; initial data writes/copy; Footer call; jump
back. Require a completed negative epoch < -1 before treating the slot as live,
and require the same epoch thereafter. Do NOT patch the guard or run this code.
The alternate table is not presumed writable. Unknown/RO storage fails closed.

## Runtime implementation

`zoom/farz.py` derives addresses from pinned instruction bytes, mirrors the
selector branch, validates PE/sections, exact code hash and writable non-executable
pages, then changes only the selected 4-byte float to max(original,18000), with
-1 allowed as the original default-path value. Initial floats outside -1 or
32..65536 are refused; no general negative/NaN/Infinity allowance.

Unlike exp5 there is no entity-name lookup, GameMode write or broad scan for FarZ.
No FCVAR flag changes, ConVar method invocation, vtable hook, code write, DLL edit,
page unprotection, remote call or constant-write loop. The selected data is shared
within this client module, not asserted to be a field owned solely by one camera.

Conditional restoration checks the same process/module/code/storage and guard,
not a possibly obsolete camera/entity pointer. A scene/camera change can stop
active control yet still allow restoring unchanged static storage. Foreign bytes,
changed module/code/guard and partial writes are NOT forcibly repaired. Races
between checks and writes remain possible. Forced termination cannot restore.

The actual current selector/guard/float values have not been captured by the
assistant; they must pass these runtime checks. The implementation is not a
claim of assistant-tested live clipping correction or anti-cheat safety.

## Preservation and tests

The user confirmed exp4 zoom and exp5 haze work. Their accepted core files are
preserved. Only the Scene UI's FarZ factory/logging/release label changed.
Historical Scene worker tests explicitly substitute the old GameMode helper;
new worker tests exercise DirectFarZ with NO GameMode present. Synthetic tests
construct equivalent RIP-relative instructions and data; they do not execute
game machine code. Captured instruction decoding is separately checked against
the actual supplied report. Raw user reports and code dumps are not bundled.

New module is original MPL-2.0 source, using the existing project's typed Windows
memory layer and camera verification. Included NOTICE/license remain applicable.
