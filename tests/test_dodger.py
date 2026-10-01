from dataclasses import replace
from pathlib import Path
import json
import math
import struct
import tempfile
import unittest
from unittest.mock import patch
from helpers import fixture,settings,fake_module,ROOT
from ks_core import Action
from ks_reader import CombatReader
from ks_service import KillSteal
from ks_dashboard import apply_control,dashboard
from test_service import FakeInput
from dodger import DEFENSES,BY_NAME,Threat,choose_defense
from dodge_reader import LaunchTracker,ThreatReader,Frame,SOURCES


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.s=settings();self.me,self.enemy=fixture()
        self.me=replace(self.me,actions=(Action('item_manta',300,1,0,125,2),))
        self.t=Threat((1,2,3),200,100,'sniper_assassinate',10.5,11.,10.)
        self.disabled={p.name for p in DEFENSES if not p.enabled}

    def choose(self,**kw):
        args=dict(me=self.me,units=[self.me,self.enemy],threats=[self.t],now=10.01,viewport=(1920,1080),settings=self.s,disabled=self.disabled)
        args.update(kw);return choose_defense(**args)

    def test_manta_uses_owned_active_slot(self):
        d=self.choose();self.assertEqual(d.key,self.s.item_keys[2]);self.assertEqual(d.profile.name,'item_manta')

    def test_manta_global_camera_independent(self):
        self.assertIsNotNone(self.choose(me=replace(self.me,screen=())))

    def test_stale_wrong_target_unknown_evidence_rejected(self):
        for t in (replace(self.t,target=999),replace(self.t,observed=8),replace(self.t,observed=12),replace(self.t,evidence='nearby_cast'),replace(self.t,earliest=math.nan)):
            with self.subTest(t=t):self.assertIsNone(self.choose(threats=[t]))

    def test_busy_dead_stale_local_rejected(self):
        for me in (replace(self.me,busy=True),replace(self.me,alive=False),replace(self.me,at=8)):
            self.assertIsNone(self.choose(me=me))

    def test_unready_action_rejected(self):
        a=self.me.actions[0]
        for bad in (replace(a,cooldown=3),replace(a,cooldown=-1),replace(a,cooldown=math.nan),replace(a,mana=10000),replace(a,mana=-1),replace(a,level=0),replace(a,hidden=True),replace(a,activated=False),replace(a,casting=True),replace(a,slot=6)):
            with self.subTest(bad=bad):self.assertIsNone(self.choose(me=replace(self.me,actions=(bad,))))

    def test_inventory_unknown(self):self.assertIsNone(self.choose(me=replace(self.me,inventory_known=False)))
    def test_disabled(self):self.assertIsNone(self.choose(disabled={'item_manta'}))
    def test_quarantine(self):self.assertIsNone(self.choose(quarantined={self.me.actions[0].identity}))
    def test_duplicate_threat(self):self.assertIsNone(self.choose(used={self.t.id}))
    def test_too_late(self):self.assertIsNone(self.choose(threats=[replace(self.t,earliest=10.1)]))
    def test_non_disjointable(self):self.assertIsNone(self.choose(threats=[replace(self.t,disjointable=False)]))

    def hero(self,hero,action):
        return replace(self.me,name='npc_dota_hero_'+hero,actions=(Action(action,300,1,0,50),))

    def test_counterspell_blockable_only(self):
        me=self.hero('antimage','antimage_counterspell')
        self.assertIsNotNone(self.choose(me=me))
        self.assertIsNone(self.choose(me=me,threats=[replace(self.t,blockable=False)]))

    def test_phase_shift_holds_full_channel(self):
        d=self.choose(me=self.hero('puck','puck_phase_shift'))
        self.assertIsNotNone(d);self.assertGreater(d.hold,1.)

    def test_lotus_not_preferred_over_true_dodge(self):
        me=replace(self.me,actions=self.me.actions+(Action('item_lotus_orb',301,1,0,50,1),))
        d=self.choose(me=me,disabled=());self.assertEqual(d.profile.name,'item_manta')

    def test_carapace_never_for_zero_damage_shackle(self):
        me=self.hero('nyx_assassin','nyx_assassin_spiked_carapace')
        self.assertIsNone(self.choose(me=me,disabled=(),threats=[replace(self.t,damage=False)]))
        self.assertIsNotNone(self.choose(me=me,disabled=()))

    def test_unverified_transformations_stay_blocked_even_enabled(self):
        for name in ('monkey_king_mischief','alchemist_chemical_rage','lone_druid_true_form','phoenix_supernova'):
            self.assertIsNone(self.choose(me=self.hero(BY_NAME[name].hero,name),disabled=()))

    def test_infest_no_arbitrary_enemy_host(self):
        me=self.hero('life_stealer','life_stealer_infest')
        t=replace(self.t,earliest=11.0,latest=11.5)
        self.assertIsNone(self.choose(me=me,disabled=(),threats=[t]))
        ally=replace(self.enemy,team=2,pos=(60,0))
        d=self.choose(me=me,units=[me,ally],disabled=(),threats=[t]);self.assertEqual(d.target,ally)
        self.assertIsNone(self.choose(me=me,units=[me,replace(ally,visible=False)],disabled=(),threats=[t]))

    def test_doppelganger_requires_safe_screen_point(self):
        me=self.hero('phantom_lancer','phantom_lancer_doppelwalk')
        t=replace(self.t,earliest=11.0,latest=11.5)
        self.assertIsNotNone(self.choose(me=me,threats=[t]))
        self.assertIsNone(self.choose(me=replace(me,screen=()),threats=[t]))

    def test_snowball_enemy_target_required(self):
        me=self.hero('tusk','tusk_snowball');t=replace(self.t,earliest=11.0,latest=11.5)
        self.assertIsNotNone(self.choose(me=me,disabled=(),threats=[t]))
        self.assertIsNone(self.choose(me=me,units=[me],disabled=(),threats=[t]))

    def test_sleight_rejects_window_wider_than_protection(self):
        me=self.hero('ember_spirit','ember_spirit_sleight_of_fist')
        self.assertIsNone(self.choose(me=me,disabled=()))


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.tracker=LaunchTracker();self.me=fixture()[0]
        self.s=dict(caster=200,entity=300,name='sniper_assassinate',target=100,handle=1,phase=True,cd=0,start=9.,pos=(2000,0))
    def sample(self,t,**kw):return self.tracker.update([dict(self.s,**kw)],self.me,t)
    def launch(self):
        self.sample(10);self.sample(10.04);return self.sample(10.08,phase=False,cd=20)
    def test_launch_requires_target_phase_and_cd(self):self.assertEqual(len(self.launch()),1)
    def test_cancel_no_cd_no_threat(self):
        self.sample(10);self.sample(10.04);self.assertEqual(self.sample(10.08,phase=False),())
    def test_first_seen_midflight_not_guessed(self):self.assertEqual(self.sample(10.08,phase=False,cd=20),())
    def test_single_phase_sample_not_enough(self):
        self.sample(10);self.assertEqual(self.sample(10.04,phase=False,cd=20),())
    def test_gap_rejects_launch(self):
        self.sample(10);self.sample(10.04);self.assertEqual(self.sample(10.4,phase=False,cd=20),())
    def test_target_changed(self):
        self.sample(10);self.sample(10.04);self.assertEqual(self.sample(10.08,phase=False,cd=20,target=999),())
    def test_cast_generation_changed(self):
        self.sample(10);self.sample(10.04);self.assertEqual(self.sample(10.08,phase=False,cd=20,start=9.1),())
    def test_one_launch_no_repeated_event(self):
        t=self.launch()[0];again=self.sample(10.12,phase=False,cd=19.9);self.assertEqual([x.id for x in again],[t.id])
    def test_lost_observation_invalidates(self):
        self.launch();self.assertEqual(self.tracker.update([],self.me,10.1),())
    def test_teleport_invalidates(self):
        self.launch();self.me=replace(self.me,pos=(3000,3000));self.assertEqual(self.sample(10.1,phase=False,cd=19),())

    def test_cooldown_one_sample_after_phase_end(self):
        self.sample(10);self.sample(10.04)
        self.assertEqual(self.sample(10.08,phase=False),())
        self.assertEqual(len(self.sample(10.11,phase=False,cd=20)),1)

    def test_start_time_reset_after_cast(self):
        for reset in (-1.,0.):
            self.tracker=LaunchTracker()
            self.sample(10);self.sample(10.04)
            threats=self.sample(10.08,phase=False,cd=20,start=reset)
            self.assertEqual(len(threats),1)
            self.assertEqual(threats[0].id[2],9.)

    def test_delayed_cd_after_grace_is_not_a_launch(self):
        self.sample(10);self.sample(10.04);self.sample(10.08,phase=False)
        self.assertEqual(self.sample(10.14,phase=False),())
        self.assertEqual(self.sample(10.22,phase=False,cd=20),())

    def test_cancel_and_new_cast_not_confused(self):
        self.sample(10);self.sample(10.04);self.sample(10.08,phase=False)
        self.sample(10.10,phase=True,start=10.1)
        self.assertEqual(self.sample(10.12,phase=False,cd=20,start=10.1),())

    def test_no_cooldown_mode_cannot_be_treated_as_confirmed(self):
        self.sample(10);self.sample(10.04)
        self.sample(10.08,phase=False);self.sample(10.14,phase=False)
        self.assertEqual(self.sample(10.25,phase=False),())

    def test_serial_change_at_release_rejected(self):
        self.sample(10);self.sample(10.04)
        self.assertEqual(self.sample(10.08,phase=False,cd=20,handle=0x8001),())


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.combat=CombatReader(fake_module(),ROOT/'client_dll.json',settings())
        self.reader=ThreatReader(self.combat,ROOT/'client_dll.json')
        self.me,self.enemy=fixture()
        self.me=replace(self.me,entity=0x20000,idx=1)
        self.enemy=replace(self.enemy,entity=0x30000,pos=(2000,0))
        self.ep=0x40000;self.handle=0x18001;self.ah=0x28002
        self.memory={};self.reads=0
        self.put(self.ep+16,'<Q',0x50000);self.put(0x50000,'<Q',self.ep);self.put(0x50010,'<I',self.ah)
        self.put(self.me.entity+16,'<Q',0x60000);self.put(0x60000,'<Q',self.me.entity);self.put(0x60010,'<I',self.handle)
        for base in (0x50000,0x60000):
            for i in range(20):self.memory.setdefault(base+i,0)
        self.name='sniper_assassinate';self.cls=SOURCES[self.name][0]
        self.field(self.cls,'m_hTarget','<I',self.handle)
        self.field('C_DOTABaseAbility','m_flCastStartTime','<f',9.)
        self.dot=type('Dot',(),{})();self.dot.rb=self.rb;self.dot.get_entity=lambda idx:(self.ep,self.name) if idx==(self.ah&0x7fff) else None
        self.reader.check_build=lambda dot:True
    def put(self,addr,fmt,value):
        for i,b in enumerate(struct.pack(fmt,value)):self.memory[addr+i]=b
    def field(self,cls,name,fmt,value):self.put(self.ep+self.combat.offset(cls,name),fmt,value)
    def rb(self,addr,size):
        self.reads+=1
        try:return bytes(self.memory[addr+i] for i in range(size))
        except KeyError:return None
    def frame(self,t,phase,cd=0):
        self.field('C_DOTABaseAbility','m_bInAbilityPhase','<B',phase);self.field('C_DOTABaseAbility','m_fCooldown','<f',cd)
        heroes=[dict(is_local=True,_ks=replace(self.me,at=t)),dict(_ks=replace(self.enemy,at=t),abilities=[dict(name=self.name,ent=self.ep)])]
        with patch('dodge_reader.time.perf_counter',return_value=t):return self.reader.capture(self.dot,heroes)
    def test_real_schema_addresses_to_launch(self):
        self.frame(10,1);self.frame(10.04,1);f=self.frame(10.08,0,20)
        self.assertTrue(f.build_ok);self.assertEqual(len(f.threats),1);self.assertLess(self.reads,60)
    def test_wrong_serial_same_index_blocked(self):
        self.field(self.cls,'m_hTarget','<I',self.handle+0x8000)
        self.frame(10,1);self.frame(10.04,1);self.assertFalse(self.frame(10.08,0,20).threats)
    def test_failed_read_clears_estimates(self):
        self.frame(10,1);self.frame(10.04,1);self.frame(10.08,0,20)
        del self.memory[self.ep+self.combat.offset(self.cls,'m_hTarget')]
        f=self.frame(10.1,0,19);self.assertFalse(f.build_ok);self.assertFalse(f.threats)
    def test_mismatched_build_blocked(self):
        self.reader.check_build=lambda dot:False;f=self.frame(10,1);self.assertFalse(f.build_ok)
    def test_wrong_designer_name_blocked(self):
        self.dot.get_entity=lambda idx:(self.ep,'unrelated')
        self.frame(10,1);self.frame(10.04,1);self.assertFalse(self.frame(10.08,0,20).threats)

    def test_reader_accepts_reset_cast_start_at_release(self):
        self.frame(10,1);self.frame(10.04,1)
        self.field('C_DOTABaseAbility','m_flCastStartTime','<f',-1.)
        f=self.frame(10.08,0,20)
        self.assertEqual(len(f.threats),1)

    def test_reader_reports_wrong_target_not_silent(self):
        self.field(self.cls,'m_hTarget','<I',0x8007)
        f=self.frame(10,1)
        self.assertIn('local_idx=1',' '.join(f.observations))
        self.assertFalse(f.threats)

    def test_reader_reports_serial_mismatch(self):
        self.field(self.cls,'m_hTarget','<I',self.handle+0x8000)
        f=self.frame(10,1)
        self.assertIn('SERIAL MISMATCH',' '.join(f.observations))


class DodgeServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        for n in ('ks_profiles.json','client_dll.json'):(self.root/n).write_bytes((ROOT/n).read_bytes())
        self.b=FakeInput();self.ks=KillSteal(self.root,fake_module(),self.b);self.d=self.ks.dodger
        me,enemy=fixture();self.me=replace(me,actions=me.actions+(Action('item_manta',400,1,0,125,2),))
        self.enemy=enemy;self.t=Threat((1,2,3),200,100,'sniper_assassinate',10.8,11.4,10.1)
        self.h=[]
    def tearDown(self):
        for h in list(self.ks.log.handlers):h.close();self.ks.log.removeHandler(h)
        self.tmp.cleanup()
    def tick(self,t=10.1):
        self.h=[dict(is_local=True,_ks=replace(self.me,at=t),_ks_viewport=(1920,1080),_dodge=Frame(t,100,(replace(self.t,observed=t),),'test',True)),dict(_ks=replace(self.enemy,at=t))]
        with patch('ks_service.time.perf_counter',return_value=t):self.ks.tick(self.h,0.)
    def arm(self):self.d.enabled=True
    def test_off_default(self):self.tick();self.assertEqual(self.b.calls,[])
    def test_dodge_wins_over_ks_same_tick(self):
        self.arm();self.ks.toggle.enabled=True;self.tick()
        self.assertEqual(len(self.b.calls),1);self.assertEqual(self.b.calls[0].action.name,'item_manta');self.assertIn('Dodger',self.ks.status)
    def test_no_repeat_while_cd_pending(self):
        self.arm();self.tick();self.tick(10.12);self.tick(10.15);self.assertEqual(len(self.b.calls),1)
    def test_cd_confirmation_not_a_confirmed_dodge(self):
        self.arm();self.tick();self.me=replace(self.me,actions=(replace(self.me.actions[1],cooldown=20),))
        self.tick(10.2);self.assertIsNone(self.d.pending)
    def test_no_cd_quarantine_survives_menu(self):
        self.arm();self.tick();self.tick(11.8);self.assertIn(('item_manta',400),self.d.quarantined)
        self.d.control(False,None,True);self.assertIn(('item_manta',400),self.d.quarantined)
    def test_failed_input_reserved_and_off(self):
        self.b.result='failed';self.arm();self.ks.toggle.enabled=True;self.tick();self.assertFalse(self.d.enabled);self.assertFalse(self.ks.toggle.enabled);self.assertIsNotNone(self.d.pending)
    def test_preview_never_input(self):
        self.ks.settings.auto_cast=False;self.arm();self.tick();self.assertEqual(self.b.calls,[]);self.assertIn('PREVIEW',self.d.status)
    def test_focus_loss_stops(self):
        self.arm();self.b.focus=False;self.tick();self.assertFalse(self.d.enabled);self.assertEqual(self.b.calls,[])
    def test_menu_stops(self):
        self.arm();self.ks.ui_menu_open=True;self.tick();self.assertTrue(self.d.enabled);self.assertEqual(self.b.calls,[])
    def test_manual_mouse_no_longer_pauses_defense(self):
        self.arm();self.b.keys.add(2);self.tick();self.assertEqual(len(self.b.calls),1);self.assertEqual(self.b.calls[0].action.name,'item_manta')
    def test_ks_pending_blocks_second_command(self):
        self.arm();self.ks.ledger.next_send=12;self.tick();self.assertEqual(self.b.calls,[])
    def test_invalid_control_all_or_nothing(self):
        doc=dict(session=self.ks.ui_session,revision=1,disabled=[],menu_open=False,dodge_arm=True,dodge_disabled=['fake'])
        self.assertFalse(apply_control(self.ks,doc));self.assertEqual(self.ks.ui_revision,0);self.assertFalse(self.d.arm_pending)
    def test_deferred_menu_arm(self):
        with patch('dodge_service.time.perf_counter',return_value=10):
            self.assertTrue(apply_control(self.ks,dict(session=self.ks.ui_session,revision=1,disabled=[],menu_open=False,dodge_arm=True)))
        self.assertFalse(self.d.enabled);self.tick();self.assertEqual(len(self.b.calls),1)
    def test_dashboard_has_live_module(self):
        self.tick();data=dashboard(self.ks,self.h,0,now=10.1)
        self.assertIn('dodger',data);self.assertTrue(data['dodger']['available'])

    def test_preview_mode_is_explicit_even_when_off(self):
        self.ks.settings.auto_cast=False;self.tick()
        data=self.d.dashboard(self.h)
        self.assertEqual(data['mode'],'PREVIEW')
        self.assertIn('auto_cast=false',data['mode_hint'])
        self.assertEqual(self.b.calls,[])

    def test_cli_preview_reason_distinguished_from_config(self):
        self.ks.settings.auto_cast=False;self.ks.preview_requested=True;self.tick()
        self.assertIn('PREVIEW.bat',self.d.dashboard(self.h)['mode_hint'])

    def test_console_has_independent_dodger_state(self):
        import io
        from contextlib import redirect_stdout
        out=io.StringIO()
        with redirect_stdout(out):self.tick()
        self.assertIn('[DODGER AUTO OFF]',out.getvalue())

    def test_hero_change_cancels_pending_request(self):
        self.tick()
        self.d.arm_pending=True;self.d.requested=10.1
        self.me=replace(self.me,entity=777,name='npc_dota_hero_antimage')
        self.tick(10.12)
        self.assertFalse(self.d.enabled);self.assertFalse(self.d.arm_pending)
        self.assertIn('Сменился защищаемый',self.d.status)
        self.assertEqual(self.b.calls,[])

    def test_source_mana_and_readiness_are_not_fabricated(self):
        self.me=replace(self.me,mana=10);self.tick()
        row=next(x for x in self.d.dashboard(self.h)['sources'] if x['name']=='item_manta')
        self.assertFalse(row['ready']);self.assertEqual(row['status'],'mana')
        self.assertEqual(row['title'],'Manta Style')

    def test_missing_owned_item_is_not_read_error(self):
        self.tick()
        row=next(x for x in self.d.dashboard(self.h)['sources'] if x['name']=='item_lotus_orb')
        self.assertFalse(row['owned']);self.assertEqual(row['status'],'absent')

    def test_profile_change_does_not_arm_other_module(self):
        self.arm();self.ks.toggle.enabled=True
        doc=dict(session=self.ks.ui_session,revision=1,disabled=[],menu_open=True,dodge_disabled=['item_manta'])
        self.assertTrue(apply_control(self.ks,doc))
        self.assertFalse(self.d.enabled);self.assertFalse(self.ks.toggle.enabled)
        self.assertEqual(self.d.disabled,{'item_manta'})


class DodgePageContracts(unittest.TestCase):
    def test_native_pages_are_separate(self):
        s=(ROOT/'ks_menu.cs').read_text()
        script=s[s.index('        void ScriptPage('):s.index('        void SelectScript(')]
        self.assertNotIn('DodgerCard',script);self.assertNotIn('DodgerPage',script)
        self.assertIn('SelectScript("Dodger")',s)
        self.assertIn('if(scriptModule=="Dodger")DodgerPage(rows);else ScriptPage(rows)',s)

    def test_native_shared_tiles_use_dodger_flags(self):
        s=(ROOT/'ks_menu.cs').read_text()
        self.assertIn('row.defensive?dodgeDesired:desired',s)
        self.assertIn('if(page==0&&scriptModule=="Dodger")return Rows()',s)
        self.assertIn('PREVIEW · КЛАВИШИ НЕ НАЖИМАЮТСЯ',s)
        self.assertIn('new SourceTile(Change,Select)',s)
