"""Exact old/new schema fixtures + fake memory/runtime. Not live attack proof."""
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from helpers import ROOT
import last_hit_build as builds
from last_hit_reader import Reader
from last_hit_stats import LiveStats, SIEGE
import test_last_hit_alpha4 as alpha4


def schema(current=True):
    # The two supplied dumps differ ONLY by two unused empty enum entries.
    # Reconstruct their actual bytes, rather than mock away the SHA gate.
    doc=json.loads((ROOT/'client_dll.json').read_bytes())
    enums=doc['client.dll']['enums']
    removed={'DOTAInternational2025PredictionRankings':{'alignment':18,'members':{},'type':'unknown'},
             'H____WH___H__H_ڹ__':{'alignment':149,'members':{},'type':'unknown'}}
    for key in removed:enums.pop(key,None)
    if not current:enums.update(removed)
    doc['client.dll']['enums']=dict(sorted(enums.items()))
    return json.dumps(doc,ensure_ascii=False,indent=2).encode()


def image(build):return {'timestamp':build.timestamp,'size':build.size,'base':'0x10000000'}


class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.new=schema();cls.old=schema(False)
    def test_reconstructed_fixtures_have_actual_supplied_digests(self):
        self.assertEqual(hashlib.sha256(self.new).hexdigest(),builds.CURRENT.schema)
        self.assertEqual(hashlib.sha256(self.old).hexdigest(),builds.LEGACY.schema)
    def test_accepts_exact_new_tuple_and_globals(self):
        b=builds.CURRENT;self.assertEqual(builds.check_build(self.new,image(b),builds.GLOBALS[b.schema]),b)
    def test_accepts_exact_legacy_tuple_and_globals(self):
        b=builds.LEGACY;self.assertEqual(builds.check_build(self.old,image(b),builds.GLOBALS[b.schema]),b)
    def test_equal_classes_do_not_authorize_unknown_raw_hash(self):
        b=builds.CURRENT
        with self.assertRaises(ValueError):builds.check_build(self.new+b' ',image(b),builds.GLOBALS[b.schema])
    def test_old_pe_with_new_schema_and_inverse_rejected(self):
        for raw,b in ((self.new,builds.LEGACY),(self.old,builds.CURRENT)):
            with self.assertRaises(ValueError):builds.check_build(raw,image(b),builds.GLOBALS[b.schema])
    def test_unknown_timestamp_size_and_malformed_types_rejected(self):
        b=builds.CURRENT
        for key,value in [('timestamp',b.timestamp+1),('size',b.size+4096),('size',str(b.size)),('timestamp',True)]:
            pe=image(b);pe[key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                builds.check_build(self.new,pe,builds.GLOBALS[b.schema])
    def test_every_changed_or_missing_global_rejected(self):
        b=builds.CURRENT
        for key in builds.GLOBALS[b.schema]:
            for value in (None,False,-1):
                g=dict(builds.GLOBALS[b.schema]);g[key]=value
                with self.subTest(key=key,value=value),self.assertRaises(ValueError):builds.check_build(self.new,image(b),g)
    def test_new_schema_does_not_accept_stale_globals(self):
        with self.assertRaises(ValueError):builds.check_build(self.new,image(builds.CURRENT),builds.GLOBALS[builds.LEGACY.schema])
    def test_layout_is_new_build_scoped_and_fresh(self):
        self.assertIsNone(builds.reviewed_layout(builds.LEGACY))
        unknown=builds.Build(builds.CURRENT.schema,123,builds.CURRENT.size,'unreviewed',True)
        self.assertIsNone(builds.reviewed_layout(unknown))
        first=builds.reviewed_layout(builds.CURRENT);first['count']=999
        self.assertEqual(builds.reviewed_layout(builds.CURRENT)['count'],16)
    def test_existing_collectors_keep_old_gate(self):
        from collect_targets import check_build
        check_build(self.old,image(builds.LEGACY))
        with self.assertRaises(ValueError):check_build(self.new,image(builds.CURRENT))


class InitializationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.r=Reader.__new__(Reader);self.r.root=self.root
        self.r.dot=SimpleNamespace();self.r.verify_dot=SimpleNamespace()
        self.r.probe=None;self.r.verify_probe=None;self.r.assets=None;self.r.stats=None
        self.r.stat_cache={};self.r.stats_note='not initialized'
        self.select(builds.CURRENT)
    def select(self,b):
        self.build=b;(self.root/'client_dll.json').write_bytes(schema(b==builds.CURRENT))
        self.profile={'settings':{'live_modifier_layout':None,'auto_cast':True}}
        self.save_profile()
    def save_profile(self):
        (self.root/'ks_profiles.json').write_text(json.dumps(self.profile))
    def initialize(self,asset_error=None,verify_image=None):
        self.p=SimpleNamespace(image=image(self.build))
        self.v=SimpleNamespace(image=verify_image or image(self.build))
        with patch.dict('sys.modules',{'dota_esp':SimpleNamespace(**builds.GLOBALS[self.build.schema])}), \
             patch('last_hit_reader.Probe',side_effect=[self.p,self.v]), \
             patch('last_hit_reader.game_directory',return_value=self.root), \
             patch('last_hit_reader.AttackData',side_effect=asset_error,return_value=SimpleNamespace()) as assets:
            self.r.initialize()
        return assets
    def test_new_build_works_with_importer_null_without_writing_profile(self):
        before=(self.root/'ks_profiles.json').read_bytes();self.initialize()
        self.assertIs(self.r.probe,self.p);self.assertIs(self.r.verify_probe,self.v)
        self.assertIsNotNone(self.r.assets);self.assertIsNotNone(self.r.stats)
        self.assertEqual(self.r.stats.layout['client_timestamp'],1790705915)
        self.assertTrue(self.r.stats.require_siege_modifier)
        self.assertEqual(before,(self.root/'ks_profiles.json').read_bytes())
    def test_new_layout_does_not_depend_on_ks_profile_or_restore_it(self):
        (self.root/'ks_profiles.json').unlink();self.initialize()
        self.assertIsNotNone(self.r.stats);self.assertFalse((self.root/'ks_profiles.json').exists())
    def test_new_layout_does_not_adopt_arbitrary_profile_offsets(self):
        self.profile['settings']['live_modifier_layout']={'count':999};self.save_profile();self.initialize()
        self.assertEqual(self.r.stats.layout['count'],16)
        self.assertEqual(json.loads((self.root/'ks_profiles.json').read_text()),self.profile)
    def test_legacy_null_keeps_public_reader_without_private_approval(self):
        self.select(builds.LEGACY);self.initialize()
        self.assertIsNotNone(self.r.probe);self.assertIsNotNone(self.r.assets);self.assertIsNone(self.r.stats)
        value,note=self.r.modifier_stats((0x20000,0x10002))
        self.assertIsNone(value);self.assertIn('unavailable',note)
    def test_bad_legacy_optional_profile_is_explicit_unavailable(self):
        self.select(builds.LEGACY);self.profile['settings']['live_modifier_layout']={};self.save_profile();self.initialize()
        self.assertIsNone(self.r.stats);self.assertIsNotNone(self.r.probe);self.assertIn('unavailable',self.r.stats_note)
    def test_legacy_rejects_optional_layout_from_another_build(self):
        self.select(builds.LEGACY);self.profile['settings']['live_modifier_layout']=builds.reviewed_layout(builds.CURRENT)
        self.save_profile();self.initialize();self.assertIsNone(self.r.stats)
        self.assertIn('another build',self.r.stats_note)
    def test_missing_assets_publish_no_partial_initialization(self):
        with self.assertRaisesRegex(ValueError,'synthetic assets'):self.initialize(ValueError('synthetic assets'))
        self.assertIsNone(self.r.probe);self.assertIsNone(self.r.verify_probe)
        self.assertIsNone(self.r.assets);self.assertIsNone(self.r.stats)
    def test_failed_verifier_gate_publishes_nothing(self):
        with self.assertRaises(ValueError):self.initialize(verify_image=image(builds.LEGACY))
        self.assertIsNone(self.r.probe);self.assertIsNone(self.r.assets)
    def test_retry_after_failure_rechecks_build_before_capture(self):
        with self.assertRaises(ValueError):self.initialize(ValueError('synthetic assets'))
        (self.root/'client_dll.json').write_bytes(schema()+b' ')
        with self.assertRaises(ValueError):self.initialize()
        self.assertIsNone(self.r.probe)
        (self.root/'client_dll.json').write_bytes(schema());self.initialize();self.assertIsNotNone(self.r.probe)


class CurrentStatsTests(unittest.TestCase):
    def setUp(self):
        self.f=alpha4.LiveStatsTests();self.f.p=alpha4.memory.Probe()
        self.f.c=builds.reviewed_layout(builds.CURRENT);self.f.p.image=image(builds.CURRENT)
        self.f.r=LiveStats(self.f.p.classes,self.f.c,builds.CURRENT.schema,require_siege_modifier=True)
        self.f.hero=0x40000
        self.f.manager=self.f.hero+self.f.p.classes['C_DOTA_BaseNPC']['fields']['m_ModifierManager']
        self.f.data=0x100000;self.f.types={}
        self.f.p.runtime_type=lambda obj:self.f.types.get(obj)
    def test_current_known_owned_siege_field_can_be_read(self):
        self.f.vector([(SIEGE,'incoming_hero_damage_penalty','<f',40)])
        self.assertAlmostEqual(self.f.read(True)[0],.6)
    def test_missing_siege_modifier_is_unknown_not_full_damage(self):
        self.f.vector([])
        with self.assertRaisesRegex(ValueError,'reduction unknown'):self.f.read(True)
    def test_current_duplicate_ias_items_remain_distinct(self):
        self.f.vector([('CDOTA_Modifier_Item_WraithBand','bonus_attack_speed','<i',7)]*2)
        self.assertEqual(self.f.read()[0],14.)
    def test_current_foreign_modifier_owner_still_rejected(self):
        self.f.test_foreign_owner_refused()
    def test_pe_checked_again_on_each_private_read(self):
        self.f.vector([]);self.f.p.image['timestamp']+=1
        with self.assertRaisesRegex(ValueError,'build/layout mismatch'):self.f.read()
    def test_unknown_siege_stays_in_helper_not_in_attack_choices(self):
        self.f.vector([])
        r=Reader.__new__(Reader);r.stats=self.f.r;r.dot=None;r.stat_cache={}
        with patch('last_hit_stats.Probe',return_value=self.f.p):value,note=r.modifier_stats((self.f.hero,0x10002),True)
        self.assertIsNone(value);self.assertIn('unknown',note)
        import test_last_hit as old
        from last_hit_core import Model
        c=old.unit(lane_type='siege',incoming_multiplier=value)
        m=Model();chosen,_=m.choose(old.frame([c]));self.assertIsNone(chosen)
        self.assertEqual(len(m.rows),1);self.assertEqual(m.rows[0]['state'],'damage_unknown')


if __name__=='__main__':unittest.main()
