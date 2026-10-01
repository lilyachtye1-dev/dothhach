import copy
from dataclasses import replace
import json
import math
import tempfile
from pathlib import Path
import unittest

from ks_core import Settings, Action, Toggle, CastLedger, choose, keycode, raw_damage, raw_damage_details
from helpers import ROOT, settings, fixture


class ConfigTests(unittest.TestCase):
    def load_changed(self, callback):
        doc = json.loads((ROOT / 'ks_profiles.json').read_text())
        callback(doc)
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'profiles.json'
            p.write_text(json.dumps(doc))
            return Settings(p)

    def test_catalogue(self):
        s = settings()
        self.assertEqual(len(s.profiles), 29)
        self.assertEqual(len(s.disabled), 19)
        self.assertEqual(s.toggle_vk, 0)

    def test_named_keys(self):
        for text, vk in [('R', 82), ('q', 81), ('F6', 117), ('F12', 123), ('NONE', 0)]:
            self.assertEqual(keycode(text), vk)
        for bad in ['Alt+Q', 'F13', 'Enter', True, -1, 0xBA]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                keycode(bad)

    def test_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'p.json'
            p.write_text('{"schema_version":1,"schema_version":1}')
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                Settings(p)

    def test_unsupported_template_cannot_be_enabled(self):
        def change(d):
            next(row for row in d['abilities'] if row['model'] == 'unsupported')['enabled'] = True
        with self.assertRaisesRegex(ValueError, 'only flat'):
            self.load_changed(change)

    def test_public_741f_catalogue_is_prefilled_but_disabled(self):
        doc = json.loads((ROOT / 'ks_profiles.json').read_text(encoding='utf-8'))
        ids = {row['id'] for row in doc['abilities']}
        expected = {'beastmaster_primal_roar', 'dragon_knight_dragon_tail', 'leshrac_lightning_storm',
                    'broodmother_spawn_spiderlings', 'night_stalker_void', 'bounty_hunter_shuriken_toss',
                    'spirit_breaker_nether_strike', 'sniper_assassinate', 'chen_penitence',
                    'witch_doctor_paralyzing_cask', 'skeleton_king_hellfire_blast', 'venomancer_snakebite',
                    'spectre_spectral_dagger'}
        self.assertTrue(expected.issubset(ids))
        for profile_id in expected | {'sandking_burrowstrike'}:
            with self.subTest(profile=profile_id):
                row = next(item for item in doc['abilities'] if item['id'] == profile_id)
                self.assertFalse(row['enabled'])
                self.assertEqual(row['model'], 'flat')
                self.assertTrue(row['damage'] and all(float(value) > 0 for value in row['damage']))
                self.assertIn('dwBuildNumber=6356260', row['notes'])
                def enable_one(candidate, profile_id=profile_id):
                    next(item for item in candidate['abilities'] if item['id'] == profile_id)['enabled'] = True
                self.load_changed(enable_one)
        rows = {row['id']: row for row in doc['abilities']}
        self.assertIn('7.41f', settings().disabled_reasons['sniper_assassinate'])
        self.assertEqual(rows['zuus_lightning_bolt']['damage'], [140, 220, 300, 380])
        self.assertEqual(rows['lina_laguna_blade']['damage_type'], 'magical')
        self.assertEqual(rows['lina_laguna_blade']['damage'], [400, 580, 760])
        self.assertEqual(rows['lich_frost_nova']['damage'], [120, 200, 280, 360])
        self.assertEqual(rows['queenofpain_scream_of_pain']['self_damage_fraction'], 0.25)
        self.assertFalse(rows['puck_illusory_orb']['enabled'])

    def test_zeus_cloud_is_skipped_with_warning_without_disabling_other_profiles(self):
        for profile_id in ('zuus_cloud', 'custom_cloud_flat'):
            def change(d):
                profile = copy.deepcopy(d['abilities'][2])
                profile.update(id=profile_id, hero='zuus', enabled=True, model='flat', kind='ability',
                               variants={'zuus_cloud': 0}, key='NONE', cast='no_target')
                profile.pop('stack_scaling', None)
                d['abilities'].append(profile)
            with self.subTest(profile_id=profile_id):
                settings = self.load_changed(change)
                self.assertNotIn('zuus_cloud', settings.by_name)
                self.assertIn(profile_id, settings.disabled)
                self.assertTrue(any('Zeus Cloud не загружен' in warning for warning in settings.warnings))
                self.assertIn('lion_impale', settings.by_name)

    def test_f6_is_not_a_killsteal_toggle_or_reserved_control_key(self):
        def bind_f6(d):
            d['abilities'][1].update(key='F6')
        settings = self.load_changed(bind_f6)
        self.assertEqual(settings.toggle_vk, 0)
        self.assertEqual(settings.profiles['lion_finger_of_death'].key, 117)

    def test_invalid_profiles_fail_closed(self):
        changes = [lambda d: d['abilities'][1].update(cast='channel'),
                   lambda d: d['abilities'][1].update(damage_type='physical'),
                   lambda d: d['abilities'][1].update(damage=[math.nan]),
                   lambda d: d['abilities'][1].update(key='F7'),
                   lambda d: d['abilities'][1].update(key='Z'),
                   lambda d: d['abilities'][1].update(variants={'item_dagon':0}),
                   lambda d: d['abilities'][1].update(hero='*'),
                   lambda d: d['abilities'][1].update(range=[10,20]),
                   lambda d: d['abilities'][1].update(self_damage_fraction=1.01),
                   lambda d: d['settings'].update(auto_cast='true')]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.load_changed(change)

    def test_new_ability_needs_only_json(self):
        def change(d):
            new = copy.deepcopy(d['abilities'][1])
            new.update(id='test_nuke', hero='test_hero', variants={'test_nuke':0}, key='T', damage=[700,800,900])
            d['abilities'].append(new)
        s = self.load_changed(change)
        me, enemy = fixture()
        me = replace(me, name='npc_dota_hero_test_hero', actions=(Action('test_nuke', 777, 2, 0, 100),))
        d = choose((me, enemy), s, 10.1, (1920,1080))
        self.assertEqual(d.profile.name, 'test_nuke')
        self.assertEqual(d.key, ord('T'))
        self.assertAlmostEqual(d.damage, 800*.73)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.s = settings()
        self.me, self.enemy = fixture()

    def pick(self, me=None, enemy=None, now=10.1, notes=None, excluded=()):
        return choose((me or self.me, enemy or self.enemy), self.s, now, (1920,1080), notes, excluded)

    def test_lion_reference(self):
        d = self.pick()
        self.assertEqual(d.key, 82)
        self.assertEqual(d.required, 190)
        self.assertAlmostEqual(d.damage, 438)

    def test_unknown_talent_uses_lower_bound_and_never_assumes_bonus(self):
        p = self.s.profiles['lion_finger_of_death']
        me = replace(self.me, stack_values=(('CDOTA_Modifier_Lion_FingerOfDeath_Kill_Counter', 2),),
                     talent_levels=())
        damage, note = raw_damage_details(p, me, 1)
        self.assertEqual(damage, 650)
        self.assertIn('нижняя оценка', note)
        self.assertEqual(raw_damage(p, me, 1), 650)
        # At this HP only the unconfirmed talent bonus would reach the threshold.
        self.assertIsNone(self.pick(me=me, enemy=replace(self.enemy, hp=450)))
        decision = self.pick(me=me, enemy=replace(self.enemy, hp=400))
        self.assertEqual(decision.profile.name, 'lion_finger_of_death')
        self.assertAlmostEqual(decision.damage, 650 * (1 - self.enemy.mr))

    def test_each_enabled_profile_has_a_synthetic_lethal_case(self):
        for p in self.s.profiles.values():
            with self.subTest(profile=p.name):
                alias = list(p.variants)[-1]
                a = Action(alias, 300, len(p.damage), 0, 50, slot=0 if p.kind=='item' else -1, charges=1)
                me = replace(self.me, name='npc_dota_hero_'+('lion' if p.hero=='*' else p.hero), actions=(a,))
                enemy = replace(self.enemy, hp=1, pos=(100,0))
                d = self.pick(me, enemy)
                self.assertIsNotNone(d)
                self.assertEqual(d.profile.name, p.name)

    def test_bad_target_values(self):
        for changes in [dict(team=2), dict(team=0), dict(visible=False), dict(alive=False), dict(illusion=True),
                        dict(hp=0), dict(hp=math.nan), dict(pos=()), dict(screen=(0,0)), dict(screen=(1000,1050)),
                        dict(mr=25), dict(mr=math.nan), dict(regen=math.inf)]:
            with self.subTest(changes=changes):
                self.assertIsNone(self.pick(enemy=replace(self.enemy, **changes)))

    def test_local_state(self):
        for changes in [dict(mana=199), dict(mana=math.nan), dict(busy=True), dict(alive=False), dict(pos=()), dict(team=0)]:
            with self.subTest(changes=changes):
                self.assertIsNone(self.pick(me=replace(self.me, **changes)))

    def test_action_state(self):
        for changes in [dict(level=0), dict(level=4), dict(cooldown=.01), dict(cooldown=-1), dict(cooldown=math.nan),
                        dict(hidden=True), dict(activated=False), dict(casting=True), dict(mana=10000)]:
            with self.subTest(changes=changes):
                me = replace(self.me, actions=(replace(self.me.actions[0], **changes),))
                self.assertIsNone(self.pick(me=me))

    def test_range_boundary(self):
        self.assertIsNotNone(self.pick(enemy=replace(self.enemy, pos=(900,0))))
        self.assertIsNone(self.pick(enemy=replace(self.enemy, pos=(901,0))))

    def test_margin_and_regen(self):
        self.assertIsNotNone(self.pick(enemy=replace(self.enemy, hp=398)))
        self.assertIsNone(self.pick(enemy=replace(self.enemy, hp=399)))
        self.assertIsNone(self.pick(enemy=replace(self.enemy, hp=390, regen=100)))

    def test_freshness(self):
        for now in (9., 10.251, math.nan):
            self.assertIsNone(self.pick(now=now))
        self.assertIsNone(self.pick(me=replace(self.me, at=9)))

    def test_inventory_rules(self):
        self.assertIsNotNone(self.pick(me=replace(self.me, inventory_known=False)))
        self.assertIsNone(self.pick(enemy=replace(self.enemy, inventory_known=False)))
        self.assertIsNone(self.pick(enemy=replace(self.enemy, items=('item_sphere',))))
        self.s.auto_cast = False
        self.assertIsNotNone(self.pick(enemy=replace(self.enemy, inventory_known=False)))

    def test_dagon_variants_current_slot_and_no_charges_requirement(self):
        for level in range(1,6):
            a = Action('item_dagon_'+str(level), 303, 1, 0, 100, slot=2, charges=0)
            d = self.pick(me=replace(self.me, actions=(a,)))
            self.assertEqual(d.key, ord('C'))
            self.assertAlmostEqual(d.damage, (300+100*level)*.73)
        upgraded = Action('item_dagon_upgraded', 303, 3, 0, 100, slot=2)
        d = self.pick(me=replace(self.me, actions=(upgraded,)))
        self.assertEqual(d.profile.variants[upgraded.name], 0)
        self.assertAlmostEqual(d.damage, (300+100*3)*.73)
        for slot in (-1,6,9,16):
            a = Action('item_dagon_5', 303, 1, 0, 100, slot=slot)
            self.assertIsNone(self.pick(me=replace(self.me, actions=(a,))))

    def test_pure_damage_ignores_mr(self):
        me = replace(self.me, name='npc_dota_hero_bane', actions=(Action('bane_brain_sap',300,4,0,100),))
        d = self.pick(me, replace(self.enemy, mr=.99))
        self.assertEqual(d.damage, 300)

    def test_int_scaling(self):
        me = replace(self.me, name='npc_dota_hero_skywrath_mage', intellect=100,
                     actions=(Action('skywrath_mage_arcane_bolt',300,1,0,50),))
        d = self.pick(me, replace(self.enemy, hp=50))
        self.assertAlmostEqual(d.damage, (60+150)*.73)
        self.assertIsNone(self.pick(replace(me, intellect=math.nan)))

    def test_point_waits_for_stationary_target(self):
        me = replace(self.me, actions=(Action('lion_impale',300,4,0,100),))
        self.assertIsNotNone(self.pick(me))
        for changes in [dict(motion_known=False), dict(speed=100), dict(stationary_for=.1)]:
            self.assertIsNone(self.pick(me, replace(self.enemy, **changes)))

    def test_no_target_radius(self):
        me = replace(self.me, name='npc_dota_hero_queenofpain', actions=(Action('queenofpain_scream_of_pain',300,4,0,100),))
        d = self.pick(me, replace(self.enemy, pos=(450,0)))
        self.assertFalse(d.profile.needs_cursor)
        self.assertIsNotNone(self.pick(me, replace(self.enemy, pos=(600,0))))
        self.assertIsNone(self.pick(me, replace(self.enemy, pos=(601,0))))

    def test_no_target_stationary_gate(self):
        me = replace(self.me, name='npc_dota_hero_centaur', actions=(Action('centaur_hoof_stomp',300,4,0,100),))
        target = replace(self.enemy, hp=1, pos=(300,0))
        self.assertIsNotNone(self.pick(me, target))
        self.assertIsNone(self.pick(me, replace(target, speed=100)))
        self.assertIsNone(self.pick(me, replace(target, stationary_for=.1)))

    def test_self_damage_gate_prevents_lethal_scream(self):
        action = Action('queenofpain_scream_of_pain',300,4,0,100)
        me = replace(self.me, name='npc_dota_hero_queenofpain', hp=50, actions=(action,))
        self.assertIsNone(self.pick(me, replace(self.enemy, hp=100, pos=(100,0))))
        d = self.pick(replace(me, hp=100), replace(self.enemy, hp=100, pos=(100,0)))
        self.assertEqual(d.profile.name, 'queenofpain_scream_of_pain')

    def test_global_visible_only(self):
        me = replace(self.me, name='npc_dota_hero_zuus', actions=(Action('zuus_thundergods_wrath',300,3,0,100),))
        d = self.pick(me, replace(self.enemy, pos=(10000,0)))
        self.assertFalse(d.profile.needs_cursor)
        self.assertIsNone(self.pick(me, replace(self.enemy, visible=False)))

    def test_hero_binding_protects_stolen_spell(self):
        self.assertIsNone(self.pick(me=replace(self.me, name='npc_dota_hero_rubick')))

    def test_quarantine_excludes_only_one_source(self):
        self.assertIsNone(self.pick(excluded={self.me.actions[0].identity}))
        a = Action('item_dagon', 303,1,0,100,slot=0)
        d = self.pick(me=replace(self.me, actions=self.me.actions+(a,)), excluded={self.me.actions[0].identity})
        self.assertEqual(d.profile.name, 'item_dagon')

    def test_masks(self):
        self.s.local_mask = 4
        self.s.target_mask = 8
        self.assertIsNone(self.pick(me=replace(self.me,state=4)))
        self.assertIsNone(self.pick(enemy=replace(self.enemy,state=8)))

    def test_mana_economy_first(self):
        dagon = Action('item_dagon_5',303,1,0,100,slot=0)
        self.assertEqual(self.pick(me=replace(self.me,actions=self.me.actions+(dagon,))).action,dagon)


class ToggleTests(unittest.TestCase):
    def test_rising_edge_not_repeat(self):
        t=Toggle()
        self.assertFalse(t.update(False))
        self.assertTrue(t.update(True))
        self.assertTrue(t.update(True))
        self.assertTrue(t.update(False))
        self.assertFalse(t.update(True))

    def test_held_at_startup(self):
        t=Toggle()
        self.assertFalse(t.update(True))
        self.assertFalse(t.update(True))
        t.update(False)
        self.assertTrue(t.update(True))

    def test_no_refocus_rearm(self):
        t=Toggle(); t.update(False); t.update(True)
        self.assertFalse(t.update(True, hard_off=True))
        self.assertFalse(t.update(True))
        self.assertFalse(t.update(False))
        self.assertTrue(t.update(True))

    def test_unready_press_is_not_delayed_activation(self):
        t=Toggle(); t.update(False)
        self.assertFalse(t.update(True, may_enable=False))
        self.assertFalse(t.update(True, may_enable=True))


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.s=settings(); self.me,self.enemy=fixture()
        self.d=choose((self.me,self.enemy),self.s,10.1,(1920,1080))
        self.l=CastLedger(); self.l.sync(self.me,10.1)
        self.assertTrue(self.l.begin(self.d,self.s,10.1))

    def test_not_ready_while_pending(self):
        self.assertFalse(self.l.ready(10.2))
        self.assertFalse(self.l.begin(self.d,self.s,10.2))

    def test_cooldown_feedback_and_impact_wait(self):
        a=replace(self.me.actions[0],cooldown=10)
        me=replace(self.me,at=10.2,actions=(a,))
        self.l.sync(me,10.21)
        self.assertIsNone(self.l.pending)
        self.assertFalse(self.l.ready(10.3))
        self.assertTrue(self.l.ready(11.))

    def test_pre_send_frame_does_not_confirm(self):
        me=replace(self.me,actions=(replace(self.me.actions[0],cooldown=10),))
        self.l.sync(me,10.2)
        self.assertIsNotNone(self.l.pending)

    def test_unknown_result_quarantined_not_retried(self):
        self.l.sync(replace(self.me,at=13),13)
        self.assertIn(self.me.actions[0].identity,self.l.quarantined)
        self.assertIsNone(self.l.pending)
        self.assertFalse(self.l.begin(self.d,self.s,14))
        self.l.sync(replace(self.me,at=14),14)
        self.assertIn(self.me.actions[0].identity,self.l.quarantined)

    def test_manual_cd_observation_releases_quarantine(self):
        self.l.sync(replace(self.me,at=13),13)
        a=replace(self.me.actions[0],cooldown=10)
        self.l.sync(replace(self.me,at=14,actions=(a,)),14)
        self.assertNotIn(a.identity,self.l.quarantined)

    def test_other_hero_resets_pending(self):
        self.l.sync(replace(self.me,entity=1000,at=10.2),10.3)
        self.assertIsNone(self.l.pending)
        self.assertFalse(self.l.quarantined)

    def test_charge_feedback(self):
        self.l.pending.uses_charges=True
        self.l.pending.action=replace(self.l.pending.action,charges=2)
        me=replace(self.me,at=10.2,actions=(replace(self.me.actions[0],charges=1),))
        self.l.sync(me,10.3)
        self.assertIsNone(self.l.pending)


if __name__=='__main__': unittest.main()
