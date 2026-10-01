"""Optional Mono smoke of actual C# DTO + pure HUD model, with a real Python DTO.
No WPF execution or claim of a Windows/Dota test. Skips if Mono is not installed.
"""
from dataclasses import replace
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from helpers import ROOT
import test_dodger as existing
from ks_dashboard import dashboard


class NativeCompactSmoke(unittest.TestCase):
    @unittest.skipUnless(shutil.which('mcs') and shutil.which('mono'),'optional Mono toolchain not installed')
    def test_actual_native_model_and_wire_dto(self):
        f=existing.DodgeServiceTests();f.setUp();self.addCleanup(f.tearDown)
        f.me=replace(f.me,name='npc_dota_hero_antimage');f.tick()
        f.d.enabled=True;f.d.status='ON · Ожидаем угрозу'
        f.h[0]['_dodge']=replace(f.h[0]['_dodge'],threats=(),compact_reason='Assassinate: нет валидной цели')
        with patch('dodge_service.time.perf_counter',return_value=10.1):doc=dashboard(f.ks,f.h,0,now=10.1)
        menu=(ROOT/'ks_menu.cs').read_text(encoding='utf-8')
        dtos=menu[menu.index('    public class SourceData'):menu.index('    static class MenuTheme')]
        native=(ROOT/'ktweaksl.cs').read_text(encoding='utf-8')
        helper=native[native.index('    internal sealed class CompactHudView'):native.index('    partial class OverlayContent')]
        smoke=(ROOT/'tests/compact_hud_smoke.cs').read_text(encoding='utf-8')
        code='using System;using System.Globalization;using System.Collections.Generic;namespace EspOverlay {\n'+dtos+helper+smoke+'\n}'
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp);cs=tmp/'smoke.cs';exe=tmp/'smoke.exe';payload=tmp/'dashboard.json'
            cs.write_text(code,encoding='utf-8');payload.write_text(json.dumps(doc,ensure_ascii=False,allow_nan=False),encoding='utf-8')
            built=subprocess.run(['mcs','-warnaserror','-langversion:5','-r:System.Web.Extensions','-out:'+str(exe),str(cs)],capture_output=True,text=True,timeout=30)
            self.assertEqual(built.returncode,0,built.stdout+built.stderr)
            run=subprocess.run(['mono',str(exe),str(payload)],capture_output=True,text=True,timeout=30)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)
            self.assertIn('PASS: 27 actual native HUD/DTO assertions',run.stdout)
