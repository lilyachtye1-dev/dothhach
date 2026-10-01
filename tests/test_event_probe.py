import math
import sys
import types
import unittest
from unittest.mock import patch
import collect_events as e
from collect_live import ProbeLimit


class EventTests(unittest.TestCase):
    def test_positions_finite_bounded_not_guessed(self):
        self.assertEqual(e.finite_position((10, 20, 30)), [10, 20, 30])
        for value in (None, (1, 2), (math.nan, 0, 0), (math.inf, 0, 0), (16000, 0, 0)):
            self.assertIsNone(e.finite_position(value))

    def probe(self):
        class Probe:
            classes = {'C_BaseEntity': {'fields': {'m_Particles': 1384}},
                       'C_ParticleSystem': {'fields': {'m_bActive': 100}}}
            reads = 0
            def read(self, address, count):
                self.reads += 1
                return bytes(count)
            def runtime_type(self, entity):
                return 'C_ParticleSystem'
        return Probe()

    def dot(self, entries):
        return types.SimpleNamespace(entities=lambda: entries, pos=lambda _: (1., 2., 3.),
              dormant_state=lambda _: True, abilities_of=lambda *args: [], u8=lambda _: 3)

    def run_capture(self, dot, probe):
        now = [0.]
        def sleep(delay): now[0] += max(delay, .001)
        offsets = types.SimpleNamespace(AB_B_IN_ABILITY_PHASE=100, M_I_TEAM_NUM=10)
        with patch.dict(sys.modules, {'dota_esp': offsets}):
            return e.capture(dot, probe, 1, 2, clock=lambda: now[0], sleep=sleep)

    def test_opaque_fields_not_decoded_as_target(self):
        out = e.raw_fields(self.probe(), 0x10000, 'C_ParticleSystem')
        self.assertEqual(out, {'m_bActive': {'offset': 100, 'bytes': '00'}})

    def test_sampling_explicit_no_live_hidden_hero_claim(self):
        frames = self.run_capture(self.dot({1: (0x10000, 'npc_dota_hero_lion')}), self.probe())
        self.assertEqual(len(frames), 2)
        self.assertTrue(frames[0]['complete'])
        hero = frames[0]['heroes'][0]
        self.assertTrue(hero['dormant'])
        self.assertIn('position_may_be_last_seen', hero)
        self.assertNotIn('target', frames[0]['particle_entities'][0])

    def test_read_failure_recorded_without_false_complete(self):
        frames = self.run_capture(self.dot(None), self.probe())
        self.assertFalse(frames[0]['complete'])
        self.assertIn('entity system unavailable', frames[0]['error'])

    def test_budget_limit_not_silently_empty_success(self):
        probe = self.probe()
        def fail(_): raise ProbeLimit('budget')
        probe.runtime_type = fail
        frames = self.run_capture(self.dot({1: (0x10000, 'particle')}), probe)
        self.assertFalse(frames[0]['complete'])
        self.assertEqual(frames[0]['limit'], 'budget')

    def test_particle_output_bounded(self):
        entries = {i: (0x10000 + i * 4096, 'particle') for i in range(300)}
        frames = self.run_capture(self.dot(entries), self.probe())
        self.assertEqual(len(frames[0]['particle_entities']), 32)
        self.assertTrue(frames[0]['particle_limit'])
        self.assertFalse(frames[0]['rtti_scan_complete'])


if __name__ == '__main__': unittest.main()
