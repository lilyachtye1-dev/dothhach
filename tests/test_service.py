from dataclasses import replace
from pathlib import Path
from contextlib import redirect_stdout
import io
import tempfile
import json
import unittest
from unittest.mock import patch
from helpers import ROOT, fixture, fake_module
from ks_service import KillSteal
from ks_core import Action


class FakeInput:
    def __init__(self):
        self.keys=set()
        self.focus=True
        self.calls=[]
        self.result='sent'
    def down(self,key): return key in self.keys
    def context(self): return (123,100,100,1920,1080) if self.focus else None
    def hard_stop_reason(self):
        return ','.join(str(k) for k in (13,9,35) if k in self.keys)
    def interruption_reason(self):
        return ','.join(str(k) for k in (1,2,16,17,18,13,9,91,92) if k in self.keys)
    def interrupted(self): return bool(self.interruption_reason())
    def cast(self,d,s,context,armed):
        if not armed(): return 'skipped','not armed'
        self.calls.append(d)
        return self.result,'fake backend only'


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        root=Path(self.tmp.name)
        for name in ('ks_profiles.json','client_dll.json'):
            (root/name).write_bytes((ROOT/name).read_bytes())
        self.b=FakeInput()
        self.ks=KillSteal(root,fake_module(),self.b)
        self.assertEqual(self.ks.error,'')
        me,e=fixture()
        self.heroes=[dict(is_local=True,_ks=me,_ks_viewport=(1920,1080)),dict(_ks=e)]
        self.time=10.1

    def tearDown(self):
        for h in list(self.ks.log.handlers):
            h.close(); self.ks.log.removeHandler(h)
        self.tmp.cleanup()

    def tick(self,at=None,age=.01,refresh=True):
        if at is not None: self.time=at
        if refresh:
            for h in self.heroes:
                if h.get('_ks'): h['_ks']=replace(h['_ks'],at=self.time-.01)
        with patch('ks_service.time.perf_counter',return_value=self.time):
            self.ks.tick(self.heroes,age)

    def turn_on(self):
        # Simulate the program's arm button (its deferred-request path is tested separately).
        self.tick(10.1)
        self.ks.toggle.enabled=True
        self.ks.off_reason=''

    def set_cd(self,cd):
        me=self.heroes[0]['_ks']
        self.heroes[0]['_ks']=replace(me,actions=(replace(me.actions[0],cooldown=cd),))

    def test_start_off_no_input(self):
        self.tick()
        self.assertFalse(self.ks.toggle.enabled)
        self.assertEqual(self.b.calls,[])

    def test_program_armed_state_runs_without_hotkey(self):
        self.turn_on(); self.tick(10.14)
        self.assertEqual(len(self.b.calls),1)
        self.assertTrue(self.ks.toggle.enabled)
        self.assertIn('SENT',self.ks.status)

    def test_f6_does_not_arm_or_toggle_killsteal(self):
        self.tick(10.1)
        self.b.keys.add(117); self.tick(10.12)
        self.assertFalse(self.ks.toggle.enabled)
        self.b.keys.remove(117)
        self.ks.toggle.enabled=True
        self.b.keys.add(117); self.tick(10.14)
        self.assertTrue(self.ks.toggle.enabled)

    def test_sent_status_is_not_printed_twice(self):
        self.turn_on()
        self.ks.last_log='older status'
        self.ks.last_log_at=self.time-.9
        out=io.StringIO()
        with redirect_stdout(out):
            self.tick(10.14)
        self.assertEqual(out.getvalue().count('KS5 SENT'),1)

    def test_holding_f6_does_not_arm_or_cast(self):
        self.tick(); self.b.keys.add(117)
        for at in (10.12,10.15,10.2): self.tick(at)
        self.assertFalse(self.ks.toggle.enabled)
        self.assertEqual(self.b.calls,[])

    def test_f6_does_not_turn_off_ui_armed_state(self):
        self.turn_on(); self.tick(10.14)
        self.b.keys.add(117); self.tick(10.16)
        self.assertTrue(self.ks.toggle.enabled)

    def test_automatic_second_cast_after_confirmed_cd_and_recovery(self):
        self.turn_on(); self.tick(10.14)
        self.set_cd(5); self.tick(10.2)
        self.assertIsNone(self.ks.ledger.pending)
        self.set_cd(0); self.tick(11.2)
        self.assertEqual(len(self.b.calls),2)
        self.assertTrue(self.ks.toggle.enabled)

    def test_no_cd_no_spam(self):
        self.turn_on(); self.tick(10.14)
        for t in (10.2,11.,12.,13.,14.): self.tick(t)
        self.assertEqual(len(self.b.calls),1)
        self.assertIn(self.heroes[0]['_ks'].actions[0].identity,self.ks.ledger.quarantined)

    def test_focus_off_no_auto_resume(self):
        self.turn_on()
        self.b.focus=False; self.tick(10.14)
        self.assertFalse(self.ks.toggle.enabled)
        self.b.focus=True; self.tick(10.16)
        self.assertFalse(self.ks.toggle.enabled)
        self.assertEqual(self.b.calls,[])

    def test_escape_does_not_turn_off_killsteal(self):
        self.turn_on()
        self.b.keys.add(27); self.tick(10.14)
        self.assertTrue(self.ks.toggle.enabled)
        self.b.keys.remove(27)

    def test_enter_tab_preserve_enabled_and_resume_on_release(self):
        for code in (13,9):
            with self.subTest(key=code):
                self.b.keys={code};self.ks.toggle.enabled=True
                self.tick(10.14)
                self.assertTrue(self.ks.toggle.enabled)
                self.assertEqual(self.b.calls,[])
        self.b.keys.clear();self.tick(10.15)
        self.assertEqual(len(self.b.calls),1)

    def test_manual_mouse_does_not_pause_or_disarm(self):
        self.turn_on()
        self.b.keys.update((1,2));self.tick(10.14)
        self.assertTrue(self.ks.toggle.enabled)
        self.assertEqual(len(self.b.calls),1)
        self.assertIn('SENT',self.ks.status)

    def test_held_own_spell_key_is_not_pressed_again(self):
        self.turn_on()
        self.b.keys.add(82); self.tick(10.14)
        self.assertEqual(self.b.calls,[])
        self.assertTrue(self.ks.toggle.enabled)

    def test_other_spell_key_does_not_pause(self):
        self.turn_on()
        self.b.keys.update((ord('E'),ord('Q'),18,2)); self.tick(10.14)
        self.assertEqual(len(self.b.calls),1)
        self.assertTrue(self.ks.toggle.enabled)

    def test_stale_short_pause_recovers(self):
        self.turn_on(); self.tick(10.14,age=1.)
        self.assertTrue(self.ks.toggle.enabled)
        self.assertEqual(self.b.calls,[])
        self.tick(10.2)
        self.assertEqual(len(self.b.calls),1)

    def test_long_stale_turns_off(self):
        self.turn_on(); self.tick(10.14,age=1.)
        self.tick(12.3,age=3.)
        self.assertFalse(self.ks.toggle.enabled)
        self.assertEqual(self.b.calls,[])

    def test_death_pauses_and_resumes_without_rearming(self):
        self.turn_on()
        self.heroes[0]['_ks']=replace(self.heroes[0]['_ks'],alive=False)
        self.tick(10.14)
        self.assertTrue(self.ks.toggle.enabled)
        self.assertIn('герой мёртв',self.ks.status)
        self.assertEqual(self.b.calls,[])
        self.heroes[0]['_ks']=replace(self.heroes[0]['_ks'],alive=True)
        self.tick(10.2)
        self.assertTrue(self.ks.toggle.enabled)
        self.assertEqual(len(self.b.calls),1)

    def test_hero_change_turns_off(self):
        self.turn_on()
        self.heroes[0]['_ks']=replace(self.heroes[0]['_ks'],entity=999)
        self.tick(10.14)
        self.assertFalse(self.ks.toggle.enabled)

    def test_preview_never_sends(self):
        self.ks.settings.auto_cast=False
        self.turn_on(); self.tick(10.14)
        self.assertEqual(self.b.calls,[])
        self.assertEqual(self.ks.target_idx,2)
        self.assertIn('БЕЗ ВВОДА',self.ks.status)

    def test_unknown_target_inventory_still_blocked(self):
        self.heroes[1]['_ks']=replace(self.heroes[1]['_ks'],inventory_known=False)
        self.turn_on(); self.tick(10.14)
        self.assertEqual(self.b.calls,[])
        self.assertIn('инвентарь неизвестен',self.ks.status)

    def test_skipped_input_does_not_create_pending(self):
        self.b.result='skipped'
        self.turn_on(); self.tick(10.14)
        self.assertIsNone(self.ks.ledger.pending)
        self.assertFalse(self.ks.ledger.quarantined)
        self.assertTrue(self.ks.toggle.enabled)

    def test_failed_input_off_and_no_automatic_retry(self):
        self.b.result='failed'
        self.turn_on(); self.tick(10.14)
        self.assertFalse(self.ks.toggle.enabled)
        self.assertIsNotNone(self.ks.ledger.pending)
        self.tick(12.5)
        self.assertEqual(len(self.b.calls),1)
        self.assertTrue(self.ks.ledger.quarantined)

    def test_wire_and_status(self):
        self.ks.status='a\tb\nc'; self.ks.detail='d\ne\tf'
        fields=self.ks.wire_fields().split('\t')
        self.assertEqual(len(fields),5)
        self.assertNotIn('\n',self.ks.wire_fields())

    def test_global_offscreen_runs_through_toggle_service(self):
        me=self.heroes[0]['_ks']
        self.heroes[0]['_ks']=replace(me,name='npc_dota_hero_zuus',
            actions=(Action('zuus_thundergods_wrath',300,3,0,100),))
        self.heroes[1]['_ks']=replace(self.heroes[1]['_ks'],screen=())
        self.turn_on(); self.tick(10.14)
        self.assertEqual(len(self.b.calls),1)
        self.assertEqual(self.b.calls[0].profile.cast,'global')
        self.assertEqual(self.b.calls[0].target.screen,())

    def test_menu_file_disarms_without_clearing_pending(self):
        self.turn_on();self.tick(10.14)
        pending=self.ks.ledger.pending
        self.ks.ui_control_path.write_text(json.dumps(dict(session=self.ks.ui_session,revision=1,
            disabled=['lion_finger_of_death'])))
        self.tick(10.5)
        self.assertFalse(self.ks.toggle.enabled)
        self.assertIn('lion_finger_of_death',self.ks.ui_disabled)
        self.assertIs(self.ks.ledger.pending,pending)
        self.assertEqual(len(self.b.calls),1)

    def test_menu_arm_waits_for_closed_menu_and_safe_live_data(self):
        self.ks.ui_control_path.write_text(json.dumps(dict(session=self.ks.ui_session,revision=1,
            disabled=[],menu_open=True,arm_after_close=True)))
        self.tick(10.1)
        self.assertFalse(self.ks.toggle.enabled)
        self.assertTrue(self.ks.ui_arm_pending)
        self.ks.ui_control_path.write_text(json.dumps(dict(session=self.ks.ui_session,revision=2,
            disabled=[],menu_open=False)))
        self.tick(10.16)
        self.assertTrue(self.ks.toggle.enabled)
        self.assertFalse(self.ks.ui_arm_pending)
        self.assertEqual(len(self.b.calls),1)

    def test_old_menu_session_file_ignored(self):
        self.ks.ui_control_path.write_text(json.dumps(dict(session='old-session',revision=999,
            disabled=['lion_finger_of_death'])))
        self.turn_on();self.tick(10.14)
        self.assertEqual(self.ks.ui_revision,0)
        self.assertFalse(self.ks.ui_disabled)
        self.assertEqual(len(self.b.calls),1)

    def test_menu_open_flag_blocks_even_when_game_still_focused(self):
        self.ks.ui_menu_open=True
        self.tick(10.1)
        self.b.keys.add(117);self.tick(10.12)
        self.assertFalse(self.ks.toggle.enabled)
        self.b.keys.remove(117);self.tick(10.14)
        self.assertEqual(self.b.calls,[])
        self.assertIn('меню',self.ks.status)


if __name__=='__main__': unittest.main()
