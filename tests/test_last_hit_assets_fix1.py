"""FIX1: split installation NPC tables, not imported game balance fixtures."""
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from helpers import ROOT
from last_hit_assets import AttackData, VPK, kv1

HERO='npc_dota_hero_antimage'
BASE='"DOTAHeroes" {"npc_dota_hero_base" {"AttackAnimationPoint" ".31" "MovementTurnRate" ".61" "ProjectileSpeed" "777"}}'
AM='"DOTAHeroes" {"npc_dota_hero_antimage" {"AttackAnimationPoint" ".27"}}'
MANIFEST='#base "heroes/base.txt"\n#base "heroes/am.txt"\n"DOTAHeroes" {"Version" "1"}'
UNITS='"DOTAUnits" {"npc_dota_units_base" {"AttackAnimationPoint" ".51" "ProjectileSpeed" "888"} "npc_dota_badguys_tower1_mid" {"BaseClass" "npc_dota_tower"}}'


def files():
    return {'scripts/npc/npc_heroes.txt':MANIFEST,'scripts/npc/heroes/base.txt':BASE,
            'scripts/npc/heroes/am.txt':AM,'scripts/npc/npc_units.txt':UNITS}


def loose(folder,mapping):
    for key,value in mapping.items():
        p=Path(folder)/key;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(value,encoding='utf-8')


def archive(folder,mapping,version=2,external=False):
    groups={};body=b'';tree=b'txt\0'
    for key,value in mapping.items():
        p=Path(key);groups.setdefault(p.parent.as_posix(),[]).append((p.stem,value.encode()))
    for directory,rows in groups.items():
        tree+=directory.encode()+b'\0'
        for name,data in rows:
            pre=data[:7];rest=data[7:]
            tree+=name.encode()+b'\0'+struct.pack('<IHHIIH',zlib.crc32(data),len(pre),0 if external else 0x7fff,len(body),len(rest),0xffff)+pre
            body+=rest
        tree+=b'\0'
    tree+=b'\0\0'
    header=struct.pack('<III',0x55aa1234,version,len(tree))+(bytes(16) if version==2 else b'')
    p=Path(folder)/'pak01_dir.vpk';p.write_bytes(header+tree+(b'' if external else body))
    if external:(Path(folder)/'pak01_000.vpk').write_bytes(body)
    return p


class SplitNPCTests(unittest.TestCase):
    def test_reproduces_old_empty_manifest_failure_then_loads_antimage(self):
        # Old loader saw only Version and no hero: exact missing-row mechanism.
        old=kv1(MANIFEST)['DOTAHeroes']
        self.assertNotIn(HERO,old)
        with tempfile.TemporaryDirectory() as t:
            loose(t,files());a=AttackData(t)
            self.assertEqual(a.timing(HERO,False),(.27,.61,0))
            self.assertEqual(a.file_count,4)
    def test_split_vpk_v1_v2_preload_embedded_and_external(self):
        for version in (1,2):
            for external in (False,True):
                with self.subTest(version=version,external=external),tempfile.TemporaryDirectory() as t:
                    archive(t,files(),version,external);a=AttackData(t)
                    self.assertEqual(a.timing(HERO,True),(.27,.61,777))
                    self.assertEqual(set(a.sources.values()),{'VPK'})
    def test_one_vpk_index_for_all_dependencies(self):
        with tempfile.TemporaryDirectory() as t:
            archive(t,files())
            with patch('last_hit_assets.VPK',wraps=VPK) as indexed:
                AttackData(t);self.assertEqual(indexed.call_count,1)
    def test_loose_manifest_with_packed_dependencies(self):
        with tempfile.TemporaryDirectory() as t:
            archive(t,files());loose(t,{'scripts/npc/npc_heroes.txt':MANIFEST})
            a=AttackData(t);self.assertEqual(a.timing(HERO,True),(.27,.61,777))
            self.assertEqual(a.sources['scripts/npc/npc_heroes.txt'],'loose')
    def test_loose_hero_override_takes_precedence_over_vpk(self):
        with tempfile.TemporaryDirectory() as t:
            archive(t,files());loose(t,{'scripts/npc/heroes/am.txt':AM.replace('.27','.29')})
            self.assertEqual(AttackData(t).timing(HERO,True),(.29,.61,777))
    def test_units_inherit_their_own_defaults_not_hero_defaults(self):
        with tempfile.TemporaryDirectory() as t:
            loose(t,files());a=AttackData(t)
            self.assertEqual(a.timing('npc_dota_badguys_tower1_mid',True,False),(.51,1.,888))
            self.assertRaises(ValueError,a.timing,'npc_dota_badguys_tower1_mid',True,True)
    def test_local_fields_override_base_recursively(self):
        m=files();m['scripts/npc/npc_heroes.txt']=MANIFEST.replace('"Version" "1"','"Version" "1" "npc_dota_hero_antimage" {"MovementTurnRate" ".73"}')
        with tempfile.TemporaryDirectory() as t:
            loose(t,m);self.assertEqual(AttackData(t).timing(HERO,True),(.27,.73,777))
    def test_nested_relative_dependencies_and_repeated_include(self):
        m=files();m['scripts/npc/npc_heroes.txt']='#include "heroes/list.txt"\n#include "heroes/am.txt"\n"DOTAHeroes" {}'
        m['scripts/npc/heroes/list.txt']='#base "./base.txt"\n"DOTAHeroes" {}'
        with tempfile.TemporaryDirectory() as t:
            loose(t,m);self.assertEqual(AttackData(t).timing(HERO,True),(.27,.61,777))
    def test_first_base_value_wins_without_losing_other_rows(self):
        m=files();m['scripts/npc/npc_heroes.txt']='#base "heroes/am.txt"\n#base "heroes/other.txt"\n#base "heroes/base.txt"\n"DOTAHeroes" {}'
        m['scripts/npc/heroes/other.txt']=AM.replace('.27','.91').replace('}}','} "npc_dota_hero_lion" {"AttackAnimationPoint" ".49"}}')
        with tempfile.TemporaryDirectory() as t:
            loose(t,m);a=AttackData(t)
            self.assertEqual(a.timing(HERO,False)[0],.27)
            self.assertEqual(a.timing('npc_dota_hero_lion',False)[0],.49)
    def test_directive_cycle_is_refused(self):
        m=files();m['scripts/npc/heroes/am.txt']='#base "../npc_heroes.txt"\n'+AM
        with tempfile.TemporaryDirectory() as t:
            loose(t,m)
            with self.assertRaisesRegex(ValueError,'cycle'):AttackData(t)
    def test_missing_dependency_is_named_not_silently_ignored(self):
        m=files();del m['scripts/npc/heroes/am.txt']
        with tempfile.TemporaryDirectory() as t:
            archive(t,m)
            with self.assertRaisesRegex(ValueError,'heroes/am.txt'):AttackData(t)
    def test_absolute_and_escaping_paths_are_refused(self):
        for name in ('../../outside.txt','/tmp/a.txt','C:\\outside.txt','https://host/a.txt'):
            with self.subTest(name=name),tempfile.TemporaryDirectory() as t:
                m=files();m['scripts/npc/npc_heroes.txt']='#base "'+name+'"\n"DOTAHeroes" {}';loose(t,m)
                with self.assertRaises(ValueError):AttackData(t)
    def test_crc_mismatch_in_dependency_stops_loading(self):
        with tempfile.TemporaryDirectory() as t:
            p=archive(t,files(),external=True);segment=Path(t)/'pak01_000.vpk'
            raw=bytearray(segment.read_bytes());raw[1]^=1;segment.write_bytes(raw)
            with self.assertRaisesRegex(ValueError,'CRC'):AttackData(t)
    def test_flat_old_format_remains_supported(self):
        with tempfile.TemporaryDirectory() as t:
            m=files();m['scripts/npc/npc_heroes.txt']='"DOTAHeroes" {'+BASE.split('{',1)[1].rsplit('}',1)[0]+AM.split('{',1)[1].rsplit('}',1)[0]+'}'
            loose(t,m);a=AttackData(t);self.assertEqual(a.file_count,2)
            self.assertEqual(a.timing(HERO,True),(.27,.61,777))
    def test_path_through_symlink_cannot_leave_installation(self):
        with tempfile.TemporaryDirectory() as t,tempfile.TemporaryDirectory() as outside:
            m=files();del m['scripts/npc/heroes/am.txt'];loose(t,m)
            dest=Path(outside)/'am.txt';dest.write_text(AM)
            try:(Path(t)/'scripts/npc/heroes/am.txt').symlink_to(dest)
            except OSError:self.skipTest('symlinks unavailable')
            with self.assertRaisesRegex(ValueError,'leaves installation'):AttackData(t)
    def test_comments_quoted_slashes_empty_values_and_braces(self):
        text='/* comment */ "x" { "url" "https://a/b" "empty" "" "brace" "}" // tail\n }'
        self.assertEqual(kv1(text),{'x':{'url':'https://a/b','empty':'','brace':'}'}})
    def test_malformed_quotes_and_missing_values_refused(self):
        for text in ('"x" {"y" }','"x" "unterminated','/* never closed'):
            with self.subTest(text=text),self.assertRaises(ValueError):kv1(text)


if __name__=='__main__':unittest.main()
