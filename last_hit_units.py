"""Lane identity vocabulary, not a balance table or a nearest-unit heuristic."""
import re

LANE_NAME=re.compile(r'^npc_dota_(?:creep_)?(?:goodguys|badguys)_(melee|ranged|flagbearer|siege)(?:_upgraded(?:_mega)?|_mega)?$')
LANE_CLASSES={'C_DOTA_BaseNPC_Creep_Lane','C_DOTA_BaseNPC_Creep_Siege'}
LABELS={'melee':'Мечник','ranged':'Дальний','flagbearer':'Знаменосец','siege':'Катапульта','lane':'Линейный'}


def candidate_name(name):
    return bool(name and not name.startswith('creep_') and ('creep' in name or 'tower' in name or LANE_NAME.fullmatch(name)))


def lane_type(name,cls='',ranged=None):
    match=LANE_NAME.fullmatch(name or '')
    if match:return match.group(1)
    if cls=='C_DOTA_BaseNPC_Creep_Siege' or name=='npc_dota_creep_siege':return 'siege'
    if cls in LANE_CLASSES or name=='npc_dota_creep_lane':
        return 'ranged' if ranged==2 else 'melee' if ranged==1 else 'lane'
    return ''


def entity_kind(cls,name):
    if cls=='C_DOTA_BaseNPC_Tower':return 'tower'
    if cls in LANE_CLASSES:return 'creep'
    # Some builds expose the base RTTI for lane units; require an exact lane
    # name here. Neutral/summoned/hero objects never pass on a substring alone.
    if cls=='C_DOTA_BaseNPC_Creep' and LANE_NAME.fullmatch(name or ''):return 'creep'
    return None
