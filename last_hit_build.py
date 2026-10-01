"""Last Hit-only, explicit build review. Never mutates KS profiles or updater pins.
No automatic approval from diagnostic JSON, equal class layouts, or build_number.
2026-09-30 review: supplied raw schema + loaded PE + three stable Zuus samples,
12 lane targets (melee/ranged/flagbearer); no siege or nonzero IAS evidence.
Private vector access remains guarded on EVERY read. IAS stays partial; new-build
siege requires a present, owned, stable modifier, never assumed 0% reduction.
"""
from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True)
class Build:
    schema: str
    timestamp: int
    size: int
    label: str
    reviewed_stats: bool = False


LEGACY=Build('7dd78049388a98fa332c175cfa3bf648691da99ca070fc8e8f3b04fecf3e53d9',1790374934,115027968,'alpha4 / 2026-09-26')
CURRENT=Build('fabedd63041971c8ee09243fb229abd896d259b8e7351a61c1700f594868b1c6',1790705915,115027968,'alpha5 / 2026-09-30',True)
BUILDS=(LEGACY,CURRENT)
COMMON={'DW_HIGHEST_INDEX':8336,'DW_BUILD_NUMBER':6356260,
        'DW_NETWORK_GAME_CLIENT':9493552,'OFF_NGC_LOCAL_PLAYER':248}
GLOBALS={
 LEGACY.schema:dict(COMMON,DW_GAME_ENTITY_SYSTEM=0x653DB10,DW_VIEW_MATRIX=0x61C4F10,
                    DW_VIEW_RENDER=0x61C58C8,DW_GAME_RULES=0x61BF940,DW_GLOBAL_VARS=0x5A8C380),
 CURRENT.schema:dict(COMMON,DW_GAME_ENTITY_SYSTEM=0x653DBB0,DW_VIEW_MATRIX=0x61C4F80,
                     DW_VIEW_RENDER=0x61C5938,DW_GAME_RULES=0x61BF9B0,DW_GLOBAL_VARS=0x5A8C400),
}


def check_build(raw,image,globals_values):
    digest=sha256(raw).hexdigest()
    build=next((b for b in BUILDS if digest==b.schema and
                type(image.get('timestamp')) is int and type(image.get('size')) is int and
                (image['timestamp'],image['size'])==(b.timestamp,b.size)),None)
    if build is None:
        raise ValueError('Last Hit: неизвестная пара схема / client.dll; требуется проверка новой сборки')
    wrong=[key for key,value in GLOBALS[build.schema].items()
           if type(globals_values.get(key)) is not int or globals_values[key]!=value]
    if wrong:raise ValueError('Last Hit: globals не соответствуют сборке: '+', '.join(wrong))
    return build


def reviewed_layout(build):
    # Explicit code-reviewed NEW build only. Not reinstating the KS profile
    # removed by the importer; not a generic fallback for other DLLs/dumps.
    if build!=CURRENT:return None
    return {'client_timestamp':build.timestamp,'client_size':build.size,'schema_sha256':build.schema,
            'manager_owner':8,'count':16,'data':24,'capacity':32,
            'modifier_manager':48,'stack_count':132,'max_count':128}
