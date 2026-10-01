"""Regression cases from AUTO ON / 0xffffffff report. No live-game claims."""
from dataclasses import replace
import json
import math
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import test_dodger as existing
import test_live_stacks as stacks
from dodge_diagnostics import CastJournal
from dodge_reader import Frame
from ks_dashboard import dashboard
from ks_core import Action


class ReaderDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.f=existing.ReaderTests();self.f.setUp()
        self.f.field(self.f.cls,'m_hTarget','<I',0xffffffff)
        self.probe=patch.object(self.f.combat.modifiers,'present',return_value=())
        self.mock_probe=self.probe.start();self.addCleanup(self.probe.stop)

    def events(self,frame,kind='cast_fields'):
        return [json.loads(text) for _,text in frame.events if json.loads(text)['kind']==kind]

    def test_invalid_handle_does_not_hide_phase_cd_start(self):
        frame=self.f.frame(10,1)
        text=' '.join(frame.observations)
        for token in ('phase=1','cd=0.000','start=9.000','target=0xffffffff','idx=invalid','invalid_handle'):
            self.assertIn(token,text)
        self.assertNotIn('idx=32767',text)
        self.assertIn('нет валидной цели',frame.compact_reason)
        self.assertTrue(frame.build_ok);self.assertFalse(frame.threats)

    def test_unknown_target_never_becomes_a_threat_on_release(self):
        self.f.frame(10,1);self.f.frame(10.04,1)
        frame=self.f.frame(10.08,0,20)
        self.assertFalse(frame.threats)
        self.assertEqual([(x['phase'],x['cd']) for x in self.events(frame)],[(1,0),(0,20)])

    def test_short_observed_phase_survives_until_later_sender_poll(self):
        self.f.frame(10,0);phase=self.f.frame(10.04,1)
        self.f.frame(10.08,0,20);later=self.f.frame(10.12,0,19.9)
        self.assertEqual([x['phase'] for x in self.events(later)],[0,1,0])
        self.assertEqual(len(self.events(phase)),2)  # old immutable snapshot did not change

    def test_countdown_does_not_flood_journal(self):
        self.f.frame(10,0,20)
        for i in range(1,50):frame=self.f.frame(10+i*.02,0,20-i*.02)
        self.assertEqual(len(self.events(frame)),1)

    def test_unknown_release_cannot_borrow_a_previously_valid_target(self):
        self.f.field(self.f.cls,'m_hTarget','<I',self.f.handle)
        self.f.frame(10,1);self.f.frame(10.04,1)
        self.f.field(self.f.cls,'m_hTarget','<I',0xffffffff)
        self.assertFalse(self.f.frame(10.08,0,20).threats)

    def test_other_target_still_has_cast_diagnostics(self):
        self.f.field(self.f.cls,'m_hTarget','<I',0x8007)
        frame=self.f.frame(10,1)
        self.assertEqual(self.events(frame)[0]['target_state'],'other_index')
        self.assertIn('цель не вы',frame.compact_reason)
        self.assertFalse(frame.threats)

    def test_serial_mismatch_not_reported_as_local_target(self):
        self.f.field(self.f.cls,'m_hTarget','<I',self.f.handle+0x8000)
        frame=self.f.frame(10,1)
        self.assertEqual(self.events(frame)[0]['target_state'],'SERIAL MISMATCH')
        self.assertFalse(frame.threats)

    def test_zero_handle_has_no_real_index(self):
        self.f.field(self.f.cls,'m_hTarget','<I',0)
        event=self.events(self.f.frame(10,0))[0]
        self.assertIsNone(event['target_index']);self.assertEqual(event['target_state'],'invalid_handle')

    def test_gap_is_visible_but_does_not_invent_missing_phase(self):
        self.f.frame(10,0);frame=self.f.frame(10.5,0,20)
        self.assertEqual(len(self.events(frame)),2);self.assertFalse(frame.threats)

    def test_bad_cast_fields_block_even_with_local_handle(self):
        self.f.field(self.f.cls,'m_hTarget','<I',self.f.handle)
        frame=self.f.frame(10,2)
        self.assertEqual(self.events(frame)[0]['target_state'],'invalid_cast_fields')
        self.assertFalse(frame.threats)

    def test_nonfinite_start_cd_fail_closed(self):
        for value in (math.nan,math.inf,-math.inf):
            self.f.field('C_DOTABaseAbility','m_flCastStartTime','<f',value)
            frame=self.f.frame(10,1)
            self.assertFalse(frame.build_ok);self.assertFalse(frame.threats)

    def test_modifier_presence_is_not_permission_for_input(self):
        self.mock_probe.return_value=(('CDOTA_Modifier_Sniper_Assassinate',True),)
        self.f.frame(10,1);self.f.frame(10.04,1);frame=self.f.frame(10.08,0,20)
        self.assertFalse(frame.threats)
        marks=self.events(frame,'modifier_diagnostic')
        self.assertTrue(marks[0]['present']['CDOTA_Modifier_Sniper_Assassinate'])
        self.assertFalse(marks[0]['use_for_input'])

    def test_modifier_probe_is_bounded_per_phase(self):
        for t in (10,10.04,10.08,10.16,10.20,10.24):self.f.frame(t,1)
        self.f.frame(10.28,0,20)
        for t in (10.32,10.36):self.f.frame(t,0,19)
        self.assertEqual(self.mock_probe.call_count,3)

    def test_modifier_error_does_not_disable_existing_detector(self):
        self.mock_probe.side_effect=ValueError('owner mismatch')
        frame=self.f.frame(10,1)
        self.assertTrue(frame.build_ok);self.assertFalse(frame.threats)
        self.assertEqual(self.events(frame,'modifier_diagnostic')[0]['unknown'],'owner mismatch')

    def test_modifier_poll_does_not_depend_on_replicated_phase(self):
        self.mock_probe.return_value=(('CDOTA_Modifier_Sniper_Assassinate',False),)
        self.f.frame(10,0)
        self.mock_probe.return_value=(('CDOTA_Modifier_Sniper_Assassinate',True),)
        frame=self.f.frame(11.01,0)
        marks=self.events(frame,'modifier_diagnostic')
        self.assertEqual([x['present']['CDOTA_Modifier_Sniper_Assassinate'] for x in marks],[False,True])
        self.assertFalse(frame.threats)

    def test_idle_modifier_poll_is_throttled_and_unchanged_result_deduplicated(self):
        self.f.frame(10,0);self.f.frame(10.1,0);self.f.frame(10.2,0)
        frame=self.f.frame(11.01,0)
        self.assertEqual(self.mock_probe.call_count,2)
        self.assertEqual(len(self.events(frame,'modifier_diagnostic')),1)

    def test_no_modifier_probe_when_target_already_verified(self):
        self.f.field(self.f.cls,'m_hTarget','<I',self.f.handle)
        self.f.frame(10,1);self.f.frame(10.04,1);frame=self.f.frame(10.08,0,20)
        self.mock_probe.assert_not_called();self.assertEqual(len(frame.threats),1)


class JournalTests(unittest.TestCase):
    def test_bounded_ordered_immutable_history(self):
        journal=CastJournal()
        for i in range(1000):journal.add('test',i,n=i)
        events=journal.snapshot()
        self.assertEqual(len(events),64);self.assertEqual(events[0][0],937)
        journal.add('test',1001)
        self.assertEqual(events[-1][0],1000)

    def test_removed_sources_are_not_kept_as_active_state(self):
        journal=CastJournal();journal.observe(1,10,(False,),phase=False)
        journal.retain(set());self.assertFalse(journal.states)
        journal.observe(1,10.01,(False,),phase=False)
        self.assertEqual(len(journal.events),2)


class ModifierPresenceTests(unittest.TestCase):
    def setUp(self):
        self.f=stacks.ReplayTests();self.f.setUp()

    def read(self):
        f=self.f
        probe=SimpleNamespace(image=f.sample['client'],read=f.mem.rb,runtime_type=lambda p:f.mem.types.get(p))
        with patch('ks_modifiers.Probe',return_value=probe):
            return dict(f.reader.present(f.mem,f.hero,{stacks.COUNTER,'CDOTA_Modifier_Sniper_Assassinate'}))

    def test_zero_stack_modifier_is_present_not_absent(self):
        import struct
        struct.pack_into('<i',self.f.mem.blocks[self.f.counter],0x84,0)
        result=self.read()
        self.assertIs(result[stacks.COUNTER],True)
        self.assertIs(result['CDOTA_Modifier_Sniper_Assassinate'],False)

    def test_unknown_rtti_is_unknown_not_absence(self):
        del self.f.mem.types[self.f.counter]
        with self.assertRaisesRegex(ValueError,'unknown modifier'):self.read()

    def test_foreign_owner_is_rejected(self):
        import struct
        struct.pack_into('<Q',self.f.mem.blocks[self.f.counter],0x30,self.f.manager+8)
        with self.assertRaisesRegex(ValueError,'owner'):self.read()


class ServiceDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.f=existing.DodgeServiceTests();self.f.setUp();self.addCleanup(self.f.tearDown)
        self.f.tick();self.d=self.f.d;self.ks=self.f.ks

    def test_raw_invalid_target_through_armed_counterspell_service_never_sends(self):
        reader=existing.ReaderTests();reader.setUp()
        reader.me=replace(reader.me,name='npc_dota_hero_antimage',
                          actions=(Action('antimage_counterspell',0x70000,1,0,50),))
        reader.field(reader.cls,'m_hTarget','<I',0xffffffff)
        self.d.enabled=True;self.d.last_hero=None;self.ks.last_hero=None
        with patch.object(reader.combat.modifiers,'present',return_value=()):
            for now,phase,cd in ((10.2,1,0),(10.24,1,0),(10.28,0,20)):
                frame=reader.frame(now,phase,cd)
                heroes=[dict(is_local=True,_ks=replace(reader.me,at=now),_ks_viewport=(1920,1080),_dodge=frame),
                        dict(_ks=replace(reader.enemy,at=now))]
                with patch('dodge_service.time.perf_counter',return_value=now):self.ks.tick(heroes,0)
                self.assertTrue(self.d.enabled);self.assertFalse(self.f.b.calls)
                self.assertIn('нет валидной цели',self.d.dashboard(heroes)['compact_reason'])

    def test_transition_logs_bypass_heartbeat_but_do_not_repeat(self):
        self.d.enabled=True
        self.f.h[0]['_dodge']=replace(self.f.h[0]['_dodge'],events=((1,'phase=1'),(2,'phase=0 cd=20')))
        with patch.object(self.ks.log,'info') as log,patch('dodge_service.time.perf_counter',return_value=10.11):
            self.d.trace(self.f.h,0);self.d.trace(self.f.h,0)
        events=[str(call) for call in log.call_args_list if '[DODGER OBS #' in str(call)]
        self.assertEqual(len(events),2)

    def test_overflow_is_reported_not_silently_hidden(self):
        self.d.enabled=True
        self.f.h[0]['_dodge']=replace(self.f.h[0]['_dodge'],events=((9,'phase=1'),))
        with patch.object(self.ks.log,'warning') as log:
            self.d.trace(self.f.h,0)
        self.assertIn('overflow',log.call_args[0][0]);self.assertEqual(log.call_args[0][1],8)

    def test_compact_reason_exposes_unknown_target_while_armed(self):
        self.d.enabled=True;self.d.status='ON · Ожидаем угрозу'
        self.f.h[0]['_dodge']=replace(self.f.h[0]['_dodge'],compact_reason='Assassinate: нет валидной цели')
        data=dashboard(self.ks,self.f.h,0,now=10.1)
        self.assertFalse(data['armed']);self.assertTrue(data['dodger']['armed'])
        self.assertEqual(data['dodger']['compact_reason'],'Assassinate: нет валидной цели')
        self.assertEqual(data['dodger']['event_age'],-1)

    def test_manual_pause_takes_priority_over_target_reason(self):
        self.d.status='ON · пауза: смерть/каст/ручной ввод'
        self.f.h[0]['_dodge']=replace(self.f.h[0]['_dodge'],compact_reason='Assassinate: нет валидной цели')
        self.assertIn('ручной ввод',self.d.dashboard(self.f.h)['compact_reason'])

    def test_sent_history_has_age_and_does_not_claim_successful_dodge(self):
        self.f.arm();self.f.tick()
        with patch('dodge_service.time.perf_counter',return_value=10.6):data=self.d.dashboard(self.f.h)
        self.assertEqual(data['last_action'],'SENT · Manta Style')
        self.assertAlmostEqual(data['event_age'],.5)

    def test_preview_history_explicitly_has_no_input(self):
        self.ks.settings.auto_cast=False;self.f.arm();self.f.tick()
        data=self.d.dashboard(self.f.h)
        self.assertIn('PREVIEW',data['last_action']);self.assertIn('без ввода',data['last_action'])
        self.assertFalse(self.f.b.calls)

    def test_hero_change_clears_last_action(self):
        self.f.arm();self.f.tick();self.f.me=replace(self.f.me,entity=101)
        self.f.tick(10.2)
        self.assertNotIn('SENT',self.d.last_action);self.assertEqual(self.d.last_event_at,0)


if __name__=='__main__':unittest.main()
