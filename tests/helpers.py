import ast
from pathlib import Path
from types import SimpleNamespace
from ks_core import Settings, Unit, Action

ROOT = Path(__file__).resolve().parents[1]


def settings():
    return Settings(ROOT / 'ks_profiles.json')


def fixture(at=10.):
    action = Action('lion_finger_of_death', 300, 1, 0., 200)
    me = Unit(1, 100, 'npc_dota_hero_lion', 2, True, True, 1000, 675, .25, 0,
              (0, 0), (800, 500), at, actions=(action,), intellect=100,
              stack_values=(('CDOTA_Modifier_Lion_FingerOfDeath_Kill_Counter',0),),
              talent_levels=(('special_bonus_unique_lion_8',0),))
    enemy = Unit(2, 200, 'npc_dota_hero_bounty_hunter', 3, False, True, 150, 100, .27, 0,
                 (500, 0), (1000, 500), at, motion_known=True, stationary_for=1.)
    return me, enemy


def fake_module():
    attrs = {}
    for node in ast.parse((ROOT / 'dota_esp.py').read_text(encoding='utf-8')).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            try:
                attrs[node.targets[0].id] = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                pass
    attrs['handle_idx'] = lambda h: 0 if h in (0, 0xFFFFFFFF) else h & 0x7FFF
    attrs['compute_mr_from_live_base'] = lambda base, intellect, items: base / 100 + intellect * .001
    return SimpleNamespace(**attrs)
