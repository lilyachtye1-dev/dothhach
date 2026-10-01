# First user target-probe capture (2026-09-29)

Inputs preserved in /home/user/uploads/manifest.json and frames.json. User renamed frames.jsonl to frames.json only; parsed as JSON lines, all 625 records valid. PE/schema match pinned build. All frames complete; all recorded post-read object identity checks true. This is a real user capture, not an assistant Windows run.

## Coverage

- wr_protected: 198 frames, 9.9595s. Phase at 1.1119–1.2124s; first sampled exit/CD10 at 1.2626s.
- wr_other: 198 frames, 9.9519s. Phase at .6082–.760s; first sampled exit/CD10 at .8113s.
- wr_cancel: 198 frames, 9.9600s. Phase at 6.469–6.570s; exit at 6.6213s without CD. Earlier local command/stop also present, without sampled phase. Labels remain user declared.
- ck_protected: only 31 frames, 1.5142s. Active caster ability handle 0x13d011a, caster 0x160016f; phase .4542–.808s, CD10 at .858s. A second CK ability stayed idle.
- ck_other and ck_cancel absent. Manifest error: 24MiB uncompressed cap reached at 25,161,393 bytes, zero oversize-frame stubs. This is our recorder sizing defect, not a user error or renaming damage. Do not request the full same test unchanged.

## Useful new order-buffer correlation — LOCAL INPUT ONLY

Controller 0xbf0001 (player slot 0; assigned hero AM 0x13f80d2 in WR stages, AM 0xd8277 in CK stage) has an order-header pointer at header byte 8. In its captured one-hop block, little-endian u32 at byte 40 tracks order sequence; byte 44 takes values 6 (cast candidate), 10 (stop candidate), 1 (move candidate); byte 48 matches target entity index; byte 52 matches ability entity index. Observed:

| stage | first candidate time | seq | kind | target index | ability index |
| --- | ---: | ---: | ---: | ---: | ---: |
| wr_protected | .9601 | 1797 | 6 | 210 (AM 0x13f80d2) | 751 (WR 0x15a82ef) |
| wr_other | .5570 | 1808 | 6 | 897 (Bloodseeker 0xbf8381) | 751 |
| wr_cancel early | .7567 | 1818 | 6 | 210 | 751 |
| wr_cancel early stop | .8576 | 1819 | 10 | 0 | 0 |
| wr_cancel phase | 6.4690 | 1820 | 6 | 210 | 751 |
| wr_cancel stop | 6.6213 | 1821 | 10 | 0 | 0 |
| ck_protected | .2519 | 1831 | 6 | 631 (AM 0xd8277) | 282 (CK 0x13d011a) |

The bot caster controllers' order headers remain zero with unadvanced sequences. The useful data lives in the user's own controller while they manually command enemy demo units. This is NOT proof that independent enemy/bot orders replicate into the same field. These are index-only correlations, not full serial-bearing target handles, and the block may retain the last acknowledged/stale command. No runtime decoder or input provider has been enabled. Do not claim a universal enemy target source from this demo.

## Other fields and recorder limitations

WR target remains ffffffff in every sample; all four arrow vectors remain zero and shackle_count remains zero. CK known header stays zero. No exact known full-handle matches in collected opaque blocks/particle fields.

Particle output records the same 16 identities; recorder selects sorted cached particles[:16], so early/static objects can starve later discoveries. Per-frame RTTI scan_complete is false (96 of ~1307–1380 entity records per frame); rotating discovery still runs but field-output truncation is a separate deficiency. Absence of a target/projectile in this output proves no global absence. Also several announcer entity names satisfy the existing hero prefix filter, wasting capture size. Fix output coverage and budget strategy before further capture requests.

Ordinary alpha7 automation remains untouched. No need for the user to rename back or resend these files. User has already supplied the WR comparison and a CK release; do not ask to repeat those blindly.
