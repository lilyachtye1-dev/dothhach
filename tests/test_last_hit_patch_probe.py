"""Synthetic/offline checks only. No claim of new-build runtime approval."""
import ast
import json
from pathlib import Path
import tempfile
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from helpers import ROOT
import last_hit_patch_probe as tool
import test_last_hit as previous


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        (self.root/'client_dll.json').write_bytes((ROOT/'client_dll.json').read_bytes()+b'\n ')
        self.source='\n'.join(k+' = '+str(v) for k,v in tool.EXPECTED_GLOBALS.items())
        (self.root/'dota_esp.py').write_text(self.source)
        (self.root/'ks_profiles.json').write_text('{"live_modifier_layout":null,"auto_cast":true}')
        self.image={'base':'0x10000000','timestamp':123,'size':0x1000000}  # synthetic
    def result(self,collector):
        with patch('builtins.print'):
            code=tool.run(self.root,collector)
        return code,json.loads((self.root/tool.REPORT).read_text())
    def collect(self,report,raw,classes):report['client_image']=self.image.copy()
    def test_semantically_identical_schema_is_not_byte_identical(self):
        check,_,_=tool.static_report(self.root)
        self.assertTrue(check['class_layout_matches']);self.assertFalse(check['raw_schema_matches_supplied'])
        self.assertFalse(check['global_mismatches'])
    def test_success_never_approves_or_changes_installation(self):
        original={p.name:p.read_bytes() for p in self.root.iterdir()}
        code,r=self.result(self.collect)
        self.assertEqual(code,0);self.assertFalse(r['approved_for_input'])
        self.assertFalse(r['profile_changes']);self.assertFalse(r['writes_to_game'])
        for name,data in original.items():self.assertEqual((self.root/name).read_bytes(),data)
        self.assertEqual({p.name for p in self.root.iterdir()},set(original)|{tool.REPORT})
    def test_wrong_global_blocks_all_process_reads(self):
        (self.root/'dota_esp.py').write_text(self.source.replace('106159024','106158864'))
        collector=Mock();code,r=self.result(collector)
        collector.assert_not_called();self.assertEqual(code,2)
        self.assertIn('DW_GAME_ENTITY_SYSTEM',r['installation']['global_mismatches'])
    def test_changed_field_blocks_all_process_reads(self):
        p=self.root/'client_dll.json';data=json.loads(p.read_bytes())
        data['client.dll']['classes']['C_BaseEntity']['fields']['m_iHealth']+=4
        p.write_text(json.dumps(data));collector=Mock();code,r=self.result(collector)
        collector.assert_not_called();self.assertEqual(code,2);self.assertFalse(r['approved_for_input'])
    def test_malformed_json_still_creates_report(self):
        (self.root/'client_dll.json').write_text('{');collector=Mock();code,r=self.result(collector)
        self.assertEqual(code,2);collector.assert_not_called();self.assertIn('error',r)
    def test_missing_constant_still_creates_report(self):
        (self.root/'dota_esp.py').write_text('');code,r=self.result(Mock())
        self.assertEqual(code,2);self.assertIn('Missing constants',r['error'])
    def test_failure_after_pe_retains_identity(self):
        def collector(report,*args):
            report['client_image']=self.image.copy();raise ValueError('synthetic hero failure')
        code,r=self.result(collector)
        self.assertEqual(code,2);self.assertEqual(r['client_image'],self.image)
        self.assertFalse(r['approved_for_input'])
    def test_no_process_is_reported_not_approved(self):
        code,r=self.result(Mock(side_effect=ProcessLookupError('synthetic process absent')))
        self.assertEqual(code,2);self.assertNotIn('client_image',r);self.assertFalse(r['approved_for_input'])
    def test_no_pe_is_not_success(self):
        code,r=self.result(lambda *args:None)
        self.assertEqual(code,2);self.assertIn('PE data not collected',r['error'])
    def test_oversize_optional_data_preserves_pe(self):
        def collector(report,*args):
            report['client_image']=self.image.copy();report['nearby_targets']=['x'*300000]
        code,r=self.result(collector)
        self.assertEqual(code,2);self.assertEqual(r['client_image'],self.image)
        self.assertLess((self.root/tool.REPORT).stat().st_size,256*1024)
    def test_nan_optional_data_preserves_pe(self):
        def collector(report,*args):
            report['client_image']=self.image.copy();report['nearby_targets']=[float('nan')]
        code,r=self.result(collector)
        self.assertEqual(code,2);self.assertEqual(r['client_image'],self.image)
    def test_candidate_is_fresh_and_never_saved(self):
        baseline=dict(tool.CANDIDATE_LAYOUT)
        first=tool.candidate_config(self.image,'a'*64);first['count']=999
        self.assertEqual(tool.CANDIDATE_LAYOUT,baseline)
        second=tool.candidate_config(self.image,'b'*64)
        self.assertEqual(second['count'],16);self.assertEqual(second['schema_sha256'],'b'*64)
        self.assertEqual(second['client_timestamp'],123)
    def test_no_sender_worker_or_configuration_mutation(self):
        tree=ast.parse((ROOT/'last_hit_patch_probe.py').read_text())
        calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call)]
        names=[n.func.id if isinstance(n.func,ast.Name) else n.func.attr if isinstance(n.func,ast.Attribute) else '' for n in calls]
        for forbidden in ('SendInput','write_int','write_float','write_bytes_game','Controller','Thread','start','check_build'):
            self.assertNotIn(forbidden,names)
        self.assertNotIn('Reader',names)  # only __new__, never worker-starting constructor
        source=(ROOT/'last_hit_patch_probe.py').read_text()
        self.assertIn('r.stats=None',source);self.assertNotIn('ks_profiles.json',source)


class HeroTests(unittest.TestCase):
    def setUp(self):
        self.f=previous.ReaderTests();self.f.setUp()
        self.f.field('C_DOTA_BaseNPC_Hero','m_flAgilityTotal','<f',31.)
        self.f.p.runtime_type=lambda ep:'C_DOTA_Unit_Hero_AntiMage'
        self.stats=SimpleNamespace(read=Mock(return_value=(15.,'synthetic supported IAS')))
    def sample(self):return tool.sample_hero(self.f.d,self.f.p,self.f.ep,self.f.name,self.stats)
    def test_public_fields_and_partial_private_result(self):
        r=self.sample();self.assertEqual(r['fields']['m_flAgilityTotal'],31.)
        self.assertEqual(r['private_candidate']['structural_check'],'passed')
        self.assertFalse(r['private_candidate']['approved_for_input'])
        self.assertEqual(r['private_candidate']['partial_ias'],15.)
    def test_wrong_runtime_type_blocks_private_reads(self):
        self.f.p.runtime_type=lambda ep:'C_DOTA_BaseNPC_Creep'
        with self.assertRaisesRegex(ValueError,'RTTI'):self.sample()
        self.stats.read.assert_not_called()
    def test_invalid_identity_blocks_private_reads(self):
        self.f.put_identity(self.f.ep,self.f.ident,0x10003)
        with self.assertRaisesRegex(ValueError,'identity'):self.sample()
        self.stats.read.assert_not_called()
    def test_missing_public_field_is_not_zero(self):
        del self.f.p.memory[self.f.ep+self.f.p.classes['C_DOTA_BaseNPC_Hero']['fields']['m_flAgilityTotal']]
        with self.assertRaisesRegex(ValueError,'unreadable'):self.sample()
    def test_nonfinite_stat_blocks_private_reads(self):
        self.f.field('C_DOTA_BaseNPC_Hero','m_flAgilityTotal','<f',float('nan'))
        with self.assertRaisesRegex(ValueError,'nonfinite'):self.sample()
        self.stats.read.assert_not_called()
    def test_dead_hero_rejected(self):
        self.f.field('C_BaseEntity','m_iHealth','<i',0)
        with self.assertRaisesRegex(ValueError,'not alive'):self.sample()
    def test_private_failure_retains_public_fields(self):
        self.stats.read.side_effect=ValueError('synthetic owner mismatch');r=self.sample()
        self.assertIn('fields',r);self.assertEqual(r['private_candidate']['structural_check'],'failed')
        self.assertFalse(r['private_candidate']['approved_for_input'])
    def test_recycled_hero_during_private_read_is_rejected(self):
        def recycle(*args):
            self.f.put_identity(self.f.ep,self.f.ident,0x20002)
            return 15.,'synthetic'
        self.stats.read.side_effect=recycle;r=self.sample()
        self.assertEqual(r['private_candidate']['structural_check'],'failed')
        self.assertIn('changed',r['private_candidate']['reason'])


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.f=previous.ReaderTests();self.f.setUp();self.d=self.f.d
        self.f.name='npc_dota_hero_antimage';self.f.entities[2]=(self.f.ep,self.f.name)
        self.f.field('C_DOTA_BaseNPC_Hero','m_flAgilityTotal','<f',31.)
        self.d.client=0x10000000;self.d.pm=SimpleNamespace(process_id=123)
        self.d.rb=self.f.p.read
        dos=bytearray(64);dos[:2]=b'MZ';struct.pack_into('<I',dos,0x3c,0x80)
        pe=bytearray(256);pe[:4]=b'PE\0\0';struct.pack_into('<I',pe,8,123)
        struct.pack_into('<H',pe,24,0x20b);struct.pack_into('<I',pe,80,0x1000000)
        self.f.p.put(self.d.client,bytes(dos));self.f.p.put(self.d.client+0x80,bytes(pe))
        self.hero={'ent':self.f.ep,'idx':2,'is_local':True,'alive':True,'designer':self.f.name}
        self.d.snapshot=Mock(return_value={'heroes':[self.hero],'ents':{},'local':{'hero_assigned':2}})
    def collect(self,report):
        with patch.dict('sys.modules',{'dota_esp':SimpleNamespace(Dot=lambda:self.d)}), \
                patch('collect_live.Probe.runtime_type',return_value='C_DOTA_Unit_Hero_AntiMage'), \
                patch('last_hit_patch_probe.time.sleep'):
            tool.collect(report,(ROOT/'client_dll.json').read_bytes(),self.f.p.classes)
    def test_actual_collector_reads_pe_and_three_samples_without_input(self):
        report={};self.collect(report)
        self.assertEqual(report['client_image']['timestamp'],123)
        self.assertEqual(len(report['hero_samples']),3)
        for row in report['hero_samples']:
            self.assertEqual(row['fields']['m_flAgilityTotal'],31.)
            self.assertFalse(row['private_candidate']['approved_for_input'])
            self.assertEqual(row['private_candidate']['structural_check'],'failed')
        self.assertEqual(report['nearby_targets'],[])
        self.d.snapshot.assert_called_once_with(full_scan=True)
    def test_no_hero_still_returns_pe(self):
        self.d.snapshot.return_value={'heroes':[]};report={};self.collect(report)
        self.assertEqual(report['client_image']['timestamp'],123)
        self.assertNotIn('hero_samples',report)
    def test_snapshot_failure_does_not_erase_pe(self):
        self.d.snapshot.side_effect=ValueError('synthetic snapshot failure');report={}
        with self.assertRaisesRegex(ValueError,'snapshot failure'):self.collect(report)
        self.assertEqual(report['client_image']['timestamp'],123)


if __name__=='__main__':unittest.main()
