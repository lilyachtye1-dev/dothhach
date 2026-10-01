# -*- coding: utf-8 -*-
"""
dota_esp.py — v11. Dota 2 (Source 2) reader.

Фикс v11 для отзывчивости полного снимка:
  * штатный feed-скан ограничен highestEntityIndex; assigned/last/pawn hero
    handles выше hi по-прежнему force-fetch через get_entity(idx).
  * если найдено меньше 10 контроллеров, полный скан восстанавливает редкий
    случай заниженного hi; повтор не чаще раза в 5 секунд.
  * snapshot(full_scan=True) сохраняет полный обход для диагностик/старых вызовов.
  * handle-check identity смягчён (fallback без match).
  * PlayerResource: шире поиск + soft-match hero_id.
  * v9: reject pawn='player' | pid_scan | TP x0 normal.

Схема: client_dll.json 2026-09-26.
"""
import struct
import sys
import time
import math

try:
    import ctypes
    import ctypes.wintypes
except Exception:
    ctypes = None

import pymem
from pymem.process import module_from_name

# ---- globals (offsets.json) ----
DW_GAME_ENTITY_SYSTEM = 0x653DB10
DW_HIGHEST_INDEX = 0x2090
DW_VIEW_MATRIX = 0x61C4F10
DW_VIEW_RENDER = 0x61C58C8
DW_GAME_RULES = 0x61BF940
DW_GLOBAL_VARS = 0x5A8C380
DW_NETWORK_GAME_CLIENT = 0x90DC30
OFF_NGC_LOCAL_PLAYER = 0xF8

DW_BUILD_NUMBER = 0x60FD24
BUILD_EXPECTED = 6356260

VM_SIGS = [
    "48 8D 0D ? ? ? ? 48 C1 E0 ? 48 03 C1 C3",
    "48 8D 0D ? ? ? ? 48 C1 E0 ? 48 03 C1 48 C3",
]

# ---- C_BaseEntity ----
M_CBODY_COMPONENT = 0x30
M_P_GAME_SCENE_NODE = 0x330
M_I_MAX_HEALTH = 0x348
M_I_HEALTH = 0x34C
M_LIFE_STATE = 0x354
M_I_TEAM_NUM = 0x3E7
M_H_OWNER_ENTITY = 0x514

M_P_SCENE_NODE = 0x8
M_VEC_ABS_ORIGIN = 0xD8
M_B_SCENE_DORMANT = 0x113

# ---- C_DOTA_BaseNPC ----
M_I_CURRENT_LEVEL = 0xBAC
M_B_CONSIDERED_HERO = 0xBB7
M_B_IS_SUMMONED = 0xBBB
M_B_IS_CLONE = 0xBDC
M_FL_MANA = 0xC04
M_FL_MAX_MANA = 0xC08
M_B_IS_ILLUSION = 0xC2C
M_ISZ_UNIT_NAME = 0xC78
M_INVENTORY = 0x10C8
M_VEC_ABILITIES = 0xC30   # m_vecAbilities, client_dll.json 2026-09-26
M_B_HAS_INVENTORY = 0x11D8
M_N_PLAYER_OWNER_ID = 0x12C8
M_FL_DEATH_TIME = 0x152C
M_FL_PHYSICAL_ARMOR = 0x1534
M_FL_MAGICAL_RESIST = 0x1538   # m_flMagicalResistanceValue (client_dll.json 2026-09-26)

INV_H_ITEMS = 0x20
INV_MAX_SLOTS = 21
SLOT_TP = 15

# ---- C_DOTA_BaseNPC_Hero ----
M_FL_RESPAWN_TIME = 0x19D8
M_FL_STRENGTH = 0x19E0
M_FL_AGILITY = 0x19E4
M_FL_INTELLECT = 0x19E8
M_FL_STRENGTH_TOTAL = 0x19EC
M_FL_AGILITY_TOTAL = 0x19F0
M_FL_INTELLECT_TOTAL = 0x19F4
M_I_PLAYER_ID_HERO = 0x1A68
M_H_REPLICATING = 0x1A7C
M_FL_SPAWNED_AT = 0x1A84
M_FL_LAST_DEATH_TIME = 0x1ABC
M_I_HERO_ID = 0x1D10

# ---- ability / item ----
AB_I_LEVEL = 0x628
AB_B_IN_ABILITY_PHASE = 0x634   # C_DOTABaseAbility: герой сейчас кастует
AB_F_COOLDOWN = 0x638
AB_FL_COOLDOWN_LENGTH = 0x63C
AB_I_MANA_COST = 0x640
AB_N_CHARGES = 0x660
IT_I_CURRENT_CHARGES = 0x6E0
IT_I_MAX_CHARGES = 0x6E8

# ---- controllers ----
M_H_PAWN = 0x6A4
M_ISZ_PLAYER_NAME = 0x6DC
M_B_IS_LOCAL_CONTROLLER = 0x770
M_B_HERO_ASSIGNED = 0x8B9
M_N_PLAYER_ID = 0x908
M_H_ASSIGNED_HERO = 0x90C
M_H_LAST_ASSIGNED_HERO = 0x910

# ---- C_DOTAGamerules ----
GR_N_GAME_STATE = 0x7C
GR_F_GAME_START_TIME = 0x594
GR_F_PRE_GAME_START = 0x590
GR_N_GAME_WINNER = 0x850

# ---- C_DOTA_PlayerResource (schema) ----
# entity designer name usually "dota_player_manager" / "player_manager" / similar
PR_VEC_TEAM_DATA = 0x608       # m_vecPlayerTeamData CUtlVector
PR_VEC_PLAYER_DATA = 0x670     # m_vecPlayerData
PR_ID_TO_PLAYER = 0x760        # m_playerIDToPlayer
PR_ID_TO_PAWN = 0x860          # m_playerIDToPawn
PR_ISZ_NAME = 0x960

# PlayerResourcePlayerTeamData_t
TD_SELECTED_HERO_ID = 0x98
TD_KILLS = 0xA8
TD_ASSISTS = 0xAC
TD_DEATHS = 0xB0
TD_LEVEL = 0xB8
TD_RESPAWN_SECONDS = 0xC0
TD_H_SELECTED_HERO = 0xCC
TD_AFK = 0xD0
TD_TEAM_SLOT = 0x108
# stride candidates (schema max field 0x230)
TD_STRIDE_CANDS = (0x238, 0x240, 0x248, 0x250, 0x230, 0x260, 0x280, 0x200, 0x1E0)

GV_CURTIME_CANDIDATES = (0x2C, 0x30, 0x34, 0x38, 0x40, 0x48, 0x50, 0x58)

TEAMS = {0: "??", 1: "SPEC", 2: "RADIANT", 3: "DIRE", 4: "NEUTRAL"}
WINNER = {0: "-", 2: "RADIANT", 3: "DIRE", 4: "-", 5: "не решён"}
STATE_NAME = {
    0: "INIT", 1: "WAIT", 2: "HERO_SEL", 3: "STRATEGY", 4: "PRE_GAME",
    5: "GAME_IN_PROGRESS", 6: "POST", 7: "DISCONNECT", 8: "TEAM_SHOWCASE",
    9: "CUSTOM_SC", 10: "PLAYER_DRAFT",
}

# partial hero id map (common) — для fallback имени
HERO_ID_NAME = {
    1: "antimage", 2: "axe", 3: "bane", 4: "bloodseeker", 5: "crystal_maiden",
    6: "drow_ranger", 7: "earthshaker", 8: "juggernaut", 9: "mirana", 10: "morphling",
    11: "nevermore", 12: "phantom_lancer", 13: "puck", 14: "pudge", 15: "razor",
    16: "sand_king", 17: "storm_spirit", 18: "sven", 19: "tiny", 20: "vengefulspirit",
    21: "windrunner", 22: "zuus", 23: "kunkka", 25: "lina", 26: "lion",
    27: "shadow_shaman", 28: "slardar", 29: "tidehunter", 30: "witch_doctor",
    31: "lich", 32: "riki", 33: "enigma", 34: "tinker", 35: "sniper",
    36: "necrolyte", 37: "warlock", 38: "beastmaster", 39: "queenofpain", 40: "venomancer",
    41: "faceless_void", 42: "skeleton_king", 43: "death_prophet", 44: "phantom_assassin",
    45: "pugna", 46: "templar_assassin", 47: "viper", 48: "luna", 49: "dragon_knight",
    50: "dazzle", 51: "rattletrap", 52: "leshrac", 53: "furion", 54: "life_stealer",
    55: "dark_seer", 56: "clinkz", 57: "omniknight", 58: "enchantress", 59: "huskar",
    60: "night_stalker", 61: "broodmother", 62: "bounty_hunter", 63: "weaver", 64: "jakiro",
    65: "batrider", 66: "chen", 67: "spectre", 68: "ancient_apparition", 69: "doom_bringer",
    70: "ursa", 71: "spirit_breaker", 72: "gyrocopter", 73: "alchemist", 74: "invoker",
    75: "silencer", 76: "obsidian_destroyer", 77: "lycan", 78: "brewmaster", 79: "shadow_demon",
    80: "lone_druid", 81: "chaos_knight", 82: "meepo", 83: "treant", 84: "ogre_magi",
    85: "undying", 86: "rubick", 87: "disruptor", 88: "nyx_assassin", 89: "naga_siren",
    90: "keeper_of_the_light", 91: "wisp", 92: "visage", 93: "slark", 94: "medusa",
    95: "troll_warlord", 96: "centaur", 97: "magnataur", 98: "shredder", 99: "bristleback",
    100: "tusk", 101: "skywrath_mage", 102: "abaddon", 103: "elder_titan", 104: "legion_commander",
    105: "techies", 106: "ember_spirit", 107: "earth_spirit", 108: "abyssal_underlord",
    109: "terrorblade", 110: "phoenix", 111: "oracle", 112: "winter_wyvern", 113: "arc_warden",
    114: "monkey_king", 119: "dark_willow", 120: "pangolier", 121: "grimstroke",
    123: "hoodwink", 126: "void_spirit", 128: "snapfire", 129: "mars",
    135: "dawnbreaker", 136: "marci", 137: "primal_beast", 138: "muerta",
}

DESIGNER_PREFIXES = (
    "npc_", "dota_", "item_", "ent_", "good_", "bad_", "dota2_",
    "generic_", "prop_", "team_", "shop_",
)

JUNK_NAMES = (
    "player", "world", "worldent", "world_entity", "dota_player_controller",
    "player_manager", "dota_player_manager", "camera", "portrait", "thinker",
    "effigy", "statue", "courier", "observer", "sentry", "ward",
)

TP_NAME_KEYS = ("tpscroll", "tp_scroll", "teleport_scroll", "item_tpscroll", "town_portal")

# Процентные бонусы MR предметов (патч 7.41); бонусы к сопротивлению
# комбинируются мультипликативно. m_flMagicalResistanceValue — базовые проценты,
# к которым отдельно прибавляется 0.1% за итоговый интеллект.
MR_ITEM_BONUSES = {
    "item_cloak": 0.18,
    "item_glimmer_cape": 0.20,
    "item_pipe": 0.20,
    "item_mage_slayer": 0.18,
    "item_shawl": 0.10,
    "item_consecrated_wraps": 0.12,
}
PIPE_AURA_BONUS = 0.08


def compute_mr_from_live_base(base_percent, total_intellect, items):
    """Расчёт базы + INT и MR от предметов; возвращает долю (0.27 = 27%)."""
    base = base_percent / 100.0 + total_intellect * 0.001
    if not math.isfinite(base) or not 0.0 <= base <= 1.0:
        return 0.0
    remaining = 1.0 - base
    has_pipe = False
    for item in items or []:
        bonus = MR_ITEM_BONUSES.get(item)
        if bonus is not None:
            remaining *= (1.0 - bonus)
        if item == "item_pipe":
            has_pipe = True
    # Pipe aura affects its owner too; remote allied Pipe auras cannot be inferred
    # from inventory alone and therefore are not included here.
    if has_pipe:
        remaining *= (1.0 - PIPE_AURA_BONUS)
    return 1.0 - remaining


MAP_LIM = 15000


def ptr_ok(p):
    return bool(p) and 0x10000 <= p < (1 << 48) and p % 8 == 0


def any_ptr(p):
    return bool(p) and 0x10000 <= p < (1 << 48)


def handle_idx(h):
    if h is None:
        return 0
    h = h & 0xFFFFFFFF
    if h == 0 or h == 0xFFFFFFFF:
        return 0
    return h & 0x7FFF


class Dot:
    def __init__(self, proc="dota2.exe"):
        self.pm = pymem.Pymem(proc)
        self.client = self._mod("client.dll")
        self.engine2 = self._mod("engine2.dll")
        self.screen = self._screen_size()
        self.esys = self.hi = 0
        self.vm_off = DW_VIEW_MATRIX
        self._names = {}
        self.curtime_off = None
        self._gv_base = 0
        self._pr_idx = None
        self._pr_ent = None
        self._td_stride = None
        self._td_layout = None  # ('utl', ptr, size) etc
        self._force_log = []
        self._full_scan_fallback_after = 0.0
        self._known_controller_indices = set()

    def _mod(self, name):
        m = module_from_name(self.pm.process_handle, name)
        if m is None:
            raise RuntimeError(f"{name} не найден (запускать от админа?)")
        b = m.lpBaseOfDll
        return int(b, 16) if isinstance(b, str) else b

    @staticmethod
    def _screen_size():
        if ctypes is None:
            return (1920, 1080)
        try:
            u = ctypes.windll.user32
            u.FindWindowW.argtypes = [ctypes.wintypes.LPCWSTR, ctypes.wintypes.LPCWSTR]
            u.FindWindowW.restype = ctypes.wintypes.HWND
            u.GetClientRect.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.RECT)]
            u.GetClientRect.restype = ctypes.wintypes.BOOL
            hw = u.FindWindowW(None, "Dota 2")
            if not hw:
                return (1920, 1080)
            r = ctypes.wintypes.RECT()
            u.GetClientRect(hw, ctypes.byref(r))
            return (r.right - r.left or 1920, r.bottom - r.top or 1080)
        except Exception:
            return (1920, 1080)

    # ---------------- read ----------------
    def rb(self, a, n):
        if not a:
            return None
        try:
            return self.pm.read_bytes(a, n)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            return None

    def u8(self, a):
        r = self.rb(a, 1)
        return r[0] if r else 0

    def u16(self, a):
        r = self.rb(a, 2)
        return struct.unpack("<H", r)[0] if r else 0

    def u32(self, a):
        r = self.rb(a, 4)
        return struct.unpack("<I", r)[0] if r else 0

    def i32(self, a):
        r = self.rb(a, 4)
        return struct.unpack("<i", r)[0] if r else 0

    def f32(self, a):
        r = self.rb(a, 4)
        return struct.unpack("<f", r)[0] if r else 0.0

    def u64(self, a):
        r = self.rb(a, 8)
        return struct.unpack("<Q", r)[0] if r else 0

    # ---------------- strings ----------------
    def cs(self, addr, n=64):
        if not addr:
            return None
        out = bytearray()
        for i in range(0, n, 8):
            part = self.rb(addr + i, 8)
            if not part:
                break
            z = part.find(b"\0")
            out += part if z < 0 else part[:z]
            if z >= 0:
                break
        if len(out) < 2:
            return None
        try:
            s = bytes(out).decode("utf-8", "ignore")
        except Exception:
            return None
        return s if s.isprintable() else None

    def read_name(self, addr):
        p = self.u64(addr)
        if any_ptr(p):
            s = self.cs(p, 64)
            if s:
                return s, "char*"
            p2 = self.u64(p)
            if any_ptr(p2):
                s = self.cs(p2, 64)
                if s:
                    return s, "CUtlString"
            for add in (8, 16, 4):
                s = self.cs(p + add, 64)
                if s and self._looks_name(s):
                    return s, f"char*+{add}"
        s = self.cs(addr, 64)
        if s:
            return s, "inline"
        return None, None

    @staticmethod
    def _looks_name(s):
        if not s or not (2 <= len(s) <= 80) or not s.isprintable():
            return False
        low = s.lower()
        if any(low.startswith(p) for p in DESIGNER_PREFIXES):
            return True
        ok = sum(
            ch.isalnum() or ch in " _-[]().·"
            or ("\u0400" <= ch <= "\u04FF")
            or ("\u4e00" <= ch <= "\u9fff")
            for ch in s
        )
        return ok >= max(2, int(len(s) * 0.55))

    # ---------------- entities ----------------
    def _identity_name(self, ident_base, idx, ep, raw_slot=None):
        """ident_base = address of CEntityIdentity (chunk+i*0x70)."""
        cached = self._names.get(idx)
        if cached and cached[0] == ep:
            return cached[1]
        nm = None
        for noff in (0x20, 0x18, 0x28, 0x30, 0x08):
            if raw_slot is not None and noff + 8 <= len(raw_slot):
                ip = struct.unpack_from("<Q", raw_slot, noff)[0]
            else:
                ip = self.u64(ident_base + noff)
            if not any_ptr(ip):
                continue
            nm, _ = self.read_name(ident_base + noff)
            if nm and self._looks_name(nm):
                break
            nm = None
        self._names[idx] = (ep, nm)
        return nm

    def get_entity(self, idx, ents=None):
        """Достать entity по индексу ДАЖЕ если idx > highestEntityIndex."""
        if ents is not None and idx in ents:
            return ents[idx]
        if not idx or idx < 0 or idx >= 512 * 64:
            return None
        if not self.esys:
            self.esys = self.u64(self.client + DW_GAME_ENTITY_SYSTEM)
        if not ptr_ok(self.esys):
            return None
        b = idx // 512
        i = idx % 512
        cp = self.u64(self.esys + 0x10 + b * 8)
        if not ptr_ok(cp):
            return None
        o = i * 0x70
        ident = cp + o
        raw = self.rb(ident, 0x70)
        if not raw:
            return None
        ep = struct.unpack_from("<Q", raw, 0)[0]
        if not ptr_ok(ep):
            return None
        handle = struct.unpack_from("<I", raw, 0x10)[0] & 0x7FFF
        if handle != idx:
            # soft accept: hero-like fields
            mhp = self.i32(ep + M_I_MAX_HEALTH)
            hid = self.i32(ep + M_I_HERO_ID)
            team = self.u8(ep + M_I_TEAM_NUM)
            lvl = self.i32(ep + M_I_CURRENT_LEVEL)
            if not ((1 <= hid <= 300) or (200 <= mhp <= 12000 and 1 <= lvl <= 30 and team in (2, 3))):
                return None
        nm = self._identity_name(ident, idx, ep, raw)
        return (ep, nm)

    def entities(self, full_scan=True):
        self.esys = self.u64(self.client + DW_GAME_ENTITY_SYSTEM)
        if not ptr_ok(self.esys):
            return None
        self.hi = self.i32(self.esys + DW_HIGHEST_INDEX)
        # The regular feed scan walks only through highestEntityIndex. Heroes
        # assigned above it are still recovered from controller handles via
        # ensure_index() in snapshot(). Full scans remain available for recovery
        # and standalone callers because highestEntityIndex can under-report.
        last_idx = 512 * 64 - 1 if full_scan else max(0, min(512 * 64 - 1, self.hi))
        last_chunk = last_idx // 512
        out = {}
        for b in range(last_chunk + 1):
            cp = self.u64(self.esys + 0x10 + b * 8)
            if not ptr_ok(cp):
                continue
            raw = self.rb(cp, 512 * 0x70)
            if not raw:
                continue
            count = min(512, last_idx - b * 512 + 1)
            for i in range(count):
                idx = b * 512 + i
                o = i * 0x70
                ep = struct.unpack_from("<Q", raw, o)[0]
                if not ptr_ok(ep):
                    continue
                handle = struct.unpack_from("<I", raw, o + 0x10)[0] & 0x7FFF
                if handle != idx:
                    continue
                nm = self._identity_name(cp + o, idx, ep, raw[o:o + 0x70])
                out[idx] = (ep, nm)
        return out

    def _full_scan_retry_allowed(self):
        now = time.monotonic()
        if now < getattr(self, "_full_scan_fallback_after", 0.0):
            return False
        self._full_scan_fallback_after = now + 5.0
        return True

    def ensure_index(self, ents, idx):
        """Гарантирует наличие idx в ents (force get_entity)."""
        if not idx:
            return False
        if idx in ents:
            return True
        got = self.get_entity(idx)
        if got:
            ents[idx] = got
            return True
        return False

    def scene_node(self, ent):
        body = self.u64(ent + M_CBODY_COMPONENT)
        if ptr_ok(body):
            node = self.u64(body + M_P_SCENE_NODE)
            if ptr_ok(node):
                return node
        node = self.u64(ent + M_P_GAME_SCENE_NODE)
        return node if ptr_ok(node) else 0

    def pos(self, ent):
        node = self.scene_node(ent)
        if not node:
            return None
        r = self.rb(node + M_VEC_ABS_ORIGIN, 12)
        return struct.unpack("<fff", r) if r else None

    def dormant_state(self, ent):
        """Tri-state: CGameSceneNode.m_bDormant @0x113 in the supplied schema.
        None = unreadable, NEVER silently treat failed u8 as visible.
        """
        node = self.scene_node(ent)
        if not node:
            return None
        raw = self.rb(node + M_B_SCENE_DORMANT, 1)
        return bool(raw[0]) if raw is not None and len(raw) == 1 and raw[0] in (0, 1) else None

    def is_dormant(self, ent):
        value = self.dormant_state(ent)
        return True if value is None else value

    def life_state(self, ent):
        return self.u8(ent + M_LIFE_STATE)

    def in_map(self, p):
        return bool(p) and all(abs(c) < MAP_LIM for c in p)

    # ---------------- hero validation ----------------
    def is_real_hero(self, ents, idx, ep=None, nm=None):
        """Строго: настоящий npc_dota_hero / hero_id, НЕ player pawn."""
        if not idx:
            return False
        # auto-fetch if missing (idx > hi case)
        if idx not in ents:
            if not self.ensure_index(ents, idx):
                return False
        if ep is None or nm is None:
            ep, nm = ents[idx]
        low = (nm or "").lower()

        # жёсткий бан junk
        if low in JUNK_NAMES or low == "player":
            return False
        if low and not low.startswith("npc_dota_hero"):
            # ещё можно безымянных/другие — проверим поля
            if low.startswith("npc_") and "hero" not in low:
                return False
            if any(j in low for j in ("courier", "thinker", "effigy", "statue",
                                      "portrait", "ward", "tower", "barracks",
                                      "fort", "creep", "building", "shop")):
                return False

        if nm and nm.startswith("npc_dota_hero") and "announcer" not in nm:
            # иллюзии — тоже герои (пометим позже)
            return True

        # без designer: эвристика полей C_DOTA_BaseNPC_Hero
        hero_id = self.i32(ep + M_I_HERO_ID)
        pid = self.i32(ep + M_I_PLAYER_ID_HERO)
        lvl = self.i32(ep + M_I_CURRENT_LEVEL)
        mhp = self.i32(ep + M_I_MAX_HEALTH)
        hp = self.i32(ep + M_I_HEALTH)
        mmana = self.f32(ep + M_FL_MAX_MANA)
        team = self.u8(ep + M_I_TEAM_NUM)

        # явный мусор из лога: lvl=1088 hp=1/100
        if lvl > 30 or lvl < 0:
            return False
        if mhp <= 100 and hp <= 1:
            return False
        if mhp > 20000:
            return False

        score = 0
        if 1 <= hero_id <= 300:
            score += 50
        if 0 <= pid <= 23:
            score += 25
        if 1 <= lvl <= 30:
            score += 15
        if 200 <= mhp <= 12000:
            score += 15
        if mmana >= 40:
            score += 10
        if team in (2, 3):
            score += 10
        if self.u8(ep + M_B_CONSIDERED_HERO):
            score += 10
        if self.in_map(self.pos(ep)):
            score += 5

        # unit name
        un = self.unit_name_raw(ep)
        if un and "hero" in un.lower():
            score += 30
        if un and un.lower() in JUNK_NAMES:
            return False

        return score >= 60

    def unit_name_raw(self, ep):
        p = self.u64(ep + M_ISZ_UNIT_NAME)
        if any_ptr(p):
            s = self.cs(p, 64)
            if s:
                return s
            p2 = self.u64(p)
            if any_ptr(p2):
                s = self.cs(p2, 64)
                if s:
                    return s
        return self.cs(ep + M_ISZ_UNIT_NAME, 64)

    def hero_label(self, ep, nm, hero_id=None):
        if nm and nm.startswith("npc_dota_hero"):
            return nm
        un = self.unit_name_raw(ep)
        if un and ("hero" in un.lower() or un.startswith("npc_")):
            return un if un.startswith("npc_") else f"npc_dota_hero_{un}"
        if hero_id is None:
            hero_id = self.i32(ep + M_I_HERO_ID)
        if 1 <= hero_id <= 300:
            base = HERO_ID_NAME.get(hero_id, f"id{hero_id}")
            return f"npc_dota_hero_{base}"
        return nm or "npc_dota_hero_?"

    # ---------------- time ----------------
    def gamerules(self):
        raw = self.u64(self.client + DW_GAME_RULES)
        for gr, how in ((raw, "ptr"), (self.client + DW_GAME_RULES, "direct")):
            if not ptr_ok(gr):
                continue
            st = self.i32(gr + GR_N_GAME_STATE)
            if 0 <= st <= 12:
                return {
                    "gr": gr, "how": how, "state": st,
                    "start_time": self.f32(gr + GR_F_GAME_START_TIME),
                    "pre_start": self.f32(gr + GR_F_PRE_GAME_START),
                    "winner": self.i32(gr + GR_N_GAME_WINNER),
                }
        return {"gr": raw, "how": "bad", "state": None, "start_time": 0.0,
                "pre_start": 0.0, "winner": None}

    def _resolve_curtime(self, rules=None):
        if self.curtime_off is not None and self._gv_base:
            return self.f32(self._gv_base + self.curtime_off)
        rules = rules or self.gamerules()
        start = rules.get("start_time") or 0.0
        candidates_base = []
        raw = self.u64(self.client + DW_GLOBAL_VARS)
        if ptr_ok(raw):
            candidates_base.append(raw)
        candidates_base.append(self.client + DW_GLOBAL_VARS)
        best = None
        for base in candidates_base:
            for off in GV_CURTIME_CANDIDATES:
                v = self.f32(base + off)
                if not (0.0 < v < 60 * 60 * 6):
                    continue
                sc = 1
                if start > 0 and v >= start - 1.0:
                    sc += 5
                    if 0 <= v - start <= 60 * 180:
                        sc += 5
                if 10 < v < 60 * 120:
                    sc += 2
                cand = (sc, base, off, v)
                if best is None or cand[0] > best[0]:
                    best = cand
        if best and best[0] >= 3:
            self._gv_base, self.curtime_off = best[1], best[2]
            return best[3]
        return start + 1.0 if start > 0 else 0.0

    def curtime(self, rules=None):
        return self._resolve_curtime(rules)

    def hero_liveness(self, ep, ct, pr_row=None):
        """
        FOW-aware alive/dead/respawn.

        В тумане вражеский hp часто = 0, lifeState может врать, а
        m_iRespawnSeconds / m_flRespawnTime залипают на старых значениях
        → ложное «dead:194s» при живом герое на топе.

        Правила:
          ALIVE  — hp>0 и life<2
          DEAD   — явные признаки смерти + валидный таймер респавна
          FOG    — hp<=0 но нет признаков смерти (туман) → не dead
        """
        hp = self.i32(ep + M_I_HEALTH)
        mhp = self.i32(ep + M_I_MAX_HEALTH)
        life = self.life_state(ep)
        rt = self.f32(ep + M_FL_RESPAWN_TIME)          # absolute curtime or 0
        death_t = self.f32(ep + M_FL_DEATH_TIME)
        last_death = self.f32(ep + M_FL_LAST_DEATH_TIME)
        pr_sec = None
        if pr_row is not None:
            v = pr_row.get("respawn_sec")
            if v is not None and 0 <= int(v) <= 300:
                pr_sec = int(v)

        # --- явно жив ---
        if hp > 0 and life < 2:
            return {
                "alive": True, "fog": False, "respawn": 0.0,
                "hp": hp, "maxhp": mhp, "life": life, "why": "hp>0",
            }

        # --- кандидаты remaining ---
        cand = []  # (remaining, source, weight)
        if rt > 1.0:
            if rt > ct + 0.5:
                # absolute game-time
                left = rt - ct
                if 0 < left <= 200:
                    cand.append((left, "rt_abs", 3))
            elif 0 < rt <= 200:
                # already remaining-style
                cand.append((rt, "rt_rem", 2))

        if pr_sec is not None and pr_sec > 0:
            cand.append((float(pr_sec), "pr", 4))

        # death timestamps: if recently dead, remaining can be estimated
        # (only as weak signal)
        for tlab, tval in (("death_t", death_t), ("last_death", last_death)):
            if tval and ct - 300 < tval <= ct + 1:
                # died recently — but without duration we don't know left
                pass

        best_left = 0.0
        best_src = "none"
        best_w = 0
        for left, src, w in cand:
            if 0 < left <= 200 and w >= best_w:
                best_left, best_src, best_w = left, src, w

        # --- признаки реальной смерти ---
        # lifeState: 0 alive, 1 dying, 2 dead (Source)
        life_dead = life >= 2
        # respawn timer actively ticking (must be finite & decreasing-capable)
        timer_active = best_left > 0.5 and best_left <= 120  # normal dota respawn < ~100s early, <180 late
        # late-game max ~100-110s usually; 194s is SUSPICIOUS (≈ curtime-ish garbage)
        timer_sane = best_left > 0.5 and best_left <= 180

        # hp==0 + life dead + sane timer → DEAD
        if (life_dead or hp <= 0) and timer_sane and (life_dead or best_src in ("pr", "rt_abs", "rt_rem")):
            # extra guard: if ONLY pr says 194 and life is 0 and hp 0 — still could be fog
            # Prefer requiring life_dead OR (pr_sec>0 AND rt_abs active)
            strong = life_dead or (best_src in ("rt_abs", "rt_rem") and best_left <= 180)
            pr_only = (best_src == "pr" and not life_dead)
            if strong or (pr_only and best_left <= 120 and hp <= 0):
                # final sanity: 194s at game t=203 with lvl1 is impossible as remaining
                # (buyback/respawn formula ~ early game 5-20s). Cap trust:
                if best_left > 150 and (ct < 600):
                    # early/mid game can't have 150s+ respawn without special modes
                    return {
                        "alive": False, "fog": True, "respawn": 0.0,
                        "hp": max(hp, 0), "maxhp": mhp, "life": life,
                        "why": f"fog_bad_timer({best_src}={best_left:.0f})",
                    }
                return {
                    "alive": False, "fog": False, "respawn": best_left,
                    "hp": 0, "maxhp": mhp, "life": life,
                    "why": f"dead({best_src}={best_left:.1f})",
                }

        # --- hp<=0 but no strong death signal → FOG / unknown ---
        if hp <= 0:
            return {
                "alive": False, "fog": True, "respawn": 0.0,
                "hp": 0, "maxhp": mhp if mhp > 0 else 0, "life": life,
                "why": f"fog(life={life},rt={rt:.1f},pr={pr_sec})",
            }

        # life weird but hp>0
        return {
            "alive": True, "fog": False, "respawn": 0.0,
            "hp": hp, "maxhp": mhp, "life": life, "why": f"life={life}",
        }

    # ---------------- matrix ----------------
    def raw_matrix(self, addr=None):
        addr = self.vm_off if addr is None else addr
        r = self.rb(self.client + addr, 64)
        return list(struct.unpack("<16f", r)) if r else None

    def rescan_view_matrix(self, verbose=True):
        for sig in VM_SIGS:
            try:
                a = self.pm.pattern_scan_module(sig, "client.dll")
            except Exception as e:
                if verbose:
                    print(f"    pattern_scan: {e}")
                return None
            if not a:
                continue
            disp = self.i32(a + 3)
            target = a + 7 + disp
            off = target - self.client
            if verbose:
                print(f"    VM @ {hex(a)} -> +0x{off:X}")
            if 0 < off < 0x8000000:
                return off
        return None

    def w2s(self, m, p):
        if not m or not p:
            return None
        x, y, z = p
        cx = m[0] * x + m[1] * y + m[2] * z + m[3]
        cy = m[4] * x + m[5] * y + m[6] * z + m[7]
        cw = m[12] * x + m[13] * y + m[14] * z + m[15]
        if cw < 0.01:
            return None
        w, h = self.screen
        return (w / 2 * (1 + cx / cw), h / 2 * (1 - cy / cw), cw)

    # ---------------- names ----------------
    def player_name(self, ctrl):
        s = self.cs(ctrl + M_ISZ_PLAYER_NAME, 64)
        if s and self._looks_name(s):
            return s
        p = self.u64(ctrl + M_ISZ_PLAYER_NAME)
        if any_ptr(p):
            s = self.cs(p, 64)
            if s and self._looks_name(s):
                return s
            p2 = self.u64(p)
            if any_ptr(p2):
                s = self.cs(p2, 64)
                if s and self._looks_name(s):
                    return s
        return None

    # ---------------- inventory / TP ----------------
    def _read_handle_array(self, addr, count):
        r = self.rb(addr, count * 4)
        if not r:
            return []
        return list(struct.unpack("<" + "I" * count, r))

    def inventory_handles(self, hero_ep):
        inv = hero_ep + M_INVENTORY
        base = inv + INV_H_ITEMS
        results = []

        # CUtlVector: ptr @0, size @8 or @16
        for sz_off, ptr_off in ((0x10, 0x0), (0x8, 0x0), (0x0, 0x8), (0x0, 0x10), (0x0, 0x18)):
            if ptr_off == 0:
                ptr = self.u64(base)
                size = self.i32(base + sz_off) if sz_off else 0
            else:
                size = self.i32(base + (0 if sz_off == 0 else sz_off))
                ptr = self.u64(base + ptr_off)
                if sz_off == 0 and ptr_off:
                    size = self.i32(base)
            if ptr_ok(ptr) and 1 <= size <= 64:
                hs = self._read_handle_array(ptr, min(size, INV_MAX_SLOTS))
                if hs and sum(1 for h in hs if 0 < handle_idx(h) < 0x4000) >= 1:
                    results.append(hs)

        # network: size@0, ptr@8
        size = self.i32(base)
        if 1 <= size <= 64:
            for add in (8, 16, 24):
                p = self.u64(base + add)
                if ptr_ok(p):
                    hs = self._read_handle_array(p, min(size, INV_MAX_SLOTS))
                    if hs and sum(1 for h in hs if 0 < handle_idx(h) < 0x4000) >= 1:
                        results.append(hs)

        # inline ONLY if looks like handles (not size=small + ffff)
        hs = self._read_handle_array(base, INV_MAX_SLOTS)
        valid = [h for h in hs if 0 < handle_idx(h) < 0x4000]
        # filter: first dword as size (1..30) and rest ffffffff → not inline
        first = hs[0] if hs else 0
        if valid and not (1 <= (first & 0xFFFF) <= 30 and all((h & 0xFFFFFFFF) == 0xFFFFFFFF for h in hs[1:4])):
            # also require some handles with high bits (serial)
            if any((h & 0xFFFF8000) for h in valid):
                results.append(hs)

        best, best_sc = [], -1
        for hs in results:
            sc = sum(1 for h in hs if 0 < handle_idx(h) < 0x4000)
            if sc > best_sc:
                best, best_sc = hs, sc
        return best

    def item_info(self, ents, handle):
        idx = handle_idx(handle)
        if not idx or idx not in ents:
            return None
        ep, nm = ents[idx]
        charges = self.i32(ep + IT_I_CURRENT_CHARGES)
        if charges < 0 or charges > 100:
            charges = self.i32(ep + AB_N_CHARGES)
        if charges < 0 or charges > 100:
            charges = 0
        cd = self.f32(ep + AB_F_COOLDOWN)
        if cd < 0 or cd > 600:
            cd = 0.0
        return {
            "idx": idx, "ent": ep, "name": nm or f"item#{idx}",
            "charges": charges, "cd": cd,
            "cd_len": self.f32(ep + AB_FL_COOLDOWN_LENGTH),
            "mana": self.i32(ep + AB_I_MANA_COST),
            "ready": cd <= 0.05 and charges > 0,
        }

    # ---------------- abilities ----------------
    # m_hAbilities (CUtlVector<CHandle>) — offset гуляет по патчам, поэтому
    # probe один раз и кэш. Ручной оверрайд: --aboff=0x....
    AB_OFF_CANDS = tuple(range(0x0D00, 0x1700, 8))

    @staticmethod
    def _looks_ability_name(s):
        if not s or not (3 <= len(s) <= 64) or not s.isprintable():
            return False
        low = s.lower()
        if low.startswith(("item_", "npc_", "ent_", "prop_", "team_", "shop_",
                           "good_", "bad_", "dota_", "generic_")):
            return False
        if any(j in low for j in ("courier", "ward", "tower", "building",
                                  "modifier", "portrait", "thinker")):
            return False
        return "_" in low and all(c.isalnum() or c in "_-" for c in s)

    def probe_abilities_off(self, ents, ep, hero_designer=None):
        if getattr(self, "_ab_off", None):
            return self._ab_off
        now = time.perf_counter()
        # не нашли ранее — не спамим тяжёлым probe каждый snapshot
        if getattr(self, "_ab_fail_t", 0) and now - self._ab_fail_t < 30:
            return None
        short = (hero_designer or "").replace("npc_dota_hero_", "").lower()
        best, best_sc = None, 0
        for off in self.AB_OFF_CANDS:
            base = ep + off
            ptr, size = self.u64(base), self.i32(base + 8)
            if not (ptr_ok(ptr) and 2 <= size <= 16):
                size, ptr = self.i32(base), self.u64(base + 8)
                if not (ptr_ok(ptr) and 2 <= size <= 16):
                    continue
            sc = 0
            for h in self._read_handle_array(ptr, size):
                idx = handle_idx(h)
                if not idx or idx not in ents:
                    continue
                e2, nm = ents[idx]
                if not self._looks_ability_name(nm or ""):
                    sc -= 2
                    continue
                low = (nm or "").lower()
                if "attribute" in low or low.startswith("special_bonus"):
                    continue
                cdl = self.f32(e2 + AB_FL_COOLDOWN_LENGTH)
                if not (0 <= cdl <= 300):
                    sc -= 1
                    continue
                sc += 2
                if short and short in low:
                    sc += 3
            if sc > best_sc:
                best_sc, best = sc, off
            if best_sc >= 8:
                break
        if best is not None and best_sc >= 6:
            self._ab_off = best
            return best
        self._ab_fail_t = now
        return None

    def abilities_of(self, ents, ep, hero_designer=None):
        """Список способностей героя: name/ent/level/cd/cd_len/mana/charges."""
        off = getattr(self, "_ab_off", None) or M_VEC_ABILITIES
        base = ep + off
        # layout гуляет: ptr/size могут лежать с разным сдвигом
        ptr, size = 0, 0
        for po, so in ((0, 8), (8, 16), (0, 16), (8, 8)):
            p = self.u64(base + po)
            s = self.i32(base + so)
            if ptr_ok(p) and 1 <= s <= 32:
                ptr, size = p, s
                break
        if not ptr:
            return []
        out = []
        for h in self._read_handle_array(ptr, min(size, 32)):
            idx = handle_idx(h)
            if not idx:
                continue
            if idx not in ents:
                self.ensure_index(ents, idx)
            if idx not in ents:
                continue
            e2, nm = ents[idx]
            low = (nm or "").lower()
            if not nm or low.startswith("item_"):
                continue
            if "attribute" in low or "special_bonus" in low:
                continue
            cd = self.f32(e2 + AB_F_COOLDOWN)
            cdl = self.f32(e2 + AB_FL_COOLDOWN_LENGTH)
            ch = self.i32(e2 + AB_N_CHARGES)
            out.append({
                "name": nm, "ent": e2,
                "level": self.i32(e2 + AB_I_LEVEL),
                "mana": self.i32(e2 + AB_I_MANA_COST),
                "cd": cd if 0 <= cd <= 600 else 0.0,
                "cd_len": cdl if 0 <= cdl <= 300 else 0.0,
                "charges": ch if 0 <= ch <= 100 else 0,
                "cast": self.u8(e2 + AB_B_IN_ABILITY_PHASE),
            })
        return out

    def talent_names_of(self, ents, ep):
        """Read talent name, entity and current 0/1 level from the selected ability vector.
        The second reader validates the values again before using them for damage.
        """
        off = getattr(self, "_ab_off", None) or M_VEC_ABILITIES
        base = ep + off
        header = self.rb(base, 24)
        if header is None or len(header) != 24:
            return []
        ptr, size = 0, 0
        for po, so in ((0, 8), (8, 16), (0, 16), (8, 8)):
            p = struct.unpack_from("<Q", header, po)[0]
            s = struct.unpack_from("<i", header, so)[0]
            if ptr_ok(p) and 1 <= s <= 32:
                ptr, size = p, s
                break
        if not ptr:
            return []
        raw = self.rb(ptr, size * 4)
        if raw is None or len(raw) != size * 4:
            return []
        out = []
        for handle in struct.unpack("<" + "I" * size, raw):
            idx = handle_idx(handle)
            if not idx:
                continue
            if idx not in ents:
                self.ensure_index(ents, idx)
            if idx not in ents:
                continue
            talent_ep, name = ents[idx]
            if not name or not name.startswith("special_bonus"):
                continue
            value = self.rb(talent_ep + AB_I_LEVEL, 4)
            level = None
            if value is not None and len(value) == 4:
                parsed = struct.unpack("<i", value)[0]
                if parsed in (0, 1) and self.rb(talent_ep + AB_I_LEVEL, 4) == value:
                    level = parsed
            out.append({"name": name, "ent": talent_ep, "idx": idx,
                        "handle": handle, "level": level})
        # Do not publish a partially read vector. CombatReader revalidates each
        # entity and level once more before accepting either 0 (not learned) or 1.
        if self.rb(base, 24) != header or self.rb(ptr, size * 4) != raw:
            return []
        return out

    def find_tp(self, ents, hero_ep):
        hs = self.inventory_handles(hero_ep)
        tps = []
        for slot, h in enumerate(hs):
            info = self.item_info(ents, h)
            if not info:
                continue
            low = (info["name"] or "").lower()
            is_tp = any(k in low for k in TP_NAME_KEYS) or (
                slot == SLOT_TP and "item_" in low
            )
            if not is_tp:
                if slot == SLOT_TP:
                    is_tp = True
                else:
                    continue
            info = dict(info)
            info["slot"] = slot
            info["is_tp"] = True
            tps.append(info)
        if tps:
            tps.sort(key=lambda x: (0 if x.get("slot") == SLOT_TP else 1,
                                    0 if "tpscroll" in (x["name"] or "") else 1,
                                    -x.get("charges", 0)))
            return tps[0], hs
        if len(hs) > SLOT_TP and not handle_idx(hs[SLOT_TP]):
            return {"name": "empty", "charges": 0, "cd": 0.0, "slot": SLOT_TP,
                    "ready": False, "is_tp": True, "empty": True}, hs
        return None, hs

    # ---------------- PlayerResource ----------------
    def find_player_resource(self, ents):
        if self._pr_ent and self._pr_idx in ents and ents[self._pr_idx][0] == self._pr_ent:
            return self._pr_idx, self._pr_ent
        keys = ("dota_player_manager", "player_manager", "dota_player_resource",
                "player_resource", "dota_playermanager", "playermanager")
        for idx, (ep, nm) in list(ents.items()):
            if not nm:
                continue
            low = nm.lower().replace(" ", "_")
            if any(k == low or k in low for k in keys):
                self._pr_idx, self._pr_ent = idx, ep
                return idx, ep
        # fallback: entity with sensible team-data vector near PR offsets
        best = None
        best_ok = 0
        for idx, (ep, nm) in list(ents.items()):
            if nm and (nm.startswith("npc_") or nm.startswith("item_")):
                continue
            for base_off in (PR_VEC_TEAM_DATA, PR_VEC_TEAM_DATA - 8, PR_VEC_TEAM_DATA + 8,
                             0x5F0, 0x600, 0x610, 0x620):
                for ptr, size, tag in self._utl_vec(ep + base_off):
                    ok = 0
                    for i in range(min(size, 12)):
                        for stride in TD_STRIDE_CANDS:
                            hid = self.i32(ptr + i * stride + TD_SELECTED_HERO_ID)
                            h = handle_idx(self.u32(ptr + i * stride + TD_H_SELECTED_HERO))
                            if 1 <= hid <= 300:
                                ok += 2
                            if h:
                                if h not in ents:
                                    self.ensure_index(ents, h)
                                if h in ents and self.is_real_hero(ents, h):
                                    ok += 3
                    if ok > best_ok:
                        best_ok = ok
                        best = (idx, ep, base_off)
        if best and best_ok >= 6:
            self._pr_idx, self._pr_ent = best[0], best[1]
            return best[0], best[1]
        return None, None

    def _utl_vec(self, base):
        """Return (ptr, size) for CUtlVector-like at base."""
        cands = []
        # ptr@0 size@8
        p, s = self.u64(base), self.i32(base + 8)
        if ptr_ok(p) and 0 < s <= 64:
            cands.append((p, s, "p0s8"))
        # ptr@0 size@16
        p, s = self.u64(base), self.i32(base + 16)
        if ptr_ok(p) and 0 < s <= 64:
            cands.append((p, s, "p0s16"))
        # size@0 ptr@8
        s, p = self.i32(base), self.u64(base + 8)
        if ptr_ok(p) and 0 < s <= 64:
            cands.append((p, s, "s0p8"))
        # size@0 ptr@16
        s, p = self.i32(base), self.u64(base + 16)
        if ptr_ok(p) and 0 < s <= 64:
            cands.append((p, s, "s0p16"))
        return cands

    def player_resource_rows(self, ents):
        """
        list of dict per player slot from m_vecPlayerTeamData:
          hero_idx, hero_id, level, respawn_sec, kills, deaths
        """
        pr_i, pr = self.find_player_resource(ents)
        if not pr:
            return []
        cands = self._utl_vec(pr + PR_VEC_TEAM_DATA)
        best_rows = []
        best_sc = -1
        for ptr, size, tag in cands:
            for stride in TD_STRIDE_CANDS:
                rows = []
                sc = 0
                for i in range(min(size, 24)):
                    base = ptr + i * stride
                    hid = self.i32(base + TD_SELECTED_HERO_ID)
                    lvl = self.i32(base + TD_LEVEL)
                    resp = self.i32(base + TD_RESPAWN_SECONDS)
                    h = handle_idx(self.u32(base + TD_H_SELECTED_HERO))
                    kills = self.i32(base + TD_KILLS)
                    deaths = self.i32(base + TD_DEATHS)
                    # sanity
                    good = 0
                    if 1 <= hid <= 300:
                        good += 2
                    if 0 <= lvl <= 30:
                        good += 1
                    if 0 <= resp <= 300:
                        good += 1
                    if 0 <= kills <= 100 and 0 <= deaths <= 100:
                        good += 1
                    if h and h in ents and self.is_real_hero(ents, h):
                        good += 3
                    if good < 2 and hid == 0 and h == 0:
                        rows.append(None)
                        continue
                    rows.append({
                        "slot": i,
                        "hero_id": hid,
                        "level": lvl,
                        "respawn_sec": resp if 0 <= resp <= 300 else 0,
                        "hero_idx": h if (h and h in ents) else 0,
                        "kills": kills,
                        "deaths": deaths,
                        "raw_handle": self.u32(base + TD_H_SELECTED_HERO),
                    })
                    sc += good
                if sc > best_sc and sc >= 6:
                    best_sc = sc
                    best_rows = rows
                    self._td_stride = stride
                    self._td_layout = (tag, ptr, size)
        return best_rows

    def pr_id_to_pawn(self, ents, pid):
        """m_playerIDToPawn[pid] если массив CHandle."""
        pr_i, pr = self.find_player_resource(ents)
        if not pr or pid is None or not (0 <= pid <= 63):
            return 0
        # try as CHandle[64] at PR_ID_TO_PAWN
        h = handle_idx(self.u32(pr + PR_ID_TO_PAWN + pid * 4))
        if h and h in ents and self.is_real_hero(ents, h):
            return h
        # maybe 8-byte entries
        h = handle_idx(self.u32(pr + PR_ID_TO_PAWN + pid * 8))
        if h and h in ents and self.is_real_hero(ents, h):
            return h
        # m_playerIDToPlayer
        h = handle_idx(self.u32(pr + PR_ID_TO_PLAYER + pid * 4))
        if h and h in ents and self.is_real_hero(ents, h):
            return h
        return 0

    # ---------------- build hero index by player id ----------------
    def index_heroes_by_pid(self, ents):
        """
        pid -> list of (idx, ep, nm, score)
        Сканим ВСЕ сущности: m_iPlayerID / m_nPlayerOwnerID / hero fields.
        """
        by = {}
        for idx, (ep, nm) in list(ents.items()):
            low = (nm or "").lower()
            if low in JUNK_NAMES:
                continue
            if nm == "dota_player_controller":
                continue

            hero_id = self.i32(ep + M_I_HERO_ID)
            pid_hero = self.i32(ep + M_I_PLAYER_ID_HERO)
            pid_owner = self.i32(ep + M_N_PLAYER_OWNER_ID)
            lvl = self.i32(ep + M_I_CURRENT_LEVEL)
            mhp = self.i32(ep + M_I_MAX_HEALTH)
            mmana = self.f32(ep + M_FL_MAX_MANA)
            team = self.u8(ep + M_I_TEAM_NUM)

            is_named = bool(nm and nm.startswith("npc_dota_hero") and "announcer" not in nm)
            if not is_named and not self.is_real_hero(ents, idx, ep, nm):
                continue
            if not is_named and not (1 <= hero_id <= 300):
                continue
            # reject junk numbers
            if lvl > 30 or mhp <= 100:
                continue

            pids = []
            if 0 <= pid_hero <= 23:
                pids.append(pid_hero)
            if 0 <= pid_owner <= 23 and pid_owner not in pids:
                pids.append(pid_owner)
            if not pids:
                # всё ещё герой, но pid не прочитался — положим в -1 bucket
                pids = [-1]

            sc = 0
            if is_named:
                sc += 50
            if 1 <= hero_id <= 300:
                sc += 20
            if not self.u8(ep + M_B_IS_ILLUSION):
                sc += 10
            if mmana >= 40:
                sc += 5
            if team in (2, 3):
                sc += 5
            if handle_idx(self.u32(ep + M_H_REPLICATING)):
                sc -= 30  # illusion of other

            for pid in pids:
                by.setdefault(pid, []).append((sc, idx, ep, nm, hero_id))

        # sort each bucket
        for pid in by:
            by[pid].sort(reverse=True, key=lambda x: x[0])
        return by

    # ---------------- controllers ----------------
    def controllers(self, ents):
        res = {}
        for idx, (ep, nm) in list(ents.items()):
            if not nm:
                continue
            if nm != "dota_player_controller" and "player_controller" not in nm.lower():
                continue
            hero_idx = handle_idx(self.u32(ep + M_H_ASSIGNED_HERO))
            last_idx = handle_idx(self.u32(ep + M_H_LAST_ASSIGNED_HERO))
            pawn_idx = handle_idx(self.u32(ep + M_H_PAWN))
            res[idx] = {
                "ent": ep,
                "pid": self.i32(ep + M_N_PLAYER_ID),
                "hero_assigned": hero_idx,
                "last_idx": last_idx,
                "pawn_idx": pawn_idx,
                "team": self.u8(ep + M_I_TEAM_NUM),
                "local": bool(self.u8(ep + M_B_IS_LOCAL_CONTROLLER)),
                "name": self.player_name(ep),
                "assigned_flag": bool(self.u8(ep + M_B_HERO_ASSIGNED)),
                "designer": nm,
            }
        if res and not any(c["local"] for c in res.values()):
            lp = self.local_player_index()
            if lp is not None:
                for c in res.values():
                    if c["pid"] == lp:
                        c["local"] = True
                        break
        return res

    def local_player_index(self):
        base = self.u64(self.engine2 + DW_NETWORK_GAME_CLIENT)
        if ptr_ok(base):
            v = self.i32(base + OFF_NGC_LOCAL_PLAYER)
            if 0 <= v <= 64:
                return v
        v = self.i32(self.engine2 + DW_NETWORK_GAME_CLIENT + OFF_NGC_LOCAL_PLAYER)
        return v if 0 <= v <= 64 else None

    def resolve_hero_for_ctrl(self, ents, c, by_pid, pr_rows):
        """
        Вернуть (hero_idx, how). Никогда не возвращает junk 'player'.
        """
        pid = c.get("pid")
        tried = []

        def ok(i, tag):
            if not i:
                tried.append(f"{tag}:0")
                return False
            # force pull if not in list (idx > hi)
            if i not in ents:
                self.ensure_index(ents, i)
            if i in ents and self.is_real_hero(ents, i):
                return True
            # diag: what sits at this index?
            if i in ents:
                ep, nm = ents[i]
                hid = self.i32(ep + M_I_HERO_ID)
                tried.append(f"{tag}:{i}({nm or '?'} hid={hid} hp={self.i32(ep+M_I_HEALTH)}/{self.i32(ep+M_I_MAX_HEALTH)})")
            else:
                tried.append(f"{tag}:{i}(NOT_IN_ENTS)")
            return False

        # 1) assigned / last  — ALWAYS force-fetch first
        for i, tag in ((c.get("hero_assigned"), "assigned"),
                       (c.get("last_idx"), "last")):
            if ok(i, tag):
                return i, tag

        # 2) PlayerResource team data by pid/slot
        if pr_rows and pid is not None and 0 <= pid < len(pr_rows):
            row = pr_rows[pid]
            if row and row.get("hero_idx") and ok(row["hero_idx"], "pr_handle"):
                return row["hero_idx"], "pr_handle"

        # 3) m_playerIDToPawn
        i = self.pr_id_to_pawn(ents, pid)
        if ok(i, "pr_pawn"):
            return i, "pr_pawn"

        # 4) scan by m_iPlayerID
        if pid is not None and pid in by_pid:
            for sc, idx, ep, nm, hid in by_pid[pid]:
                if ok(idx, "pid_scan"):
                    return idx, f"pid_scan(sc={sc})"

        # 5) pawn ONLY if real hero
        if ok(c.get("pawn_idx"), "pawn"):
            return c.get("pawn_idx"), "pawn"

        # 6) PR hero_id match: find entity with same hero_id + team
        if pr_rows and pid is not None and 0 <= pid < len(pr_rows):
            row = pr_rows[pid]
            if row and 1 <= row.get("hero_id", 0) <= 300:
                want = row["hero_id"]
                team = c.get("team")
                for idx, (ep, nm) in list(ents.items()):
                    if not self.is_real_hero(ents, idx, ep, nm):
                        continue
                    if self.u8(ep + M_B_IS_ILLUSION):
                        continue
                    if self.i32(ep + M_I_HERO_ID) != want:
                        continue
                    if team in (2, 3) and self.u8(ep + M_I_TEAM_NUM) not in (0, team):
                        if self.u8(ep + M_I_TEAM_NUM) in (2, 3) and self.u8(ep + M_I_TEAM_NUM) != team:
                            continue
                    if ok(idx, "pr_heroid"):
                        return idx, f"pr_heroid({want})"

        # 7) last resort: any unused real hero on same team (from by_pid[-1] or all)
        team = c.get("team")
        cands = []
        for bucket in (by_pid.get(-1, []), by_pid.get(pid, []) if pid is not None else []):
            for sc, idx, ep, nm, hid in bucket:
                cands.append((sc, idx))
        # also scan all real heroes on team not yet obvious
        for idx, (ep, nm) in list(ents.items()):
            if not self.is_real_hero(ents, idx, ep, nm):
                continue
            if self.u8(ep + M_B_IS_ILLUSION):
                continue
            if team in (2, 3) and self.u8(ep + M_I_TEAM_NUM) != team:
                continue
            # skip if already has matching pid owned by someone else
            owner = self.i32(ep + M_I_PLAYER_ID_HERO)
            if 0 <= owner <= 23 and owner != pid:
                continue
            cands.append((10, idx))
        cands.sort(reverse=True)
        seen_i = set()
        for sc, idx in cands:
            if idx in seen_i:
                continue
            seen_i.add(idx)
            if ok(idx, f"team_scan"):
                return idx, f"team_scan(sc={sc})"

        return 0, "FAIL:" + ",".join(tried[:8])

    # ---------------- snapshot ----------------
    def mk_hero(self, idx, ep, nm, c, src, ents, ct, pr_row=None):
        team = self.u8(ep + M_I_TEAM_NUM)
        if c and c.get("team") in (2, 3) and team not in (2, 3):
            team = c["team"]
        ill = bool(self.u8(ep + M_B_IS_ILLUSION))
        if handle_idx(self.u32(ep + M_H_REPLICATING)):
            ill = True

        live = self.hero_liveness(ep, ct, pr_row)
        hp = live["hp"]
        mhp = live["maxhp"]
        # если FOG — maxhp всё равно читаем (для бара), hp показываем как ? через flag
        if mhp <= 0:
            mhp = self.i32(ep + M_I_MAX_HEALTH)
        if live["fog"]:
            # в тумане не доверяем hp=0: берём maxhp для отображения «полного» неизвестного
            # и помечаем fog (print покажет FOG, не dead:194s)
            hp_show = self.i32(ep + M_I_HEALTH)
            mhp_show = mhp
        else:
            hp_show, mhp_show = hp, mhp

        tp, inv_hs = self.find_tp(ents, ep)
        item_names = []
        for hh in inv_hs:
            ii = handle_idx(hh)
            if ii and ii in ents and ents[ii][1]:
                item_names.append(ents[ii][1])
        hero_id = self.i32(ep + M_I_HERO_ID)
        if (not hero_id or hero_id > 300) and pr_row:
            hero_id = pr_row.get("hero_id") or hero_id
        designer = self.hero_label(ep, nm, hero_id)
        lvl = self.i32(ep + M_I_CURRENT_LEVEL)
        if (lvl <= 0 or lvl > 30) and pr_row and 1 <= pr_row.get("level", 0) <= 30:
            lvl = pr_row["level"]

        tp_state = "no_tp"
        if tp:
            if tp.get("empty"):
                tp_state = "empty_slot"
            elif tp.get("charges", 0) <= 0:
                tp_state = "x0"
            elif tp.get("cd", 0) > 0.05:
                tp_state = f"cd{tp['cd']:.0f}"
            else:
                tp_state = "ready"

        abilities = self.abilities_of(ents, ep, designer)
        talents = self.talent_names_of(ents, ep)
        return {
            "idx": idx, "ent": ep, "designer": designer,
            "player": c["name"] if c else None,
            "pid": c["pid"] if c else self.i32(ep + M_I_PLAYER_ID_HERO),
            "team": team,
            "is_local": bool(c and c.get("local")),
            "hp": hp_show, "maxhp": mhp_show,
            "hp_pct": (hp_show / mhp_show * 100) if mhp_show > 0 and not live["fog"] else (
                -1.0 if live["fog"] else 0.0),
            "mana": self.f32(ep + M_FL_MANA),
            "maxmana": self.f32(ep + M_FL_MAX_MANA),
            "level": lvl,
            "alive": live["alive"],
            "fog": live["fog"],
            "life": live["life"],
            "live_why": live["why"],
            "illusion": ill,
            "respawn": live["respawn"] if not live["fog"] else 0.0,
            "hero_id": hero_id,
            "pos": self.pos(ep),
            "src": src,
            "tp": tp,
            "tp_state": tp_state,
            "abilities": abilities,
            "talents": talents,
            "items": item_names,
            "mr": compute_mr_from_live_base(
                self.f32(ep + M_FL_MAGICAL_RESIST),
                self.f32(ep + M_FL_INTELLECT_TOTAL), item_names),
            "inv_n": sum(1 for h in inv_hs if handle_idx(h)),
            "kda": (pr_row.get("kills"), pr_row.get("deaths")) if pr_row else None,
        }

    def overlay_fast_update(self, heroes, my_pos=None, my_team=None):
        """
        Лёгкий тик для оверлея: matrix + pos/hp/mana/life у известных героев.
        Видимость для KS отделена от проекции: поворот камеры не означает FOW.
        Неизвестный dormant-bit запрещает считать цель наблюдаемой.
        """
        if not heroes:
            return heroes, None
        m = self.raw_matrix()
        ct = self.curtime()
        if my_pos is None:
            for h in heroes:
                if h.get("is_local") and h.get("ent"):
                    p = self.pos(h["ent"])
                    if p:
                        my_pos = p
                    break
        out = []
        for h in heroes:
            h = dict(h)
            ep = h.get("ent")
            h["ks_combat_visible"] = False
            h["ks_visibility_known"] = False
            if not ep:
                h["hide"] = True
                out.append(h)
                continue
            try:
                hp = self.i32(ep + M_I_HEALTH)
                mhp = self.i32(ep + M_I_MAX_HEALTH)
                mana = self.f32(ep + M_FL_MANA)
                maxmana = self.f32(ep + M_FL_MAX_MANA)
                lvl = self.i32(ep + M_I_CURRENT_LEVEL)
                if 1 <= lvl <= 30:
                    h["level"] = lvl
                p = self.pos(ep)
                h["pos"] = p
                h["mana"] = mana
                h["maxmana"] = maxmana
                sc = self.w2s(m, p) if (m and p) else None
                h["screen"] = sc
                if my_pos and p:
                    h["dist"] = sum((a - b) ** 2 for a, b in zip(my_pos, p)) ** 0.5

                live = self.hero_liveness(ep, ct, None)
                h["alive"] = live["alive"]
                h["fog"] = live["fog"]
                h["life"] = live["life"]
                h["live_why"] = live["why"]
                h["respawn"] = live["respawn"] if not live["fog"] else 0.0
                if live["fog"]:
                    h["hp"] = hp
                    h["maxhp"] = mhp if mhp > 0 else h.get("maxhp") or 0
                    h["hp_pct"] = -1.0
                else:
                    h["hp"] = live["hp"] if live["hp"] else hp
                    h["maxhp"] = live["maxhp"] if live["maxhp"] else mhp
                    mh = h["maxhp"] or 1
                    h["hp_pct"] = (h["hp"] / mh * 100) if mh > 0 else 0.0

                # Camera projection is NOT game visibility. The old heuristic
                # classified a stationary unit as fog just because the camera moved.
                # Trust the explicitly read dormant bit, fail closed if unavailable.
                dormant_value = self.dormant_state(ep)
                dormant = dormant_value is None or dormant_value
                h["ks_visibility_known"] = dormant_value is not None
                h["ks_combat_visible"] = dormant_value is False and not live["fog"]
                if live["fog"] or dormant:
                    h["fog"] = True
                    h["hide"] = True
                    h["dormant"] = dormant
                else:
                    h["hide"] = False
                    h["dormant"] = False

                if not sc:
                    h["hide"] = True

                tp = h.get("tp")
                if tp and tp.get("ent") and not h["hide"]:
                    try:
                        cd = self.f32(tp["ent"] + AB_F_COOLDOWN)
                        ch = self.i32(tp["ent"] + IT_I_CURRENT_CHARGES)
                        if ch < 0 or ch > 100:
                            ch = self.i32(tp["ent"] + AB_N_CHARGES)
                        tp = dict(tp)
                        tp["cd"] = cd if 0 <= cd <= 600 else 0.0
                        tp["charges"] = ch if 0 <= ch <= 100 else tp.get("charges", 0)
                        tp["ready"] = tp["cd"] <= 0.05 and tp["charges"] > 0
                        h["tp"] = tp
                    except Exception:
                        pass
                abl = h.get("abilities")
                if abl and not h["hide"]:
                    for ab in abl:
                        try:
                            cd = self.f32(ab["ent"] + AB_F_COOLDOWN)
                            if 0 <= cd <= 600:
                                ab["cd"] = cd
                            ab["cast"] = self.u8(ab["ent"] + AB_B_IN_ABILITY_PHASE)
                        except Exception:
                            pass
            except Exception:
                h["hide"] = True
                h["ks_combat_visible"] = False
                h["ks_visibility_known"] = False
            out.append(h)
        return out, my_pos

    def snapshot(self, full_scan=True):
        self._force_log = []
        ents = self.entities(full_scan=full_scan)
        if ents is None:
            return None
        if not full_scan:
            for idx in getattr(self, "_known_controller_indices", ()):
                if idx not in ents:
                    self.ensure_index(ents, idx)
            if not ents and self._full_scan_retry_allowed():
                ents = self.entities(full_scan=True) or {}
        if not ents:
            return None
        rules = self.gamerules()
        ct = self.curtime(rules)
        ctrls = self.controllers(ents)
        if not full_scan and len(ctrls) < 10 and self._full_scan_retry_allowed():
            # Recover controllers that themselves sit beyond highestEntityIndex.
            # Keep this recovery scan bounded so empty/custom lobbies do not
            # monopolize the reader while preserving the rare under-reported-hi case.
            recovered = self.entities(full_scan=True) or {}
            if recovered:
                ents = recovered
                ctrls = self.controllers(ents)
        self._known_controller_indices = set(ctrls)

        # CRITICAL: assigned hero handles часто > highestEntityIndex.
        # Подтягиваем их ДО index_heroes / resolve.
        pulled = []
        for c in ctrls.values():
            for key in ("hero_assigned", "last_idx", "pawn_idx"):
                i = c.get(key) or 0
                if i and i not in ents:
                    if self.ensure_index(ents, i):
                        pulled.append(i)
        if pulled:
            self._force_log.append(f"force-fetch idx>{getattr(self,'hi',0)}: {pulled}")

        by_pid = self.index_heroes_by_pid(ents)
        pr_rows = self.player_resource_rows(ents)

        heroes = []
        seen = set()
        slots = []
        used_hero = set()

        for cidx, c in sorted(ctrls.items(),
                              key=lambda kv: (kv[1].get("team") or 0, kv[1].get("pid") or 0)):
            pid = c.get("pid")
            pr_row = pr_rows[pid] if pr_rows and pid is not None and 0 <= pid < len(pr_rows) else None
            hidx, how = self.resolve_hero_for_ctrl(ents, c, by_pid, pr_rows)

            slot = {
                "ctrl_idx": cidx, "player": c.get("name"), "pid": pid,
                "team": c.get("team"), "local": c.get("local"),
                "resolve": how, "hero_idx": hidx, "hero": None,
                "missing_reason": None, "pr": pr_row,
            }

            if not hidx or hidx not in ents:
                # stub from PlayerResource only
                if pr_row and 1 <= pr_row.get("hero_id", 0) <= 300:
                    hid = pr_row["hero_id"]
                    name = f"npc_dota_hero_{HERO_ID_NAME.get(hid, f'id{hid}')}"
                    stub = {
                        "idx": 0, "ent": 0, "designer": name + " [PR-only]",
                        "player": c.get("name"), "pid": pid,
                        "team": c.get("team") or 0,
                        "is_local": bool(c.get("local")),
                        "hp": 0, "maxhp": 0, "hp_pct": 0.0,
                        "mana": 0, "maxmana": 0,
                        "level": pr_row.get("level") or 0,
                        "alive": pr_row.get("respawn_sec", 0) <= 0,
                        "life": 1, "illusion": False,
                        "respawn": float(pr_row.get("respawn_sec") or 0),
                        "hero_id": hid, "pos": None, "src": "pr_stub",
                        "tp": None, "tp_state": "?", "inv_n": 0,
                        "kda": (pr_row.get("kills"), pr_row.get("deaths")),
                        "screen": None, "dist": None,
                    }
                    # if respawn_sec==0 and we have no entity — still unknown alive
                    if pr_row.get("respawn_sec", 0) > 0:
                        stub["alive"] = False
                    heroes.append(stub)
                    slot["hero"] = stub
                    slot["missing_reason"] = f"no entity, PR hero_id={hid}"
                    self._force_log.append(
                        f"STUB pid={pid} {c.get('name')!r} hero_id={hid} "
                        f"lvl={pr_row.get('level')} respawn={pr_row.get('respawn_sec')} how={how}"
                    )
                else:
                    slot["missing_reason"] = how
                    self._force_log.append(
                        f"MISS pid={pid} {c.get('name')!r} team={c.get('team')} {how} "
                        f"assigned={c.get('hero_assigned')} last={c.get('last_idx')} pawn={c.get('pawn_idx')}"
                    )
                slots.append(slot)
                continue

            if hidx in used_hero:
                # already taken — maybe illusion; try next pid_scan candidate
                alt = None
                if pid in by_pid:
                    for sc, idx, ep, nm, hid in by_pid[pid]:
                        if idx not in used_hero and self.is_real_hero(ents, idx):
                            alt = idx
                            how = f"pid_scan_alt(sc={sc})"
                            break
                if alt:
                    hidx = alt
                else:
                    slot["missing_reason"] = f"dup hero {hidx}"
                    slots.append(slot)
                    continue

            ep, nm = ents[hidx]
            hero = self.mk_hero(hidx, ep, nm, c, how, ents, ct, pr_row)
            heroes.append(hero)
            seen.add(hidx)
            used_hero.add(hidx)
            slot["hero"] = hero
            slots.append(slot)

        # illusions / extra named heroes not in slots
        for idx, (ep, nm) in list(ents.items()):
            if idx in seen or not nm:
                continue
            if not (nm.startswith("npc_dota_hero") and "announcer" not in nm):
                continue
            c = None
            pid = self.i32(ep + M_I_PLAYER_ID_HERO)
            if 0 <= pid <= 23:
                c = next((cc for cc in ctrls.values() if cc.get("pid") == pid), None)
            pr_row = pr_rows[pid] if pr_rows and 0 <= pid < len(pr_rows) else None
            hero = self.mk_hero(idx, ep, nm, c, "extra_named", ents, ct, pr_row)
            heroes.append(hero)
            seen.add(idx)

        # local flag
        me = next((c for c in ctrls.values() if c.get("local")), None)
        if me:
            for h in heroes:
                if h.get("pid") == me.get("pid") or h.get("idx") == me.get("hero_assigned"):
                    h["is_local"] = True

        m = self.raw_matrix()
        my_pos = next((h["pos"] for h in heroes if h.get("is_local") and h.get("pos")), None)
        for h in heroes:
            if h.get("pos") and my_pos:
                h["dist"] = sum((a - b) ** 2 for a, b in zip(my_pos, h["pos"])) ** 0.5
                h["screen"] = self.w2s(m, h["pos"])
            else:
                h["dist"] = None
                h["screen"] = self.w2s(m, h["pos"]) if h.get("pos") else None

        extra = {}
        if "--all" in sys.argv:
            for idx, (ep, nm) in list(ents.items()):
                if idx in seen or nm == "dota_player_controller":
                    continue
                if nm and nm.startswith("item_"):
                    continue
                hp = self.i32(ep + M_I_HEALTH)
                if hp <= 0:
                    continue
                p = self.pos(ep)
                extra[idx] = {
                    "designer": nm, "hp": hp, "maxhp": self.i32(ep + M_I_MAX_HEALTH),
                    "team": self.u8(ep + M_I_TEAM_NUM), "pos": p,
                    "lvl": self.i32(ep + M_I_CURRENT_LEVEL),
                    "dist": (None if not (my_pos and p)
                             else sum((a - b) ** 2 for a, b in zip(my_pos, p)) ** 0.5),
                }

        named = sum(1 for _i, (_e, n) in ents.items() if n)
        named_heroes = sum(1 for _i, (_e, n) in ents.items()
                           if n and n.startswith("npc_dota_hero") and "announcer" not in n)

        return {
            "hi": self.hi, "units": len(ents), "ents": ents, "ctrl": ctrls,
            "heroes": heroes, "slots": slots, "extra": extra, "local": me,
            "rules": rules, "my_pos": my_pos, "matrix": m,
            "named": named, "named_heroes": named_heroes,
            "force_log": list(self._force_log), "curtime": ct,
            "pr_rows": pr_rows, "by_pid_keys": sorted(by_pid.keys()),
            "pr_ent": self._pr_ent, "td_stride": self._td_stride,
        }


# ------------------------------------------------------------------ print
def fmt(v, nd=0):
    return "-" if v is None else f"{v:.{nd}f}"


def wp(p):
    return "-" if not p else f"({p[0]:.0f},{p[1]:.0f},{p[2]:.0f})"


def fmt_tp(tp):
    if not tp:
        return "TP:?"
    if tp.get("empty"):
        return "TP:empty"
    ch = tp.get("charges", 0)
    cd = tp.get("cd") or 0.0
    nm = (tp.get("name") or "?").replace("item_", "")[:14]
    if ch <= 0:
        return f"TP:{nm} x0"          # закончились — норма
    if cd > 0.05:
        return f"TP:{nm} x{ch} cd={cd:.0f}"
    return f"TP:{nm} x{ch} ready"


def print_frame(d, s, tick):
    r = s["rules"]
    st = r.get("state")
    real = [h for h in s["heroes"] if not h.get("illusion")]
    ill = [h for h in s["heroes"] if h.get("illusion")]
    ok = sum(1 for h in real if h.get("idx"))
    stub = sum(1 for h in real if not h.get("idx"))
    with_tp = sum(1 for h in real if h.get("tp") and h["tp"].get("charges", 0) > 0)
    print(
        f"=== tick {tick} | hi={s['hi']} ents={s['units']} named={s.get('named')} "
        f"heroes_named={s.get('named_heroes')} | ctrl={len(s['ctrl'])} | "
        f"героев={len(real)} (entity={ok} stub={stub}) ill={len(ill)} TP_have={with_tp} | "
        f"state={st}({STATE_NAME.get(st, '?')}) t={s.get('curtime', 0):.1f}s "
        f"PR_stride={s.get('td_stride')} ==="
    )

    missing = [sl for sl in s.get("slots", []) if sl.get("hero") is None]
    if missing:
        print(f"  !! без героя: {len(missing)}")
        for sl in missing:
            print(f"     pid={sl.get('pid')} {TEAMS.get(sl.get('team'), '?')} "
                  f"{sl.get('player')!r} | {sl.get('missing_reason')}")

    print(f"  {'':<2} {'team':<8} {'hero':<30} {'player':<14} lvl   hp/maxhp            "
          f"mana         dist   screen       resp  inv  teleport        src")
    for h in sorted(s["heroes"], key=lambda x: (not x.get("is_local"), x.get("team") or 0,
                                                x.get("pid") if x.get("pid") is not None else 99)):
        t = TEAMS.get(h.get("team"), "?")
        mark = ">>" if h.get("is_local") else "  "
        if h.get("fog"):
            st_s = " FOG"
            hp_s = f"  ?/{h.get('maxhp', 0):<6}"
            pct_s = "  FOG "
            resp_s = "   -  "
        elif h.get("alive"):
            st_s = ""
            hp_s = f"{h.get('hp', 0):>5}/{h.get('maxhp', 0):<6}"
            pct_s = f"({h.get('hp_pct', 0):>5.1f}%)"
            resp_s = f"{h.get('respawn', 0):>5.1f}s"
        else:
            st_s = f" dead:{h.get('respawn', 0):.0f}s"
            hp_s = f"{h.get('hp', 0):>5}/{h.get('maxhp', 0):<6}"
            pct_s = f"({h.get('hp_pct', 0):>5.1f}%)"
            resp_s = f"{h.get('respawn', 0):>5.1f}s"
        sc = h.get("screen")
        scr = f"({sc[0]:.0f},{sc[1]:.0f})" if sc else "(off)"
        ill_s = " ILL" if h.get("illusion") else ""
        print(
            f"  {mark:<2} [{t:<7}] {str(h.get('designer') or '?'):<30} "
            f"{str(h.get('player') or '?'):<14} "
            f"{h.get('level', 0):>3}   {hp_s} {pct_s} "
            f"{h.get('mana', 0):>5.0f}/{h.get('maxmana', 0):<5.0f} "
            f"{fmt(h.get('dist')):>6} {scr:<12} "
            f"{resp_s} inv={h.get('inv_n', 0):<2} "
            f"{fmt_tp(h.get('tp')):<16} {h.get('src', '')}{st_s}{ill_s}"
        )

    if s.get("force_log") and ("--force" in sys.argv or "--probe" in sys.argv):
        print("  --- resolve log ---")
        for line in s["force_log"][:24]:
            print(f"    {line}")

    my_team = (s.get("local") or {}).get("team")
    near = [h for h in s["heroes"]
            if h.get("team") in (2, 3) and h.get("team") != my_team
            and not h.get("is_local") and not h.get("illusion")
            and h.get("dist") is not None and h["dist"] < 1500 and h.get("alive")]
    if near:
        print("  >> CLOSE: " +
              ", ".join(f"{h.get('designer') or h.get('player')} {h['dist']:.0f}" for h in near))
    print()


def probe_out(d, s):
    print("\n=== PROBE ===")
    print(f"  curtime={s.get('curtime'):.3f}  PR={hex(s['pr_ent']) if s.get('pr_ent') else None} "
          f"stride={s.get('td_stride')} by_pid={s.get('by_pid_keys')}")
    r = s["rules"]
    print(f"  gamerules state={r.get('state')} start={r.get('start_time'):.1f}")
    print(f"  controllers: {len(s['ctrl'])}")
    for i, c in sorted(s["ctrl"].items(), key=lambda kv: (kv[1].get("team") or 0, kv[1].get("pid") or 0)):
        print(f"    pid={c['pid']:<3} local={int(c['local'])} team={c['team']} "
              f"assigned={c.get('hero_assigned')} last={c.get('last_idx')} "
              f"pawn={c.get('pawn_idx')} name={c['name']!r}")
    if s.get("pr_rows"):
        print(f"  PlayerResource team rows: {len(s['pr_rows'])}")
        for row in s["pr_rows"]:
            if not row:
                print("    (empty)")
                continue
            hn = HERO_ID_NAME.get(row["hero_id"], "?")
            print(f"    slot={row['slot']} hero_id={row['hero_id']}({hn}) "
                  f"lvl={row['level']} respawn={row['respawn_sec']}s "
                  f"K/D={row['kills']}/{row['deaths']} hIdx={row['hero_idx']}")
    print("  resolved:")
    for h in s["heroes"]:
        print(f"    pid={h.get('pid')} {h.get('player')!r} -> {h.get('designer')} "
              f"idx={h.get('idx')} src={h.get('src')} {fmt_tp(h.get('tp'))} "
              f"alive={h.get('alive')} resp={h.get('respawn'):.1f}")
    hero_names = [(i, n) for i, (e, n) in s["ents"].items()
                  if n and n.startswith("npc_dota_hero") and "announcer" not in n]
    print(f"  designer heroes in ents: {len(hero_names)}")
    for i, n in hero_names:
        ep = s["ents"][i][0]
        print(f"    [{i}] {n} hero_id={d.i32(ep + M_I_HERO_ID)} "
              f"pid={d.i32(ep + M_I_PLAYER_ID_HERO)} "
              f"owner={d.i32(ep + M_N_PLAYER_OWNER_ID)} "
              f"ill={d.u8(ep + M_B_IS_ILLUSION)} hp={d.i32(ep + M_I_HEALTH)}")
    # also dump entities with valid hero_id but no name
    ghost = []
    for i, (ep, n) in s["ents"].items():
        if n and n.startswith("npc_dota_hero"):
            continue
        hid = d.i32(ep + M_I_HERO_ID)
        if 1 <= hid <= 300 and d.is_real_hero(s["ents"], i, ep, n):
            ghost.append((i, ep, n, hid))
    print(f"  unnamed real-heroes (by hero_id): {len(ghost)}")
    for i, ep, n, hid in ghost[:16]:
        print(f"    [{i}] nm={n!r} hero_id={hid}({HERO_ID_NAME.get(hid,'?')}) "
              f"pid={d.i32(ep + M_I_PLAYER_ID_HERO)} hp={d.i32(ep + M_I_HEALTH)}/"
              f"{d.i32(ep + M_I_MAX_HEALTH)} lvl={d.i32(ep + M_I_CURRENT_LEVEL)} "
              f"team={d.u8(ep + M_I_TEAM_NUM)}")
    print("=== /PROBE ===\n")


def diag_out(d, s):
    print("\n=== DIAG ===")
    lh = next((h for h in s["heroes"] if h.get("is_local") and h.get("ent")), None)
    if lh and lh.get("ent"):
        inv = lh["ent"] + M_INVENTORY + INV_H_ITEMS
        raw = d.rb(inv, 0x40)
        print(f"  local inv m_hItems @ {hex(inv)}")
        if raw:
            for i in range(0, len(raw), 16):
                print(f"    +{i:02X} " + " ".join(f"{b:02x}" for b in raw[i:i + 16]))
        hs = d.inventory_handles(lh["ent"])
        print(f"  handles ({len(hs)}):")
        for slot, h in enumerate(hs[:INV_MAX_SLOTS]):
            if not handle_idx(h):
                continue
            print(f"    [{slot}] 0x{h:X} -> {d.item_info(s['ents'], h)}")
    if s.get("pr_ent"):
        print(f"  PlayerResource ent={hex(s['pr_ent'])} stride={s.get('td_stride')}")
        raw = d.rb(s["pr_ent"] + PR_VEC_TEAM_DATA, 0x30)
        if raw:
            print("  vecTeamData header:")
            print("   ", " ".join(f"{b:02x}" for b in raw))
    print("=== /DIAG ===\n")


def matrix_sane(m):
    if not m or len(m) != 16:
        return False
    if any(v != v or abs(v) > 1e9 for v in m):
        return False
    return abs(math.sqrt(sum(v * v for v in m[12:15])) - 1.0) < 0.35


def check_offsets(d, auto_fix=True):
    print("\n=== CHECK ===")
    ents = d.entities()
    rows = []
    rows.append(("entities", bool(ents), f"n={len(ents) if ents else 0} hi={d.hi}", ""))
    snap = d.snapshot() if ents else None
    nh = len(snap["heroes"]) if snap else 0
    ok = sum(1 for h in (snap["heroes"] if snap else []) if h.get("idx"))
    rows.append(("heroes resolved", ok >= 1, f"total={nh} with_entity={ok}", ""))
    rows.append(("PlayerResource", bool(snap and snap.get("pr_ent")),
                 f"stride={snap.get('td_stride') if snap else None}", ""))
    m = d.raw_matrix()
    vm = matrix_sane(m)
    if not vm and auto_fix:
        new = d.rescan_view_matrix(True)
        if new:
            d.vm_off = new
            vm = matrix_sane(d.raw_matrix())
    rows.append(("ViewMatrix", vm, hex(d.vm_off), ""))
    bad = 0
    for name, ok_, detail, _ in rows:
        print(f" [{' OK ' if ok_ else 'FAIL'}] {name:<22} {detail}")
        bad += not ok_
    print("ИТОГ:", "ok" if bad == 0 else f"{bad} problems")
    return bad


_hist = []


def verify(d, s):
    lh = next((h for h in s["heroes"] if h.get("is_local")), None)
    if not lh or not lh.get("pos"):
        print("[verify] no local\n")
        return
    sc = lh.get("screen")
    w, h = d.screen
    if sc:
        print(f"[verify] local screen=({sc[0]:.0f},{sc[1]:.0f}) "
              f"d=({sc[0]-w/2:+.0f},{sc[1]-h/2:+.0f})")
    print()


# =====================================================================
# OVERLAY — прозрачное click-through окно поверх Dota (tkinter + Win32)
# =====================================================================
# Почему tkinter: уже в Python, без pip. Для ESP этого достаточно.
# Альтернативы тяжелее: pygame/Qt/dearpygui/ImGui — тоже ок, но лишние deps.
# Win32: WS_EX_LAYERED + WS_EX_TRANSPARENT + TOPMOST = клики проходят в игру.

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
LWA_COLORKEY = 0x00000001
HWND_TOPMOST = -1
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040


class Overlay:
    """
    Быстрый оверлей (как vision.py):
      • background reader-thread: full snapshot редко + fast HP/pos каждый кадр
      • UI thread: только draw, и только если frame_id изменился
      • canvas items кэшируются (move/coords), без delete("all") каждый кадр
    """

    def __init__(self, dot, fps=60, console=False, enemies_only=False, show_local=True,
                 full_every=2.5):
        self.d = dot
        self.ui_fps = max(15, min(144, int(fps)))
        self.console = console
        self.enemies_only = enemies_only
        self.show_local = show_local
        # full snapshot тяжелый — реже = меньше «тикающих» подвисаний
        self.full_every = max(0.8, float(full_every))
        self.tick = 0
        self.fails = 0
        self._last_err = None
        self._hwnd_dota = None
        self._client_off = (0, 0)
        self._running = True
        self._closing = False
        self._after_id = None

        # shared state (reader → UI), как vision.frame_id
        import threading
        self._lock = threading.Lock()
        self.frame_id = 0
        self.read_fps = 0.0
        self._snap = None          # last published snapshot-like dict
        self._heroes_base = []     # from last FULL snapshot (ents/names/tp handles)
        self._my_pos = None
        self._my_team = None
        self._rules = {}
        self._curtime = 0.0
        self._last_drawn_fid = -1
        self._geom_w = 0
        self._geom_h = 0
        self._geom_check_t = 0.0
        self._item_cache = {}      # hero_idx -> canvas item ids dict

        try:
            import tkinter as tk
        except ImportError as e:
            raise RuntimeError("tkinter недоступен") from e
        self.tk = tk
        if ctypes is None:
            raise RuntimeError("ctypes/Win32 нужны для click-through оверлея")

        self.root = tk.Tk()
        self.root.title("dota_esp_overlay")
        self.root.geometry("800x600+0+0")
        self.root.configure(bg="#010101")
        self.root.attributes("-topmost", True)
        try:
            self.root.attributes("-transparentcolor", "#010101")
        except Exception:
            pass
        self.root.overrideredirect(True)
        self.root.resizable(False, False)
        self.cv = tk.Canvas(
            self.root, bg="#010101", highlightthickness=0,
            bd=0, width=800, height=600,
        )
        self.cv.pack(fill="both", expand=True)
        # static bg once
        self._bg_id = self.cv.create_rectangle(0, 0, 800, 600, fill="#010101", outline="", tags="bg")
        self._hud_sh = self.cv.create_text(8, 8, text="", fill="#000000", anchor="nw",
                                           font=("Consolas", 9, "bold"), tags="hud")
        self._hud_id = self.cv.create_text(7, 7, text="", fill="#c0c0c0", anchor="nw",
                                           font=("Consolas", 9, "bold"), tags="hud")
        self._err_id = self.cv.create_text(7, 24, text="", fill="#ff6666", anchor="nw",
                                           font=("Consolas", 8), tags="hud")

        self.root.update_idletasks()
        self._apply_win32()

        self._vk_end = 0x23
        self._vk_f8 = 0x77
        self._vk_f9 = 0x78
        self._vk_f7 = 0x76
        self._prev_keys = {}

        # start reader thread BEFORE ui loop
        self._reader_thr = threading.Thread(target=self._reader_loop, daemon=True, name="esp-reader")
        self._reader_thr.start()
        self.root.after(16, self._ui_loop)

    # ---- Win32 ----
    def _user32(self):
        return ctypes.windll.user32

    def _find_dota(self):
        u = self._user32()
        hw = u.FindWindowW(None, "Dota 2")
        if hw:
            return hw
        self._hwnd_dota = None

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        def _enum(hwnd, _lp):
            buf = ctypes.create_unicode_buffer(256)
            u.GetWindowTextW(hwnd, buf, 256)
            t = buf.value
            if t and "dota" in t.lower() and u.IsWindowVisible(hwnd):
                self._hwnd_dota = hwnd
                return False
            return True
        u.EnumWindows(_enum, 0)
        return self._hwnd_dota

    def _dota_client_rect_screen(self):
        u = self._user32()
        hw = self._find_dota()
        if not hw:
            w = u.GetSystemMetrics(0)
            h = u.GetSystemMetrics(1)
            return 0, 0, w, h
        r = ctypes.wintypes.RECT()
        u.GetClientRect(hw, ctypes.byref(r))
        pt = ctypes.wintypes.POINT(0, 0)
        u.ClientToScreen(hw, ctypes.byref(pt))
        return pt.x, pt.y, max(1, r.right - r.left), max(1, r.bottom - r.top)

    def _apply_win32(self):
        u = self._user32()
        hwnd = int(self.root.winfo_id())
        try:
            root = u.GetAncestor(hwnd, 2)
            if root:
                hwnd = root
            else:
                parent = u.GetParent(hwnd)
                if parent:
                    hwnd = parent
        except Exception:
            pass
        self._hwnd_overlay = hwnd
        style = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        u.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        try:
            u.SetLayeredWindowAttributes(hwnd, 0x00010101, 0, LWA_COLORKEY)
        except Exception:
            pass
        u.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                       SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)

    def _sync_geometry(self, force=False):
        now = time.perf_counter()
        # geometry rarely changes — check ~2 Hz
        if not force and (now - self._geom_check_t) < 0.5 and self._geom_w:
            return self._geom_w, self._geom_h
        self._geom_check_t = now
        x, y, w, h = self._dota_client_rect_screen()
        self._client_off = (x, y)
        if (w, h) != tuple(self.d.screen):
            self.d.screen = (w, h)
        if w != self._geom_w or h != self._geom_h or force:
            try:
                self.root.geometry(f"{w}x{h}+{x}+{y}")
                self.cv.config(width=w, height=h)
                self.cv.coords(self._bg_id, 0, 0, w, h)
            except Exception:
                pass
            self._geom_w, self._geom_h = w, h
        try:
            self._user32().SetWindowPos(self._hwnd_overlay, HWND_TOPMOST, 0, 0, 0, 0,
                                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        except Exception:
            pass
        return w, h

    def _key_click(self, vk):
        u = self._user32()
        down = bool(u.GetAsyncKeyState(vk) & 0x8000)
        prev = self._prev_keys.get(vk, False)
        self._prev_keys[vk] = down
        return down and not prev

    # ---- reader thread (heavy work OFF ui) ----
    def _publish(self, heroes, my_pos, my_team, rules, ct, snap=None):
        with self._lock:
            self._heroes_base = heroes
            if my_pos is not None:
                self._my_pos = my_pos
            if my_team is not None:
                self._my_team = my_team
            if rules is not None:
                self._rules = rules
            self._curtime = ct
            self._snap = snap or {
                "heroes": heroes,
                "local": {"team": my_team},
                "rules": rules or {},
                "curtime": ct,
            }
            self.frame_id += 1

    def _reader_loop(self):
        """
        Всегда FAST каждый тик. FULL — редко и ПОСЛЕ fast-publish,
        чтобы UI не замирал на время entity-scan.
        """
        try:
            ctypes.windll.kernel32.SetThreadPriority(
                ctypes.windll.kernel32.GetCurrentThread(), 1)
        except Exception:
            pass
        last_full = 0.0
        fps_s = 0.0
        full_pending = True  # first tick needs full
        while self._running and not self._closing:
            t0 = time.perf_counter()
            try:
                with self._lock:
                    base = list(self._heroes_base)
                    my_pos = self._my_pos
                    my_team = self._my_team
                    rules = self._rules

                # 1) FAST always (smooth bars) if we already have base
                if base:
                    heroes, my_pos2 = self.d.overlay_fast_update(base, my_pos, my_team)
                    ct = self.d.curtime()
                    self._publish(heroes, my_pos2 if my_pos2 is not None else my_pos,
                                  my_team, rules, ct)
                    base = heroes
                    if my_pos2 is not None:
                        my_pos = my_pos2
                    self.fails = 0

                # 2) FULL rarely — after a fast publish so hitch is only on names/slots
                need_full = full_pending or (t0 - last_full) >= self.full_every or not base
                if need_full:
                    s = self.d.snapshot()
                    last_full = time.perf_counter()
                    full_pending = False
                    if s:
                        heroes = [dict(h) for h in s.get("heroes", []) if not h.get("illusion")]
                        prev = {h.get("idx"): h for h in base if h.get("idx")}
                        for h in heroes:
                            old = prev.get(h.get("idx"))
                            if old:
                                if old.get("_last_pos") is not None:
                                    h["_last_pos"] = old["_last_pos"]
                                    h["_last_pos_t"] = old.get("_last_pos_t")
                                    h["_last_sc"] = old.get("_last_sc")
                        my_team = (s.get("local") or {}).get("team")
                        heroes, my_pos2 = self.d.overlay_fast_update(
                            heroes, s.get("my_pos"), my_team)
                        self._publish(
                            heroes,
                            my_pos2 if my_pos2 is not None else s.get("my_pos"),
                            my_team,
                            s.get("rules") or {},
                            s.get("curtime") or self.d.curtime(),
                            snap=s,
                        )
                        self.fails = 0
                    else:
                        self.fails += 1
                        if not base:
                            time.sleep(0.15)
            except KeyboardInterrupt:
                break
            except Exception as e:
                self._last_err = f"reader: {e}"
                time.sleep(0.05)
                continue

            dt = time.perf_counter() - t0
            inst = 1.0 / max(dt, 1e-4)
            # if we just did full, dt is huge — don't destroy EMA
            if dt < 0.05:
                fps_s = inst if fps_s == 0 else (0.2 * inst + 0.8 * fps_s)
            self.read_fps = fps_s
            # ~90-120 Hz fast path; full ticks naturally slower
            sleep_t = max(0.0, (1.0 / 90.0) - dt) if dt < 0.05 else 0.002
            time.sleep(sleep_t if sleep_t > 0.0004 else 0.001)

    # ---- draw helpers (update in place) ----
    def _short_hero(self, name):
        if not name:
            return "?"
        s = str(name).replace("npc_dota_hero_", "").replace("_", " ").replace(" [PR-only]", "")
        return s[:14]

    def _ensure_items(self, key):
        c = self._item_cache.get(key)
        if c:
            return c
        cv = self.cv
        # меньше объектов = меньше лагов в 5v5 (без name shadow / tp oval text)
        c = {
            "name": cv.create_text(-100, -100, text="", fill="#fff",
                                   font=("Segoe UI", 8, "bold"), anchor="s", tags="dyn"),
            "hp_bg": cv.create_rectangle(-100, -100, -99, -99, fill="#222", outline="#000", tags="dyn"),
            "hp_fg": cv.create_rectangle(-100, -100, -99, -99, fill="#3cff3c", outline="", tags="dyn"),
            "hp_tx": cv.create_text(-100, -100, text="", fill="#fff",
                                    font=("Segoe UI", 7, "bold"), tags="dyn"),
            "mp_bg": cv.create_rectangle(-100, -100, -99, -99, fill="#1a1a2e", outline="#000", tags="dyn"),
            "mp_fg": cv.create_rectangle(-100, -100, -99, -99, fill="#4080ff", outline="", tags="dyn"),
            "state": "shown",
            "last_txt": "",
            "last_hp": None,
        }
        self._item_cache[key] = c
        return c

    def _hide_items(self, c):
        if c.get("state") == "hidden":
            return
        c["state"] = "hidden"
        for k, iid in c.items():
            if k in ("state", "last_txt", "last_hp"):
                continue
            try:
                if k in ("hp_bg", "hp_fg", "mp_bg", "mp_fg"):
                    self.cv.coords(iid, -100, -100, -99, -99)
                else:
                    self.cv.coords(iid, -100, -100)
            except Exception:
                pass

    def _draw_hero_update(self, h, sw, sh, my_team):
        key = h.get("idx") or id(h)
        c = self._ensure_items(key)

        # FOG / stale / explicit hide → убрать бар (не оставлять ghost)
        if h.get("hide") or h.get("fog"):
            self._hide_items(c)
            return

        sc = h.get("screen")
        if not sc:
            self._hide_items(c)
            return
        x, y = sc[0], sc[1] - 55
        if x < -40 or y < -40 or x > sw + 40 or y > sh + 40:
            self._hide_items(c)
            return

        team = h.get("team")
        is_enemy = my_team in (2, 3) and team in (2, 3) and team != my_team
        is_local = h.get("is_local")
        if self.enemies_only and not is_enemy and not is_local:
            self._hide_items(c)
            return
        if is_local and not self.show_local:
            self._hide_items(c)
            return

        c["state"] = "shown"
        if not h.get("alive"):
            hp_col, name_col = "#555555", "#ff6666"
        elif is_local:
            hp_col, name_col = "#3cff3c", "#a0ffa0"
        elif is_enemy:
            hp_col, name_col = "#ff3c3c", "#ffb0b0"
        else:
            hp_col, name_col = "#3ca0ff", "#b0d8ff"

        bw, bh = 84, 6
        x0 = x - bw / 2
        label = self._short_hero(h.get("designer"))
        lvl = h.get("level") or 0
        # короче текст в thrash-файтах
        top = f"{label} [{lvl}]"

        self.cv.coords(c["name"], x, y - 10)
        if c.get("last_txt") != top:
            self.cv.itemconfigure(c["name"], text=top, fill=name_col)
            c["last_txt"] = top
        else:
            self.cv.itemconfigure(c["name"], fill=name_col)

        self.cv.coords(c["hp_bg"], x0, y, x0 + bw, y + bh)
        if not h.get("alive"):
            self.cv.coords(c["hp_fg"], x0 + 1, y + 1, x0 + 1, y + bh - 1)
            self.cv.itemconfigure(c["hp_fg"], fill=hp_col)
            rs = h.get("respawn") or 0
            txt = f"DEAD {rs:.0f}s"
            self.cv.coords(c["hp_tx"], x, y + bh / 2)
            if c.get("last_hp") != txt:
                self.cv.itemconfigure(c["hp_tx"], text=txt)
                c["last_hp"] = txt
        else:
            mhp = h.get("maxhp") or 1
            hp = h.get("hp") or 0
            frac = max(0.0, min(1.0, hp / mhp if mhp else 0))
            fw = max(0.0, (bw - 2) * frac)
            self.cv.coords(c["hp_fg"], x0 + 1, y + 1, x0 + 1 + fw, y + bh - 1)
            self.cv.itemconfigure(c["hp_fg"], fill=hp_col)
            # текст HP обновляем реже (целое hp) — меньше itemconfigure
            txt = f"{int(hp)}/{int(mhp)}"
            self.cv.coords(c["hp_tx"], x, y + bh / 2)
            if c.get("last_hp") != txt:
                self.cv.itemconfigure(c["hp_tx"], text=txt)
                c["last_hp"] = txt

        mm = h.get("maxmana") or 0
        if mm > 1 and h.get("alive"):
            mana = h.get("mana") or 0
            frac = max(0.0, min(1.0, mana / mm))
            my = y + bh + 1
            self.cv.coords(c["mp_bg"], x0, my, x0 + bw, my + 4)
            self.cv.coords(c["mp_fg"], x0 + 1, my + 1, x0 + 1 + (bw - 2) * frac, my + 3)
        else:
            self.cv.coords(c["mp_bg"], -10, -10, -9, -9)
            self.cv.coords(c["mp_fg"], -10, -10, -9, -9)

    def _render(self, heroes, rules, curtime, my_team, sw, sh):
        # hide stale keys
        live_keys = set()
        for h in heroes:
            if h.get("illusion"):
                continue
            key = h.get("idx") or id(h)
            live_keys.add(key)
            try:
                self._draw_hero_update(h, sw, sh, my_team)
            except Exception as e:
                self._last_err = f"draw:{e}"
        for k, c in list(self._item_cache.items()):
            if k not in live_keys:
                self._hide_items(c)

        vis = [h for h in heroes if not h.get("hide") and not h.get("fog")]
        n = len(heroes)
        fog_n = sum(1 for h in heroes if h.get("fog") or h.get("hide"))
        dead_n = sum(1 for h in heroes if not h.get("alive") and not h.get("fog"))
        st = (rules or {}).get("state")
        info = (f"ESP v13  on={len(vis)}/{n} fog={fog_n}  "
                f"t={curtime:.0f}s  read={self.read_fps:.0f}  "
                f"{'ENEMY ' if self.enemies_only else ''}"
                f"[F7][F8][END]")
        self.cv.itemconfigure(self._hud_sh, text=info)
        self.cv.itemconfigure(self._hud_id, text=info)
        self.cv.itemconfigure(self._err_id, text=(f"err: {self._last_err}" if self._last_err else ""))

    def _shutdown(self, reason=""):
        if self._closing:
            return
        self._closing = True
        self._running = False
        if reason:
            print(f"[overlay] exit: {reason}")
        try:
            if self._after_id:
                self.root.after_cancel(self._after_id)
        except Exception:
            pass
        try:
            self.root.quit()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def _ui_loop(self):
        """UI thread: hotkeys + draw only when reader published new frame_id."""
        if self._closing or not self._running:
            return
        try:
            if self._key_click(self._vk_end):
                self._shutdown("END")
                return
            if self._key_click(self._vk_f8):
                self.enemies_only = not self.enemies_only
                self._last_drawn_fid = -1  # force redraw
            if self._key_click(self._vk_f9):
                self.console = not self.console
            if self._key_click(self._vk_f7):
                self.show_local = not self.show_local
                self._last_drawn_fid = -1

            sw, sh = self._sync_geometry()
            with self._lock:
                fid = self.frame_id
                heroes = list(self._heroes_base)
                rules = self._rules
                ct = self._curtime
                my_team = self._my_team
                snap = self._snap

            if fid != self._last_drawn_fid:
                self._last_drawn_fid = fid
                self.tick += 1
                if heroes:
                    self._render(heroes, rules, ct, my_team, sw, sh)
                    self.fails = 0
                else:
                    self.fails += 1
                    self.cv.itemconfigure(self._hud_id, text="no heroes (menu/loading?)")
                if self.console and snap and (self.tick % 30 == 1):
                    try:
                        print_frame(self.d, snap, self.tick)
                    except Exception:
                        pass
        except KeyboardInterrupt:
            self._shutdown("Ctrl+C")
            return
        except Exception as e:
            self._last_err = str(e)

        if self._closing or not self._running:
            return
        delay = max(8, int(1000 / self.ui_fps))  # UI poll ~fps, draw gated by frame_id
        try:
            self._after_id = self.root.after(delay, self._ui_loop)
        except Exception:
            pass

    def run(self):
        print("[overlay] START v11.1 — fast always, full rare, hide FOG ghosts")
        print("[overlay] END=quit  F7=local  F8=enemies  F9=console")
        print("[overlay] Dota: borderless/windowed (not exclusive FS)")
        print(f"[overlay] full every {self.full_every:.1f}s | UI {self.ui_fps}fps | fast~90Hz")
        try:
            self.root.protocol("WM_DELETE_WINDOW", lambda: self._shutdown("close"))
        except Exception:
            pass
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            self._shutdown("Ctrl+C")
        finally:
            self._running = False
            self._closing = True
            try:
                self.root.destroy()
            except Exception:
                pass
        print("[overlay] stopped")


def main():
    d = Dot()
    print(f"[+] dota_esp v13 | client={hex(d.client)} engine2={hex(d.engine2)} "
          f"screen={d.screen[0]}x{d.screen[1]}")
    print("[+] v13: FOG hide | threaded overlay")
    if "--check" in sys.argv:
        check_offsets(d)
        return

    if "--overlay" in sys.argv or "-o" in sys.argv:
        fps = 60
        full_every = 2.5
        for a in sys.argv:
            if a.startswith("--fps="):
                try:
                    fps = int(a.split("=", 1)[1])
                except Exception:
                    pass
            if a.startswith("--full="):
                try:
                    full_every = float(a.split("=", 1)[1])
                except Exception:
                    pass
        console = "--console" in sys.argv or "--log" in sys.argv
        enemies = "--enemies" in sys.argv
        for a in sys.argv:
            if a.startswith("--aboff="):
                try:
                    d._ab_off = int(a.split("=", 1)[1], 16)
                except Exception:
                    pass
        # Новый ImGui-оверлей (esp_ui.py) — если зависимости стоят и не --tk
        if "--tk" not in sys.argv:
            try:
                import esp_ui
                ov = esp_ui.ImGuiOverlay(d, fps=fps, full_every=full_every,
                                         enemies_only=enemies, show_local=True)
                ov.run()
                return
            except KeyboardInterrupt:
                print("\n[overlay] stopped (KeyboardInterrupt)")
                return
            except (Exception, SystemExit) as e:
                print(f"[overlay] imgui недоступен ({e}) -> fallback tkinter")
        try:
            ov = Overlay(d, fps=fps, console=console, enemies_only=enemies,
                        show_local=True, full_every=full_every)
            ov.run()
        except KeyboardInterrupt:
            print("\n[overlay] stopped (KeyboardInterrupt)")
        except Exception as e:
            import traceback
            print(f"[overlay] failed: {e}")
            traceback.print_exc()
            print("Fallback: console mode")
        return

    tick = 0
    fails = 0
    while True:
        try:
            s = d.snapshot()
            if not s:
                fails += 1
                if fails == 20:
                    print("[!] no entities — restart script")
                time.sleep(0.5)
                continue
            fails = 0
            tick += 1
            if "--probe" in sys.argv and tick == 1:
                probe_out(d, s)
            if "--diag" in sys.argv and tick <= 2:
                diag_out(d, s)
            print_frame(d, s, tick)
            if "--verify" in sys.argv:
                verify(d, s)
            time.sleep(0.7)
        except KeyboardInterrupt:
            print("\n[*] stop")
            break
        except Exception as e:
            import traceback
            print(f"[-] {e}")
            traceback.print_exc()
            time.sleep(1.0)


if __name__ == "__main__":
    main()
