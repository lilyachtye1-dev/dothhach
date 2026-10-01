# Source and license notice

This standalone Zoom component is distributed as source under Mozilla Public License 2.0. The complete license is in `LICENSE-MPL-2.0.txt`.

## Upstream reference

- Project/author: tranqu1lizer / external_patcher
- Repository: https://github.com/tranqu1lizer/external_patcher
- Pinned revision: d565a98f6e9ca2856496a294c2b4b6e9e70cc413
- Source: https://github.com/tranqu1lizer/external_patcher/blob/d565a98f6e9ca2856496a294c2b4b6e9e70cc413/core/dota_sdk.hpp
- Upstream license: MPL-2.0.

`locator.py` adapts the source's camera-manager signature, RIP-relative address calculation, manager slot +0x20 and SetDistance member offset +0x2e4. The upstream GetDistance offset +0x270 is inconsistent with its SetDistance and is deliberately not used for writes. Experimental 2 reports raw values at both offsets without approving either one.

## Modifications / new implementation, 2026-09-30

Python port with a new bounded PE scanner, uniqueness checks, RTTI/object validation, data-page restrictions, conditional original-byte restoration, explicit enable, minimal Tk UI and synthetic tests. No BlackBone code or binaries are bundled or required. The upstream weather, particle, fog, ZFar, emoticon and remote-call functionality is not ported.

Other HACHBOB components are not included, modified, or relicensed by this archive. No assertion of upstream endorsement, current-game compatibility or anti-cheat safety is made.

## Experimental 2 diagnostic update

Separates structural discovery from old-distance plausibility validation. Adds a bounded camera-memory and candidate-vtable code report, a dedicated read-only handle/CLI launcher, automatic reporting on an unsupported old distance in the normal UI, and 20 synthetic tests. No new writable offset, numeric fallback, broad value scan, remote call, or patch has been added. The user-provided experimental-1 log rejected the old field; this update is not a live zoom compatibility fix.

## Experimental 4: automatic-mode correction

The user-supplied read-only report of build timestamp 1790705915 was decoded offline.
The observed camera update has a nonnegative +0x2e4 override branch and a negative
fallback to game calculation, feeding +0x15c. The original positive-only startup
validation was wrong for the observed -1 automatic mode. `verified.py` now requires
the observed PE/RTTI/RVAs and full method SHA256; the UI uses that pinned profile.
Original -1 is conditionally restored as exact bytes. No new writable offset,
code patch, fog feature, remote call or anti-cheat claim was added. Raw game method
binaries and personal report data are not bundled. See ZOOM_EXP4_ANALYSIS.md.
