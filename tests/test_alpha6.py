"""Alpha6 regressions: synthetic memory, user32 and physical modifier events.
No Windows input, hook, WPF or game runtime is exercised by these tests.
"""
import json
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from helpers import ROOT,settings
import test_reader_input as inputs
import test_dodger as base
from test_assassinate_mark import MemoryHarness
from dodger import BY_NAME,choose_defense,Threat
from ks_core import Action
from ks_key_state import PhysicalModifiers
from ks_dashboard import apply_control,dashboard


class InputHarness:
    def __init__(self,name='antimage_counterspell',slot=-1):
        self.f=inputs.InputTests();self.f.setUp();self.b=self.f.b
        self.physical=PhysicalModifiers();self.physical.alive=True;self.physical.at=10.1
        self.b.modifiers=self.physical;self.packets=[];self.partial=None;self.hook=None
        self.s=self.f.s;self.s.defensive_input=True;self.s.defense_item_keys=self.s.item_keys;self.s.defense_alt_selfcast=True
        self.set_action(name,slot)
        self.b.u.SendInput=self.send
    def set_action(self,name,slot=-1):
        self.a=Action(name,400,4 if slot<0 else 1,0,50,slot)
        p=BY_NAME[name]
        self.me=replace(self.f.me,name='npc_dota_hero_'+('antimage' if p.hero=='*' else p.hero),actions=(self.a,))
        self.d=SimpleNamespace(target=self.me,caster=self.me,action=self.a,
            key=self.s.item_keys[slot] if slot>=0 else ord(p.key),
            profile=SimpleNamespace(needs_cursor=p.needs_cursor and p.cast!='self',self_cast=p.cast=='self',cast='unit' if p.cast=='self' else 'point'))
    def mods(self,keys):
        self.physical.keys=set(keys);self.f.keys.update(keys)
    def send(self,n,arr,size):
        if hasattr(arr,'_obj'):arr=[arr._obj]
        events=[(arr[i].ki.wVk,bool(arr[i].ki.dwFlags&2)) for i in range(n)]
        count=self.partial if self.partial is not None else n;self.partial=None
        self.packets.append(events)
        for key,up in events[:count]:
            if up:self.f.keys.discard(key)
            else:self.f.keys.add(key)
            self.physical.event(key,not up,injected=True)
        if self.hook:self.hook(events)
        return count
    def cast(self):
        with patch('ks_input.time.perf_counter',return_value=10.1):
            return self.b.cast(self.d,self.s,self.f.ctx,lambda:self.f.armed)
    def events(self):return [x for packet in self.packets for x in packet]


class PriorityInputTests(unittest.TestCase):
    def setUp(self):self.h=InputHarness()
    def test_other_letters_and_both_mouse_buttons_do_not_block_e(self):
        self.h.f.keys={1,2,81,82,87,67}
        self.assertEqual(self.h.cast()[0],'sent');self.assertEqual(self.h.events(),[(69,False),(69,True)])
        self.assertFalse(self.h.f.moves);self.assertEqual(self.h.f.keys,{1,2,81,82,87,67})
    def test_cursor_change_in_final_guard_skips_default_self_cast(self):
        h=InputHarness('item_cyclone',2);h.s.defense_alt_selfcast=False
        h.d.profile=SimpleNamespace(needs_cursor=True,self_cast=False,cast='unit')
        calls=[]
        def armed():
            calls.append(1)
            if len(calls)==2:h.f.cursor=(7,8)
            return True
        with patch('ks_input.time.perf_counter',return_value=10.1):
            state,reason=h.b.cast(h.d,h.s,h.f.ctx,armed)
        self.assertEqual(state,'skipped');self.assertIn('наведения',reason)
        self.assertFalse(h.events());self.assertEqual(h.f.cursor,(7,8))
    def test_held_own_key_remains_untouched(self):
        self.h.f.keys={69,2};self.assertEqual(self.h.cast()[0],'skipped')
        self.assertFalse(self.h.events());self.assertIn(69,self.h.f.keys)
    def test_all_six_modifiers_are_neutralized_and_restored(self):
        mods={160,161,162,163,164,165};self.h.mods(mods)
        self.assertEqual(self.h.cast()[0],'sent')
        self.assertEqual(self.h.packets[0],[(k,True) for k in sorted(mods)]+[(69,False),(69,True)])
        self.assertEqual(self.h.f.keys,mods);self.assertEqual(self.h.physical.keys,mods)
        self.assertEqual(self.h.events().count((69,False)),1)
    def test_right_ctrl_alt_have_extended_flag_on_release_and_restore(self):
        self.h.mods({163,165});packets=[];send=self.h.b.u.SendInput
        def capture(n,arr,size):
            packets.extend((arr[i].ki.wVk,arr[i].ki.dwFlags) for i in range(n))
            return send(n,arr,size)
        self.h.b.u.SendInput=capture
        self.assertEqual(self.h.cast()[0],'sent')
        self.assertTrue(all(flags&1 for key,flags in packets if key in (163,165)))
        self.assertTrue(all(not flags&1 for key,flags in packets if key==69))
    def test_physical_press_before_async_state_update_is_not_treated_as_empty(self):
        self.h.physical.keys={160};self.h.f.keys=set()
        self.assertEqual(self.h.cast()[0],'skipped');self.assertFalse(self.h.events())
    def test_real_release_during_send_is_not_restored_from_old_snapshot(self):
        self.h.mods({160})
        def hook(events):
            if (69,False) in events:self.h.physical.event(160,False)
        self.h.hook=hook
        self.assertEqual(self.h.cast()[0],'sent');self.assertNotIn(160,self.h.f.keys)
        self.assertNotIn((160,False),self.h.events())
    def test_release_racing_restore_is_reconciled(self):
        self.h.mods({160})
        def hook(events):
            if (160,False) in events:self.h.physical.event(160,False)
        self.h.hook=hook
        self.assertEqual(self.h.cast()[0],'sent');self.assertNotIn(160,self.h.f.keys)
        self.assertEqual(self.h.events()[-1],(160,True))
    def test_unobserved_or_stale_modifier_state_skips_before_input(self):
        self.h.mods({160});self.h.physical.at=9.
        self.assertEqual(self.h.cast()[0],'skipped');self.assertFalse(self.h.events())
    def test_monitor_loss_after_key_pair_fails_closed(self):
        self.h.mods({160})
        self.h.hook=lambda events:setattr(self.h.physical,'alive',False)
        self.assertEqual(self.h.cast()[0],'failed');self.assertEqual(self.h.events().count((69,False)),1)
    def test_other_injected_modifier_state_is_not_assumed_physical(self):
        self.h.f.keys={160}
        self.assertEqual(self.h.cast()[0],'skipped');self.assertFalse(self.h.events())
    def test_aggregate_only_unknown_modifier_is_not_guessed(self):
        self.h.f.keys={16}
        self.assertEqual(self.h.cast()[0],'skipped');self.assertFalse(self.h.events())
    def test_self_items_use_alt_slot_without_cursor_even_offscreen(self):
        for name in ('item_lotus_orb','item_cyclone','item_wind_waker'):
            with self.subTest(name=name):
                h=InputHarness(name,2);h.f.keys={1,2,82}
                h.d.target=h.d.caster=replace(h.me,screen=())
                self.assertEqual(h.cast()[0],'sent')
                self.assertEqual(h.packets[0],[(164,False),(67,False),(67,True),(164,True)])
                self.assertEqual(h.f.keys,{1,2,82});self.assertFalse(h.f.moves)
    def test_selfcast_restores_physical_alt_and_shift(self):
        h=InputHarness('item_cyclone',2);h.mods({160,164,165})
        self.assertEqual(h.cast()[0],'sent');self.assertEqual(h.f.keys,{160,164,165})
        self.assertEqual(h.events().count((67,False)),1)
    def test_selfcast_requires_physical_observer(self):
        h=InputHarness('item_cyclone',2);h.physical.alive=False
        self.assertEqual(h.cast()[0],'skipped');self.assertFalse(h.events())
    def test_partial_selfcast_releases_synthetic_alt_and_stops(self):
        for count in range(4):
            with self.subTest(count=count):
                h=InputHarness('item_cyclone',2);h.partial=count
                self.assertEqual(h.cast()[0],'failed');self.assertFalse(h.f.keys)
                self.assertLessEqual(h.events().count((67,False)),1)
    def test_hard_keys_win_insert_focus_and_arming_still_block(self):
        for key in (9,13,35,45,91,92):
            self.h.f.keys={key};self.assertEqual(self.h.cast()[0],'skipped')
        self.h.f.keys=set();self.h.f.focus=False;self.assertEqual(self.h.cast()[0],'skipped')
        self.h.f.focus=True;self.h.f.armed=False;self.assertEqual(self.h.cast()[0],'skipped')
        self.assertFalse(self.h.events())
    def test_stale_caster_even_with_fresh_aim_rejects(self):
        self.h.d.caster=replace(self.h.me,at=9.)
        self.assertEqual(self.h.cast()[0],'skipped');self.assertFalse(self.h.events())
    def test_invalid_defensive_contract_does_not_fall_back_to_unguarded_input(self):
        self.h.d.key=82
        self.assertEqual(self.h.cast()[0],'skipped');self.assertFalse(self.h.events())
    def test_no_opt_in_keeps_ks_mouse_guard(self):
        self.h.s.defensive_input=False;self.h.f.keys={2}
        self.assertEqual(self.h.cast()[0],'skipped');self.assertFalse(self.h.events())
    def test_default_self_item_retains_working_cursor_cast_without_alt_requirement(self):
        h=InputHarness('item_lotus_orb',2);h.s.defense_alt_selfcast=False
        h.d.profile.self_cast=False;h.d.profile.needs_cursor=True;h.f.keys={1,2,82}
        self.assertEqual(h.cast()[0],'sent');self.assertEqual(h.events(),[(67,False),(67,True)])
        self.assertEqual(h.f.cursor,(900,570));self.assertEqual(len(h.f.moves),1)
    def test_native_menu_identity_checks_both_hwnd_and_pid(self):
        self.h.b.u.GetForegroundWindow=lambda:123
        def owner(hwnd,pointer):pointer._obj.value=456;return 1
        self.h.b.u.GetWindowThreadProcessId=owner
        self.assertTrue(self.h.b.menu_context(123,456))
        self.assertFalse(self.h.b.menu_context(124,456));self.assertFalse(self.h.b.menu_context(123,457))
    def test_alt_selfcast_never_emits_alt_f4_or_other_function_chords(self):
        for key in (115,112,96):
            h=InputHarness('item_cyclone',2)
            keys=list(h.s.item_keys);keys[2]=key;h.s.defense_item_keys=tuple(keys);h.d.key=key
            self.assertEqual(h.cast()[0],'skipped');self.assertFalse(h.events())
    def test_monitor_ignores_injected_and_non_modifier_events(self):
        p=self.h.physical
        p.event(160,True,injected=True);p.event(65,True)
        self.assertFalse(p.keys)
        p.event(160,True);p.event(160,False,injected=True);self.assertEqual(p.keys,{160})
        p.event(160,False);self.assertFalse(p.keys)


class MenuPreservationTests(unittest.TestCase):
    def setUp(self):
        self.f=base.DodgeServiceTests();self.f.setUp();self.addCleanup(self.f.tearDown)
        self.ks=self.f.ks;self.d=self.f.d;self.b=self.f.b
        self.ks.toggle.enabled=True;self.d.enabled=True
    def control(self,at=10.,**kw):
        doc=dict(session=self.ks.ui_session,revision=self.ks.ui_revision+1,disabled=[],menu_open=True,preserve_armed=True)
        doc.update(kw)
        with patch('ks_dashboard.time.perf_counter',return_value=at):return apply_control(self.ks,doc)
    def test_menu_open_holds_both_states_without_input(self):
        self.assertTrue(self.control());self.b.focus=False;self.f.tick()
        self.assertTrue(self.ks.toggle.enabled);self.assertTrue(self.d.enabled);self.assertFalse(self.b.calls)
    def test_close_resumes_existing_state_after_focus_returns(self):
        self.control();self.b.focus=False;self.f.tick()
        self.control(10.2,menu_open=False);self.f.tick(10.2)
        self.assertTrue(self.d.enabled);self.assertTrue(self.ks.toggle.enabled);self.assertFalse(self.b.calls)
        self.b.focus=True;self.f.tick(10.25)
        self.assertEqual(len(self.b.calls),1);self.assertTrue(self.ks.toggle.enabled)
    def test_close_without_focus_expires_to_off(self):
        self.control();self.b.focus=False;self.f.tick()
        self.control(10.2,menu_open=False);self.f.tick(10.8)
        self.assertFalse(self.d.enabled);self.assertFalse(self.ks.toggle.enabled)
        self.b.focus=True;self.f.tick(10.9);self.assertFalse(self.d.enabled);self.assertFalse(self.ks.toggle.enabled)
    def test_open_close_never_arms_an_off_module(self):
        self.d.enabled=self.ks.toggle.enabled=False
        self.control();self.f.tick();self.control(10.2,menu_open=False);self.f.tick(10.2)
        self.assertFalse(self.d.enabled);self.assertFalse(self.ks.toggle.enabled);self.assertFalse(self.b.calls)
    def test_insert_before_menu_message_is_pause_not_off(self):
        self.b.keys={45};self.f.tick()
        self.assertTrue(self.d.enabled);self.assertTrue(self.ks.toggle.enabled);self.assertFalse(self.b.calls)
        self.control();self.b.keys.clear();self.b.focus=False;self.f.tick(10.2)
        self.assertTrue(self.d.enabled);self.assertTrue(self.ks.toggle.enabled)
    def test_end_in_menu_remains_a_hard_stop(self):
        self.control();self.b.keys={35};self.f.tick()
        self.assertFalse(self.d.enabled);self.assertFalse(self.ks.toggle.enabled)
    def test_ui_enter_tab_do_not_disarm_while_menu_is_open(self):
        self.control();self.b.keys={13,9};self.b.focus=False;self.f.tick()
        self.assertTrue(self.d.enabled);self.assertTrue(self.ks.toggle.enabled);self.assertFalse(self.b.calls)
    def test_explicit_module_off_commands_are_independent(self):
        self.control(dodge_arm=False);self.f.tick()
        self.assertFalse(self.d.enabled);self.assertTrue(self.ks.toggle.enabled)
        self.d.enabled=True;self.control(10.2,arm_after_close=False);self.f.tick(10.2)
        self.assertTrue(self.d.enabled);self.assertFalse(self.ks.toggle.enabled)
    def test_source_changes_preserve_armed_state_in_menu(self):
        self.control(dodge_disabled=['item_manta']);self.f.tick()
        self.assertTrue(self.d.enabled);self.assertTrue(self.ks.toggle.enabled)
        self.assertIn('item_manta',self.d.disabled);self.assertFalse(self.b.calls)
    def test_bad_preserve_flag_rejected_atomically(self):
        revision=self.ks.ui_revision
        self.assertFalse(self.control(preserve_armed='yes',dodge_arm=False))
        self.assertEqual(self.ks.ui_revision,revision);self.assertTrue(self.d.enabled)
    def test_hero_change_in_menu_stays_off_after_close(self):
        self.control();self.f.tick();self.f.me=replace(self.f.me,entity=999);self.f.tick(10.2)
        self.control(10.3,menu_open=False);self.f.tick(10.3)
        self.assertFalse(self.d.enabled);self.assertFalse(self.ks.toggle.enabled)
    def test_stale_menu_data_disarms_instead_of_restoring_old_arm(self):
        self.control();self.f.tick()
        with patch('ks_service.time.perf_counter',return_value=13.):self.ks.tick(self.f.h,3.)
        self.assertFalse(self.d.enabled)
        with patch('ks_service.time.perf_counter',return_value=16.):self.ks.tick(self.f.h,6.)
        self.assertFalse(self.ks.toggle.enabled)
    def test_native_menu_focus_before_visibility_message_does_not_disarm(self):
        self.ks.ui_menu_hwnd=123;self.ks.ui_menu_pid=456
        self.b.menu_context=lambda h,p:(h,p)==(123,456)
        self.b.focus=False;self.f.tick()
        self.assertFalse(self.ks.ui_menu_open);self.assertTrue(self.ks.toggle.enabled);self.assertTrue(self.d.enabled)
        self.assertFalse(self.b.calls)
    def test_close_message_without_observed_open_has_bounded_focus_grace(self):
        self.b.focus=False;self.control(menu_open=False);self.f.tick(10.1)
        self.assertTrue(self.d.enabled);self.assertTrue(self.ks.toggle.enabled)
        self.f.tick(10.6);self.assertFalse(self.d.enabled);self.assertFalse(self.ks.toggle.enabled)
    def test_invalid_menu_identity_is_rejected_before_any_control_change(self):
        self.assertFalse(self.control(menu_hwnd=0,menu_pid=456,dodge_arm=False,dodge_alt_selfcast=True))
        self.assertTrue(self.d.enabled);self.assertFalse(self.d.alt_selfcast)
        self.assertFalse(self.control(menu_hwnd=123,menu_pid=True))
    def test_alt_selfcast_opt_in_defaults_off_and_preserves_module_state(self):
        self.assertFalse(self.d.alt_selfcast)
        self.assertFalse(self.control(dodge_alt_selfcast='yes'))
        self.assertTrue(self.control(dodge_alt_selfcast=True));self.f.tick()
        self.assertTrue(self.d.enabled);self.assertTrue(self.d.dashboard(self.f.h)['alt_selfcast'])
    def test_pending_ks_is_in_native_dto(self):
        self.ks.ui_arm_pending=True
        self.assertTrue(dashboard(self.ks,[],0.,now=10.)['arm_pending'])
        native=(ROOT/'ks_menu.cs').read_text()
        self.assertIn('if(armAfterClose.HasValue)',native);self.assertIn('if(dodgeArm.HasValue)',native)
        self.assertIn('SendControls(null,null,false)',native);self.assertIn('doc["preserve_armed"]=true',native)


class PhaseGuardTests(unittest.TestCase):
    def setUp(self):
        self.h=MemoryHarness();self.h.f.enemy=replace(self.h.f.enemy,pos=(300.,0.))
        self.h.f.field('C_DOTABaseAbility','m_flOverrideCastPoint','<f',-1.)
    def phase(self,end=11.4):
        self.h.frame(9.96,0,start=50.)
        for i in range(round((end-10.)/.04)+1):
            f=self.h.frame(round(10+i*.04,4),1,token=0x90000)
        return f
    def test_close_cast_selects_counterspell_before_release(self):
        f=self.phase();self.assertEqual(f.threats[0].evidence,'local_mark_phase')
        me=self.h.heroes[0]['_ks'];units=[x['_ks'] for x in self.h.heroes]
        d=choose_defense(me,units,f.threats,11.4,(1920,1080),settings())
        self.assertIsNotNone(d);self.assertEqual(d.key,69)
        self.assertTrue(any('cast_guard' in text for _,text in f.events))
    def test_phase_and_flight_share_exact_same_id(self):
        f=self.phase();uid=f.threats[0].id
        for i in range(14):self.h.frame(11.44+i*.04,1,token=0x90000)
        released=self.h.frame(12.,0,10,102.,0x90000)
        self.assertEqual(released.threats[0].id,uid);self.assertEqual(released.threats[0].evidence,'local_mark_launch')
    def test_no_manta_or_self_item_is_used_before_release(self):
        f=self.phase();me=self.h.heroes[0]['_ks']
        for name,slot in [('item_manta',0),('item_cyclone',1),('item_lotus_orb',2)]:
            with self.subTest(name=name):
                m=replace(me,actions=(Action(name,111,1,0,50,slot),))
                self.assertIsNone(choose_defense(m,[m],f.threats,11.4,(1920,1080),settings()))
    def test_missing_override_does_not_invent_phase_timing(self):
        off=self.h.f.combat.offset('C_DOTABaseAbility','m_flOverrideCastPoint')
        del self.h.f.memory[self.h.f.ep+off]
        self.assertFalse(self.phase().threats)
    def test_zero_or_invalid_override_rejects_phase_prediction(self):
        for point in (0.,float('nan'),8.):
            self.h.f.reader.reset_marked();self.h.f.field('C_DOTABaseAbility','m_flOverrideCastPoint','<f',point)
            self.assertFalse(self.phase().threats)
    def test_changed_override_during_phase_rejects_prediction(self):
        self.phase(11.)
        self.h.f.field('C_DOTABaseAbility','m_flOverrideCastPoint','<f',1.8)
        self.assertFalse(self.h.frame(11.04,1,token=0x90000).threats)
    def test_far_cast_remains_launch_only(self):
        self.h.f.enemy=replace(self.h.f.enemy,pos=(2000.,0.));self.assertFalse(self.phase().threats)
    def test_cancelled_phase_has_no_retained_guard(self):
        self.assertTrue(self.phase().threats)
        f=self.h.frame(11.44,0,0,100.,0x90000)
        self.assertFalse(f.threats)
    def test_unknown_mark_or_foreign_target_does_not_create_guard(self):
        self.h.error=ValueError('unreadable');self.assertFalse(self.phase().threats)
        self.h.error=None;self.h.f.field(self.h.f.cls,'m_hTarget','<I',0x8007)
        self.assertFalse(self.phase().threats)
    def test_phase_proof_has_75ms_limit_and_sniper_only(self):
        f=self.phase();me=self.h.heroes[0]['_ks'];t=f.threats[0]
        for changed in (replace(t,observed=11.2),replace(t,name='chaos_knight_chaos_bolt')):
            self.assertIsNone(choose_defense(me,[me],(changed,),11.4,(1920,1080),settings()))
    def test_reader_to_real_guard_one_e_through_phase_and_launch(self):
        f=base.DodgeServiceTests();f.setUp();self.addCleanup(f.tearDown)
        real=InputHarness();real.f.keys={1,2,82};f.ks.backend=real.b;f.d.enabled=True
        self.h.frame(9.96,0,start=50.);sent_at=None
        for i in range(55):
            now=10+i*.04;phase=i<50
            self.h.frame(now,int(phase),0 if phase else 10,100. if phase else 102.,0x90000)
            with patch('ks_service.time.perf_counter',return_value=now):f.ks.tick(self.h.heroes,0.)
            if real.events() and sent_at is None:sent_at=now
        self.assertIsNotNone(sent_at);self.assertLess(sent_at,12.)
        self.assertEqual(real.events().count((69,False)),1);self.assertFalse(real.f.moves)
    def test_confirmation_then_cd_reset_still_cannot_repeat_phase_or_flight(self):
        f=base.DodgeServiceTests();f.setUp();self.addCleanup(f.tearDown);f.d.enabled=True
        self.h.frame(9.96,0,start=50.)
        confirmed=False
        for i in range(55):
            now=10+i*.04;phase=i<50
            a=self.h.f.me.actions[0]
            if f.b.calls and not confirmed:
                a=replace(a,cooldown=3);confirmed=True
            else:a=replace(a,cooldown=0)
            self.h.f.me=replace(self.h.f.me,actions=(a,))
            self.h.frame(now,int(phase),0 if phase else 10,100. if phase else 102.,0x90000)
            with patch('ks_service.time.perf_counter',return_value=now):f.ks.tick(self.h.heroes,0.)
        self.assertTrue(confirmed);self.assertIsNone(f.d.pending);self.assertEqual(len(f.b.calls),1)
    def test_cursor_selfcast_remains_default_and_requires_safe_screen(self):
        me=replace(self.h.f.me,at=10.,screen=(),actions=(Action('item_cyclone',111,1,0,50,0),))
        t=Threat((1,),200,me.entity,'sniper_assassinate',10.7,11.2,10.)
        self.assertIsNone(choose_defense(me,[me],(t,),10.01,(1920,1080),settings()))
    def unknown_timing_history(self,casts=2):
        off=self.h.f.combat.offset('C_DOTABaseAbility','m_flOverrideCastPoint')
        del self.h.f.memory[self.h.f.ep+off]
        # Continuous 40 ms SYNTHETIC polling through two completed casts and idle.
        self.h.frame(9.96,0,start=50.)
        for i in range(casts*100):
            now=10.+i*.04;cycle=i//100;step=i%100;start=100.+cycle*4
            phase=step<50
            self.h.frame(now,int(phase),0 if phase or step>55 else 10,start if phase else start+2,
                         0x90000+cycle*16 if step<=50 else 0)
    def test_unknown_cast_point_can_use_two_consistent_observed_durations(self):
        self.unknown_timing_history()
        for i in range(36):f=self.h.frame(18.+i*.04,1,start=108.,token=0x90020)
        self.assertTrue(f.threats);self.assertEqual(f.threats[0].evidence,'local_mark_phase')
        self.assertAlmostEqual(self.h.f.reader.marked.guard_point,2.)
    def test_one_observed_duration_is_not_enough_for_unknown_cast_point(self):
        self.unknown_timing_history(casts=1)
        for i in range(36):f=self.h.frame(14.+i*.04,1,start=104.,token=0x90010)
        self.assertFalse(f.threats)
    def test_observation_gap_discards_timing_calibration(self):
        self.unknown_timing_history()
        self.h.frame(19.,0,start=106.)
        for i in range(36):f=self.h.frame(19.04+i*.04,1,start=109.,token=0x90020)
        self.assertFalse(f.threats)
    def test_five_real_close_cast_timings_with_modelled_distance_reach_phase_choice(self):
        captures=json.loads((ROOT/'tests/fixtures/assassinate_alpha5_close.json').read_text())['casts']
        self.assertEqual(len(captures),5)
        for capture in captures:
            with self.subTest(at=capture['launch']['at']):
                h=MemoryHarness();h.f.enemy=replace(h.f.enemy,pos=(1100.,0.))
                # Override availability/distance/resources/polls are NOT in the log.
                h.f.field('C_DOTABaseAbility','m_flOverrideCastPoint','<f',-1.)
                b,r=capture['begin'],capture['release'];start=b['at'];end=r['at']
                self.assertLess(capture['launch']['earliest']-capture['launch']['at'],.18)
                self.assertAlmostEqual(r['start']-b['start'],2.,places=3)
                h.frame(start-.04,0,start=b['start']-10.)
                chosen=None;now=start
                while now<end:
                    f=h.frame(now,1,start=b['start'],token=0x90000)
                    me=h.heroes[0]['_ks']
                    d=choose_defense(me,[x['_ks'] for x in h.heroes],f.threats,now,(1920,1080),settings())
                    if d:chosen=chosen or now
                    now+=.04
                self.assertIsNotNone(chosen);self.assertLess(chosen,end)
    def test_self_item_planning_no_longer_requires_camera_on_hero(self):
        me=self.h.f.me;me=replace(me,at=10.,screen=(),actions=(Action('item_cyclone',111,1,0,50,0),))
        t=Threat((1,),200,me.entity,'sniper_assassinate',10.7,11.2,10.)
        d=choose_defense(me,[me],(t,),10.01,(1920,1080),settings(),alt_selfcast=True)
        self.assertIsNotNone(d);self.assertFalse(d.profile.needs_cursor)


class DefensiveBusyTests(unittest.TestCase):
    def setUp(self):
        self.f=base.DodgeServiceTests();self.f.setUp();self.addCleanup(self.f.tearDown)
        self.f.d.enabled=True;self.f.me=replace(self.f.me,busy=True)
    def test_known_manual_cast_may_be_interrupted_by_defense(self):
        self.f.tick()  # Unknown busy reason is blocked.
        self.assertFalse(self.f.b.calls)
        self.f.h[0]['_ks_busy_reason']='lion_finger_of_death'
        with patch('ks_service.time.perf_counter',return_value=10.1):self.f.ks.tick(self.f.h,0.)
        self.assertEqual(len(self.f.b.calls),1);self.assertEqual(self.f.b.calls[0].action.name,'item_manta')
    def test_unknown_busy_state_never_forced(self):
        self.f.tick();self.f.h[0]['_ks_busy_reason']='unreadable ability activity'
        with patch('ks_service.time.perf_counter',return_value=10.1):self.f.ks.tick(self.f.h,0.)
        self.assertFalse(self.f.b.calls)


class ChaosDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.h=MemoryHarness();f=self.h.f
        f.name='chaos_knight_chaos_bolt';f.enemy=replace(f.enemy,name='npc_dota_hero_chaos_knight')
        self.h.entities[2]=(f.ep,f.name);self.h.entities[7]=(f.enemy.entity,f.enemy.name)
        off=f.combat.offset('C_DOTA_Ability_ChaosKnight_Chaos_Bolt','hAlreadyHitList')
        for i in range(24):f.memory[f.ep+off+i]=0
    def test_no_bar_mark_required_to_observe_chaos_cast(self):
        f=self.h.frame(10.,1,start=100.)
        self.assertTrue(f.build_ok);self.assertFalse(f.threats)
        events=[json.loads(text) for _,text in f.events]
        event=next(x for x in events if x.get('name')=='chaos_knight_chaos_bolt')
        self.assertEqual(event['target_state'],'unsupported_target');self.assertFalse(event['use_for_input'])
        self.assertEqual(event['already_hit_header'],'00'*24)
    def test_phase_end_and_cd_never_guess_chaos_target(self):
        self.h.frame(10.,1);self.h.frame(10.04,1)
        f=self.h.frame(10.08,0,20)
        self.assertFalse(f.threats);self.assertIn('цель неизвестна',f.compact_reason)
    def test_chaos_field_is_not_mistaken_for_m_h_target(self):
        fields=self.h.f.combat.classes['C_DOTA_Ability_ChaosKnight_Chaos_Bolt']['fields']
        self.assertNotIn('m_hTarget',fields);self.assertIn('hAlreadyHitList',fields)
    def test_unreadable_chaos_diagnostic_does_not_claim_cast_read(self):
        self.h.entities[2]=(self.h.f.ep,'wrong_ability')
        f=self.h.frame(10.,1)
        self.assertFalse(f.threats);self.assertIn('диагностика недоступна',f.compact_reason)


if __name__=='__main__':unittest.main()
