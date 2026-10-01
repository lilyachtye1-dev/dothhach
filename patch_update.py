"""HACHBOB offline patch assistant. Standard library only; never executes a dump.
Default: audit/diff, NO writes. A schema is not a source of global RVAs.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parent
FILES = ('dota_esp.py', 'client_dll.json', 'ks_profiles.json')
LOCK = '.patch_pending'
# Explicit field identities, never guess offsets by number or fuzzy field names.
GROUPS = {
'C_BaseEntity': '''M_CBODY_COMPONENT m_CBodyComponent
M_P_GAME_SCENE_NODE m_pGameSceneNode
M_I_MAX_HEALTH m_iMaxHealth
M_I_HEALTH m_iHealth
M_LIFE_STATE m_lifeState
M_I_TEAM_NUM m_iTeamNum
M_H_OWNER_ENTITY m_hOwnerEntity''',
'CBodyComponent': 'M_P_SCENE_NODE m_pSceneNode',
'CGameSceneNode': 'M_VEC_ABS_ORIGIN m_vecAbsOrigin\nM_B_SCENE_DORMANT m_bDormant',
'C_DOTA_BaseNPC': '''M_I_CURRENT_LEVEL m_iCurrentLevel
M_B_CONSIDERED_HERO m_bConsideredHero
M_B_IS_SUMMONED m_bIsSummoned
M_B_IS_CLONE m_bIsClone
M_FL_MANA m_flMana
M_FL_MAX_MANA m_flMaxMana
M_B_IS_ILLUSION m_bIsIllusion
M_ISZ_UNIT_NAME m_iszUnitName
M_INVENTORY m_Inventory
M_VEC_ABILITIES m_vecAbilities
M_B_HAS_INVENTORY m_bHasInventory
M_N_PLAYER_OWNER_ID m_nPlayerOwnerID
M_FL_DEATH_TIME m_flDeathTime
M_FL_PHYSICAL_ARMOR m_flPhysicalArmorValue
M_FL_MAGICAL_RESIST m_flMagicalResistanceValue''',
'C_DOTA_UnitInventory': 'INV_H_ITEMS m_hItems',
'C_DOTA_BaseNPC_Hero': '''M_FL_RESPAWN_TIME m_flRespawnTime
M_FL_STRENGTH m_flStrength
M_FL_AGILITY m_flAgility
M_FL_INTELLECT m_flIntellect
M_FL_STRENGTH_TOTAL m_flStrengthTotal
M_FL_AGILITY_TOTAL m_flAgilityTotal
M_FL_INTELLECT_TOTAL m_flIntellectTotal
M_I_PLAYER_ID_HERO m_iPlayerID
M_H_REPLICATING m_hReplicatingOtherHeroModel
M_FL_SPAWNED_AT m_flSpawnedAt
M_FL_LAST_DEATH_TIME m_flLastDeathTime
M_I_HERO_ID m_iHeroID''',
'C_DOTABaseAbility': '''AB_I_LEVEL m_iLevel
AB_B_IN_ABILITY_PHASE m_bInAbilityPhase
AB_F_COOLDOWN m_fCooldown
AB_FL_COOLDOWN_LENGTH m_flCooldownLength
AB_I_MANA_COST m_iManaCost
AB_N_CHARGES m_nAbilityCurrentCharges''',
'C_DOTA_Item': 'IT_I_CURRENT_CHARGES m_iCurrentCharges\nIT_I_MAX_CHARGES m_iMaxCharges',
'CBasePlayerController': '''M_H_PAWN m_hPawn
M_ISZ_PLAYER_NAME m_iszPlayerName
M_B_IS_LOCAL_CONTROLLER m_bIsLocalPlayerController''',
'C_DOTAPlayerController': '''M_B_HERO_ASSIGNED m_bHeroAssigned
M_N_PLAYER_ID m_nPlayerID
M_H_ASSIGNED_HERO m_hAssignedHero
M_H_LAST_ASSIGNED_HERO m_hLastAssignedHero''',
'C_DOTAGamerules': '''GR_N_GAME_STATE m_nGameState
GR_F_GAME_START_TIME m_flGameStartTime
GR_F_PRE_GAME_START m_flPreGameStartTime
GR_N_GAME_WINNER m_nGameWinner''',
'C_DOTA_PlayerResource': '''PR_VEC_TEAM_DATA m_vecPlayerTeamData
PR_VEC_PLAYER_DATA m_vecPlayerData
PR_ID_TO_PLAYER m_playerIDToPlayer
PR_ID_TO_PAWN m_playerIDToPawn
PR_ISZ_NAME m_iszName''',
'PlayerResourcePlayerTeamData_t': '''TD_SELECTED_HERO_ID m_nSelectedHeroID
TD_KILLS m_iKills
TD_ASSISTS m_iAssists
TD_DEATHS m_iDeaths
TD_LEVEL m_iLevel
TD_RESPAWN_SECONDS m_iRespawnSeconds
TD_H_SELECTED_HERO m_hSelectedHero
TD_AFK m_bAFK
TD_TEAM_SLOT m_iTeamSlot''',
}
FIELDS = {symbol: (cls, field) for cls, rows in GROUPS.items()
          for symbol, field in (line.split() for line in rows.splitlines())}
RVA = {
 'client.dll': ('DW_GAME_ENTITY_SYSTEM', 'DW_VIEW_MATRIX', 'DW_VIEW_RENDER', 'DW_GAME_RULES', 'DW_GLOBAL_VARS'),
 'engine2.dll': ('DW_NETWORK_GAME_CLIENT', 'DW_BUILD_NUMBER'),
 'object_offsets': ('DW_HIGHEST_INDEX', 'OFF_NGC_LOCAL_PLAYER'),
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def pairs(items):
    out = {}
    for k, v in items:
        if k in out:
            raise ValueError('Duplicate JSON key: ' + k)
        out[k] = v
    return out


def load(data):
    if len(data) > 24 * 1024 * 1024:
        raise ValueError('JSON exceeds 24 MiB')
    return json.loads(data, object_pairs_hook=pairs,
                      parse_constant=lambda s: (_ for _ in ()).throw(ValueError(s)))


def number(value, maximum):
    if isinstance(value, str):
        value = int(value, 0)
    if type(value) is not int or not 0 < value <= maximum:
        raise ValueError('Expected a positive bounded integer, got ' + repr(value))
    return value


def schema_values(data):
    classes = load(data)['client.dll']['classes']
    return {symbol: number(classes[cls]['fields'][field], 0x100000)
            for symbol, (cls, field) in FIELDS.items()}


def assignments(source, names):
    out = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in names:
                if target.id in out:
                    raise ValueError('Duplicate assignment: ' + target.id)
                if not isinstance(node.value, ast.Constant) or type(node.value.value) is not int:
                    raise ValueError('Not a literal integer: ' + target.id)
                out[target.id] = node.value
    if names - out.keys():
        raise ValueError('Missing constants: ' + ', '.join(sorted(names - out.keys())))
    return out


def replace_constants(source, values):
    nodes = assignments(source, set(values))
    lines = source.splitlines(keepends=True)
    starts, offset = [], 0
    for line in lines:
        starts.append(offset)
        offset += len(line)
    changes, edits = [], []
    for key, value in values.items():
        node = nodes[key]
        if node.value == value:
            continue
        # AST columns are UTF-8 byte offsets, not character offsets.
        a = starts[node.lineno - 1] + len(lines[node.lineno - 1].encode()[:node.col_offset].decode())
        b = starts[node.end_lineno - 1] + len(lines[node.end_lineno - 1].encode()[:node.end_col_offset].decode())
        edits.append((a, b, str(value) if key == 'BUILD_EXPECTED' else hex(value)))
        changes.append(f'{key}: {node.value:#x} -> {value:#x}')
    for a, b, text in sorted(edits, reverse=True):
        source = source[:a] + text + source[b:]
    compile(source, 'dota_esp.py', 'exec')
    return source, changes


def manifest_values(data, schema):
    m = load(data)
    if set(m) != {'format', 'schema_sha256', 'build', 'client.dll', 'engine2.dll', 'object_offsets'}:
        raise ValueError('Unexpected/missing manifest keys')
    if m['format'] != 'hachbob-offsets-v1' or m['schema_sha256'] != digest(schema):
        raise ValueError('Manifest format or exact schema SHA256 mismatch')
    values = {'BUILD_EXPECTED': number(m['build'], 0x7fffffff)}
    for group, keys in RVA.items():
        if set(m[group]) != set(keys):
            raise ValueError('Unexpected/missing offsets in ' + group)
        values.update({key: number(m[group][key], 0x10000 if group == 'object_offsets' else 0x40000000)
                       for key in keys})
    return values


def template(root):
    source = (root / FILES[0]).read_text(encoding='utf-8')
    names = {'BUILD_EXPECTED'} | {k for keys in RVA.values() for k in keys}
    old = {k: v.value for k, v in assignments(source, names).items()}
    return dict(format='hachbob-offsets-v1', schema_sha256=digest((root / FILES[1]).read_bytes()),
                build=old['BUILD_EXPECTED'],
                **{g: {k: hex(old[k]) for k in keys} for g, keys in RVA.items()})


def plan(root, schema_path, manifest_path=None, profiles_path=None):
    schema = schema_path.read_bytes()
    values = schema_values(schema)
    if manifest_path:
        values.update(manifest_values(manifest_path.read_bytes(), schema))
    source, changes = replace_constants((root / FILES[0]).read_text(encoding='utf-8'), values)
    profile_source = profiles_path if profiles_path is not None else root / FILES[2]
    cfg = load(profile_source.read_bytes())
    if type(cfg.get('settings')) is not dict or type(cfg.get('abilities')) is not list:
        raise ValueError('Invalid profile document')
    # After any explicit patch application: no automatic input, no repinning.
    cfg['settings']['auto_cast'] = False
    cfg['settings']['live_modifier_layout'] = None
    from ks_core import Settings
    with tempfile.TemporaryDirectory(prefix='hachbob-validate-') as directory:
        check = Path(directory) / 'ks_profiles.json'
        check.write_text(json.dumps(cfg, ensure_ascii=False), encoding='utf-8')
        Settings(check)
    return {FILES[0]: source.encode(), FILES[1]: schema,
            FILES[2]: (json.dumps(cfg, ensure_ascii=False, indent=2) + '\n').encode()}, changes


def ensure_closed():
    if os.name != 'nt':
        return  # Offline development/tests; no game attachment on this platform.
    # Fail closed on enumeration errors. Other Python programs must also be closed.
    result = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True, check=True)
    import csv
    rows = list(csv.reader(result.stdout.decode('utf-8', 'replace').splitlines()))
    parsed = [r for r in rows if len(r) >= 2 and r[1].isdigit()]
    if not parsed:
        raise ValueError('Cannot enumerate running processes')
    blocked = [r[0] for r in parsed if int(r[1]) != os.getpid() and
               (r[0].lower() in ('dota2.exe', 'esp_overlay.exe', 'pythonw.exe') or
                r[0].lower().startswith('python') and r[0].lower().endswith('.exe'))]
    if blocked:
        raise ValueError('Close Dota, HACHBOB/feed and other Python processes: ' + ', '.join(blocked))


def atomic(path, data):
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with tmp.open('xb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def transaction(root, proposed):
    ensure_closed()
    if set(proposed) != set(FILES):
        raise ValueError('Invalid transaction file set')
    backup = root / '.patch_backups' / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
    # Lock BEFORE the backup; launchers block while it exists, also after a crash.
    with (root / LOCK).open('x', encoding='utf-8') as f:
        f.write(str(backup.relative_to(root)))
        f.flush()
        os.fsync(f.fileno())
    try:
        backup.mkdir(parents=True)
        before = {name: (root / name).read_bytes() for name in FILES}
        for name, data in before.items():
            atomic(backup / name, data)
        receipt = {name: {'before': digest(before[name]), 'after': digest(proposed[name])} for name in FILES}
        atomic(backup / 'receipt.json', json.dumps(receipt).encode())
    except BaseException:
        # No installation file has been written in this phase.
        (root / LOCK).unlink()
        raise
    try:
        for name in FILES:
            atomic(root / name, proposed[name])
    except BaseException:
        # If rollback itself fails, keep the lock and verified backups for recovery.
        for name in FILES:
            atomic(root / name, before[name])
        (root / LOCK).unlink()
        raise
    (root / LOCK).unlink()
    return backup


def rollback(root, backup):
    ensure_closed()
    backup = backup.resolve()
    if backup.parent != (root / '.patch_backups').resolve():
        raise ValueError('Backup must be inside this installation/.patch_backups')
    receipt = load((backup / 'receipt.json').read_bytes())
    if set(receipt) != set(FILES):
        raise ValueError('Invalid backup receipt')
    before = {name: (backup / name).read_bytes() for name in FILES}
    for name in FILES:
        if digest(before[name]) != receipt[name]['before']:
            raise ValueError('Corrupt backup: ' + name)
        if digest((root / name).read_bytes()) not in (receipt[name]['after'], receipt[name]['before']):
            raise ValueError('File edited since patch; preserve/reconcile it manually first: ' + name)
    lock = root / LOCK
    if lock.exists():
        if lock.read_text().strip() != str(backup.relative_to(root.resolve())):
            raise ValueError('A different pending transaction holds the lock')
    else:
        with lock.open('x') as f:
            f.write(str(backup.relative_to(root.resolve())))
            f.flush()
            os.fsync(f.fileno())
    for name in FILES:
        atomic(root / name, before[name])
    lock.unlink()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--schema', type=Path, help='New client_dll.json in the bundled dump format')
    p.add_argument('--globals', dest='manifest', type=Path, help='Matching hachbob-offsets-v1 manifest')
    p.add_argument('--profiles', type=Path, help='Optional replacement ks_profiles.json, validated before use')
    p.add_argument('--apply', action='store_true', help='Explicitly write; requires both inputs')
    p.add_argument('--template', type=Path, help='Export OLD bundled globals; NOT a patch download')
    p.add_argument('--rollback', type=Path, help='Restore the printed backup folder')
    a = p.parse_args(argv)
    try:
        if a.rollback:
            if a.schema or a.manifest or a.profiles or a.apply or a.template:
                raise ValueError('--rollback cannot be combined with other operations')
            rollback(ROOT, a.rollback)
            print('Rollback complete. Original profiles and offsets restored.')
            return 0
        if a.template:
            if a.schema or a.manifest or a.profiles or a.apply:
                raise ValueError('--template is a separate operation')
            with a.template.open('x', encoding='utf-8') as f:
                json.dump(template(ROOT), f, indent=2)
            print('Exported OLD values, not newly discovered offsets:', a.template)
            return 0
        if (a.manifest or a.profiles) and not a.schema:
            raise ValueError('--globals and --profiles require --schema')
        if a.apply and not (a.schema and a.manifest):
            raise ValueError('--apply requires BOTH --schema and --globals from the same build')
        if (ROOT / LOCK).exists():
            raise ValueError('Pending transaction. Restore backup: ' + (ROOT / LOCK).read_text())
        proposed, changes = plan(ROOT, a.schema or ROOT / FILES[1], a.manifest, a.profiles)
        print(f'HACHBOB: {len(FIELDS)} schema fields checked; {len(changes)} constant changes.')
        print('\n'.join(changes) or 'No mapped constant changes.')
        print('NOT verified: private layouts, game balance, running-game compatibility.')
        if not a.apply:
            print('DRY RUN: installation unchanged. No internet lookup or automatic RVA discovery.')
            if a.schema:
                print('Apply will force PREVIEW and invalidate private modifier approval.')
            return 0
        backup = transaction(ROOT, proposed)
        print('Applied. PREVIEW ONLY; private modifier approval removed. Backup:', backup)
        print('Rollback: py -3.11 patch_update.py --rollback "' + str(backup) + '"')
        return 0
    except (ValueError, KeyError, TypeError, OSError, SyntaxError, RecursionError, subprocess.SubprocessError) as e:
        print('ERROR:', e, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
