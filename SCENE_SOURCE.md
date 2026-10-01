# Scene exp5 implementation / provenance

New original Python modules `zoom/scene.py` and `zoom/scene_app.py` are distributed
under MPL-2.0; complete license and original Zoom attribution are included.
The accepted exp4 source files are unchanged. No endorsement is implied.

## Evidence and reviewed layout

- Supplied current `client_dll.json` SHA256: `fabedd63041971c8ee09243fb229abd896d259b8e7351a61c1700f594868b1c6`.
  The developer checked the actual uploaded current schema, NOT only the older
  repository snapshot. Selected fields happened to be equal in both snapshots.
- Supplied `zoom_details.json` SHA256: `c8678ee0a3f8206966567f214b52cc987dcf21dc99c575498e04d1c531bfe90d`.
- Loaded camera build is pinned by the existing exp4 verifier: timestamp
  1790705915, image size 115027968, exact RTTI/RVAs/update-method fingerprint.
- Vtable slot 21 / RVA 0x1c07d50 accesses a game-mode result at +0x760:
  0x1c07dfd loads a float from `[rax+0x760]`; its positive value is selected
  before the remaining camera calculation. Current schema names this member
  `C_DOTABaseGameMode.m_flCameraFarZ`, offset 1888 / 0x760.
- The FarZ guard fingerprints the 0x245-byte window including its TLS-init branch,
  ending at the jump before the next function at 0x1c07fa0:
  `f2e19a3151ffae295bf598be1f7f3097de42f1943cd1b415bf564438f3ef52c5`.
- Entity-system global RVA 0x653dbb0 is the current reviewed project value.
  Table layout: 64 chunk pointers at +0x10, 512 identities per chunk, stride 0x70;
  entity at identity+0, full handle at +0x10, designer-name pointer at +0x20;
  entity back-pointer at entity+0x10. Unlike the general reader, there is no
  soft-accept of mismatched handles and no guessing other name offsets.

Named atmosphere fields, from the current schema:
- C_FogController.m_fog 1520 + fogparams_t.maxdensity 48 = 1568 / 0x620.
- C_EnvCubemapFog.m_flFogMaxOpacity 1560 / 0x618.
- C_EnvVolumetricFogController.m_flScattering 1520 / 0x5f0.

Those scene objects have NOT been sampled live by the developer. Runtime entity
RTTI, full serial handle/backlinks and value/page checks must pass before writes.
Schema/code evidence supports field identity, not a promise the renderer currently
consumes it. No active-volume spatial selection is claimed. Missing classes and
unsupported types fail without guessed fields. Maximum 64 matched controllers.

## Exclusions

No camera +0x70/+0x2a4 copying, no vtable/code patch, no remote function execution,
no modification of FoW fields, no weather/particle unlock. Four-byte float data
writes only. Existing Zoom control/input/config remain intact. Conditional
rollback/restore cannot eliminate races, guarantee forced-kill recovery, or ensure
anti-cheat safety. Original user reports and DLL/code dumps are not bundled.
