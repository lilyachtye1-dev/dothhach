"""Synthetic offline check; does not import pymem, Win32 or send input."""
from pathlib import Path
from ks_core import Settings, Unit, Action, choose

s = Settings(Path(__file__).with_name('ks_profiles.json'))
a = Action('lion_finger_of_death', 300, 1, 0., 200)
me = Unit(1, 100, 'npc_dota_hero_lion', 2, True, True, 1000, 675, .25, 0,
          (0, 0), (800, 500), 10., actions=(a,), intellect=100,
          stack_values=(('CDOTA_Modifier_Lion_FingerOfDeath_Kill_Counter',3),),
          talent_levels=(('special_bonus_unique_lion_8',1),))
enemy = Unit(2, 200, 'npc_dota_hero_bounty_hunter', 3, False, True, 150, 100, .27, 0,
             (500, 0), (1000, 500), 10., motion_known=True, stationary_for=1.)
notes = []
d = choose((me, enemy), s, 10.1, (1920, 1080), notes)
print('SYNTHETIC ONLY / no game access / no input')
print('Enabled profiles:', len(s.profiles))
print('Synthetic stacks: 3; talent learned: yes; raw bonus: 3 * (25 + 20) = 135')
if d:
    print(f'{d.profile.name} -> {d.target.name}: estimated {d.damage:.0f}, needed {d.required:.0f}, key VK {d.key}')
else:
    print('; '.join(notes))
