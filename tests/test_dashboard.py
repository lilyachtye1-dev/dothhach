import json
import tempfile
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from helpers import fixture, settings
from ks_core import Action, CastLedger, Toggle, choose, raw_damage
from ks_inventory import Inventory
from ks_dashboard import dashboard, source_row, apply_control, wire_dashboard


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.s=settings();self.me,self.enemy=fixture()
        self.service=SimpleNamespace(settings=self.s,ui_session='session-A',ui_revision=0,ui_disabled=set(),
            toggle=Toggle(),ledger=CastLedger(),status='AUTO OFF',detail='unit test',off_reason='')
        self.heroes=[dict(is_local=True,_ks=self.me,designer=self.me.name,level=30,maxhp=1500,maxmana=900,
            abilities=[dict(name='lion_finger_of_death',ent=300),dict(name='lion_mana_drain',ent=301)],
            _ks_inventory=Inventory(True),_ks_warnings=[])]

    def snapshot(self):return dashboard(self.service,self.heroes,.01,10.1)

    def test_hero_and_only_real_sources(self):
        d=self.snapshot()
        self.assertEqual(d['hero'],'Lion');self.assertEqual(d['level'],30)
        self.assertEqual(len(d['sources']),2)
        finger=next(x for x in d['sources'] if x['name']=='lion_finger_of_death')
        self.assertTrue(finger['ready']);self.assertFalse(d['armed'])
        self.assertEqual(finger['damage'],600)
        self.assertFalse(next(x for x in d['sources'] if x['name']=='lion_mana_drain')['supported'])

    def test_catalog_contains_only_enabled_hero_profiles_and_is_available_without_local_hero(self):
        d=self.snapshot()
        lion=next(h for h in d['catalog'] if h['id']=='lion')
        self.assertEqual(lion['hero'],'Lion')
        self.assertEqual({r['name'] for r in lion['abilities']},
                         {'lion_impale','lion_finger_of_death'})
        self.assertTrue(all(r['catalog'] and r['supported'] for r in lion['abilities']))
        self.assertFalse(any(r['kind']=='item' for h in d['catalog'] for r in h['abilities']))
        self.heroes=[]
        empty=self.snapshot()
        self.assertEqual(empty['hero'],'')
        self.assertIn('lion', {h['id'] for h in empty['catalog']})

    def test_damage_uses_same_function_as_selector(self):
        p=self.s.profiles['lion_finger_of_death']
        self.assertEqual(self.snapshot()['sources'][0]['damage'],raw_damage(p,self.me,1))
        decision=choose((self.me,self.enemy),self.s,10.1,(1920,1080))
        self.assertAlmostEqual(decision.damage,self.snapshot()['sources'][0]['damage']*(1-self.enemy.mr))

    def test_unknown_source_surfaces_reader_warning(self):
        self.heroes[0]['_ks']=replace(self.me,actions=tuple(a for a in self.me.actions if a.name!='lion_finger_of_death'))
        self.heroes[0]['_ks_warnings']=['lion_finger_of_death: invalid ability: cooldown unreadable']
        row=next(x for x in self.snapshot()['sources'] if x['name']=='lion_finger_of_death')
        self.assertEqual(row['status'],'unknown')
        self.assertIn('cooldown unreadable',row['reason'])

    def test_talent_unknown_reason_is_exposed_separately(self):
        self.heroes[0]['_ks']=replace(self.me,talent_levels=())
        self.heroes[0]['_ks_warnings']=['live talents: learned talent state unknown']
        d=self.snapshot()
        self.assertEqual(d['talent'],'неизвестно')
        self.assertEqual(d['talent_reason'],'live talents: learned talent state unknown')

    def test_finger_talent_unknown_uses_lower_bound_and_remains_available(self):
        modifier='CDOTA_Modifier_Lion_FingerOfDeath_Kill_Counter'
        self.heroes[0]['_ks']=replace(self.me,stack_values=((modifier,1),),talent_levels=())
        row=next(x for x in self.snapshot()['sources'] if x['name']=='lion_finger_of_death')
        self.assertEqual(row['status'],'talent_unknown')
        self.assertTrue(row['ready'])
        self.assertEqual(row['damage'],625)
        self.assertIn('нижняя оценка',row['damage_note'])
        self.assertEqual(row['reason'],row['damage_note'])

    def test_six_empty_slots_only_when_inventory_known(self):
        d=self.snapshot();self.assertEqual(len(d['items']),6)
        self.assertTrue(all(x['status']=='empty' for x in d['items']))
        self.heroes[0]['_ks_inventory']=Inventory(False)
        self.heroes[0]['_ks']=replace(self.me,inventory_known=False)
        d=self.snapshot();self.assertEqual(d['items'],[]);self.assertFalse(d['inventory_known'])

    def test_equipped_dagon_binding_and_level(self):
        a=Action('item_dagon_3',444,1,0,100,slot=2)
        self.heroes[0]['_ks']=replace(self.me,actions=(a,))
        self.heroes[0]['_ks_inventory']=Inventory(True,((2,123,444,'item_dagon_3'),))
        row=next(x for x in self.snapshot()['items'] if x['name']=='item_dagon_3')
        self.assertEqual(row['key'],'C');self.assertEqual(row['level'],3)
        self.assertEqual(row['damage'],600);self.assertTrue(row['supported'])

    def test_upgraded_dagon_alias_uses_read_item_level(self):
        a=Action('item_dagon_upgraded',444,3,0,100,slot=2)
        self.heroes[0]['_ks']=replace(self.me,actions=(a,))
        self.heroes[0]['_ks_inventory']=Inventory(True,((2,123,444,'item_dagon_upgraded'),))
        row=next(x for x in self.snapshot()['items'] if x['name']=='item_dagon_upgraded')
        self.assertEqual(row['title'],'Dagon Upgraded')
        self.assertEqual(row['level'],3)
        self.assertEqual(row['damage'],600)
        self.assertTrue(row['supported'])

    def test_rapier_never_listed_as_supported_damage_source(self):
        self.heroes[0]['_ks_inventory']=Inventory(True,((0,123,444,'item_rapier'),))
        row=next(x for x in self.snapshot()['items'] if x['name']=='item_rapier')
        self.assertFalse(row['supported']);self.assertFalse(row['ready']);self.assertIsNone(row['damage'])

    def test_stale_data_not_ready(self):
        d=dashboard(self.service,self.heroes,1.,11.)
        self.assertFalse(d['fresh']);self.assertFalse(d['sources'][0]['ready'])
        self.assertEqual(d['sources'][0]['status'],'stale')

    def test_cooldown_mana_and_quarantine_reasons(self):
        a=self.me.actions[0]
        for action,code in ((replace(a,cooldown=5),'cooldown'),(replace(a,mana=10000),'mana'),(replace(a,level=0),'unlearned')):
            row=source_row(self.service,self.me,action,a.name,-1,True,300)
            self.assertEqual(row['status'],code);self.assertFalse(row['ready'])
        self.service.ledger.quarantined.add(a.identity)
        row=source_row(self.service,self.me,a,a.name,-1,True,300)
        self.assertEqual(row['status'],'blocked')

    def test_hero_switch_replaces_lion_not_cached(self):
        a=Action('zuus_thundergods_wrath',444,3,0,100)
        self.heroes=[dict(is_local=True,_ks=replace(self.me,name='npc_dota_hero_zuus',actions=(a,)),level=20)]
        d=self.snapshot();self.assertEqual(d['hero'],'Zeus');self.assertEqual(d['stacks'],'—')
        self.assertEqual([x['name'] for x in d['sources']],['zuus_thundergods_wrath'])

    def test_ambiguous_local_not_guessed(self):
        self.heroes.append(self.heroes[0])
        d=self.snapshot();self.assertEqual(d['hero'],'');self.assertEqual(d['sources'],[])

    def test_wire_single_line_bounded_json(self):
        self.service.status='status\n\ttext'
        with patch('ks_dashboard.time.perf_counter',return_value=10.1):
            line=wire_dashboard(self.service,self.heroes,.01)
        self.assertNotIn('\n',line);self.assertEqual(line.count('\t'),1)
        self.assertEqual(json.loads(line.split('\t',1)[1])['hero'],'Lion')
        self.assertLess(len(line.encode()),20000)

    def test_controls_disarm_but_do_not_clear_ledger(self):
        self.service.toggle.enabled=True
        self.service.ledger.quarantined.add(self.me.actions[0].identity)
        request=dict(session='session-A',revision=1,disabled=['lion_finger_of_death'])
        self.assertTrue(apply_control(self.service,request));self.assertFalse(self.service.toggle.enabled)
        self.assertIn(self.me.actions[0].identity,self.service.ledger.quarantined)
        self.assertEqual(self.snapshot()['sources'][0]['status'],'disabled')
        self.assertIsNone(choose((self.me,self.enemy),self.s,10.1,(1920,1080),disabled=self.service.ui_disabled))

    def test_enable_profile_does_not_arm(self):
        apply_control(self.service,dict(session='session-A',revision=1,disabled=['lion_finger_of_death']))
        self.assertTrue(apply_control(self.service,dict(session='session-A',revision=2,disabled=[])))
        self.assertFalse(self.service.toggle.enabled)
        self.assertIsNotNone(choose((self.me,self.enemy),self.s,10.1,(1920,1080),disabled=self.service.ui_disabled))

    def test_menu_button_arm_is_deferred_until_close(self):
        self.service.toggle.enabled=True
        self.assertTrue(apply_control(self.service,dict(session='session-A',revision=1,disabled=[],
            menu_open=True,arm_after_close=True)))
        self.assertFalse(self.service.toggle.enabled)
        self.assertTrue(self.service.ui_arm_pending)
        self.assertTrue(apply_control(self.service,dict(session='session-A',revision=2,disabled=[],menu_open=False)))
        self.assertTrue(self.service.ui_arm_pending)
        self.assertFalse(self.service.ui_menu_open)

    def test_bad_controls_not_partly_applied(self):
        for req in (dict(session='old',revision=1,disabled=[]),dict(session='session-A',revision=True,disabled=[]),
                    dict(session='session-A',revision=1,disabled=['invented']),dict(session='session-A',revision=1,disabled='abc'),
                    dict(session='session-A',revision=1,disabled=[],arm_after_close=1)):
            self.assertFalse(apply_control(self.service,req))
            self.assertEqual(self.service.ui_revision,0);self.assertEqual(self.service.ui_disabled,set())

    def test_replay_revision_rejected(self):
        req=dict(session='session-A',revision=1,disabled=['lion_finger_of_death'])
        self.assertTrue(apply_control(self.service,req))
        req['disabled']=[]
        self.assertFalse(apply_control(self.service,req));self.assertIn('lion_finger_of_death',self.service.ui_disabled)

    def test_menu_pause_command_and_invalid_flag(self):
        req=dict(session='session-A',revision=1,disabled=[],menu_open='yes')
        self.assertFalse(apply_control(self.service,req))
        req['menu_open']=True
        self.assertTrue(apply_control(self.service,req))
        self.assertTrue(self.service.ui_menu_open)
        self.assertFalse(self.service.toggle.enabled)
