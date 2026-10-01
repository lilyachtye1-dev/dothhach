import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile

import patch_bundle as b
import patch_update as u
from ks_core import Settings


class PatchBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in u.FILES:
            shutil.copyfile(u.ROOT / name, self.root / name)
        self.before = {name: (self.root / name).read_bytes() for name in u.FILES}
        self.schema = self.before['client_dll.json']
        old = u.template(self.root)
        c = old['client.dll']; e = old['engine2.dll']; o = old['object_offsets']
        offsets = {
            'client.dll': {
                'dwEntityList': c['DW_GAME_ENTITY_SYSTEM'],
                'dwGameEntitySystem': c['DW_GAME_ENTITY_SYSTEM'],
                'dwViewMatrix': c['DW_VIEW_MATRIX'], 'dwViewRender': c['DW_VIEW_RENDER'],
                'dwGameRules': c['DW_GAME_RULES'], 'dwGlobalVars': c['DW_GLOBAL_VARS'],
                'dwGameEntitySystem_highestEntityIndex': o['DW_HIGHEST_INDEX'],
            },
            'engine2.dll': {
                'dwBuildNumber': e['DW_BUILD_NUMBER'],
                'dwNetworkGameClient': e['DW_NETWORK_GAME_CLIENT'],
                'dwNetworkGameClient_localPlayer': o['OFF_NGC_LOCAL_PLAYER'],
            },
        }
        self.offsets = json.dumps(offsets).encode()
        self.buttons = json.dumps({'client.dll': {'attack': 0x1000, 'forward': 0x2000}}).encode()
        self.info = json.dumps({'build_number': 48}).encode()
        self.files = {'client_dll.json': self.schema, 'offsets.json': self.offsets,
                      'buttons.json': self.buttons, 'info.json': self.info}

    def test_maps_standard_offsets_using_engine_build_not_info_number(self):
        manifest = b.offsets_manifest(self.offsets, self.schema)
        self.assertEqual(manifest['build'], 6356260)
        self.assertNotEqual(manifest['build'], 48)
        self.assertEqual(manifest['client.dll']['DW_GAME_ENTITY_SYSTEM'], '0x653db10')

    def test_in_app_import_rejects_user_supplied_profiles_directly_or_in_zip(self):
        profile_path = self.root / 'ks_profiles.json'
        offsets_path = self.root / 'offsets.json'
        profile_path.write_bytes(self.before['ks_profiles.json'])
        offsets_path.write_bytes(self.offsets)
        with self.assertRaisesRegex(ValueError, 'каталог способностей ведёт разработчик'):
            b.collect_files([self.root / 'client_dll.json', offsets_path, profile_path], allow_profiles=False)
        archive_path = self.root / 'profiles.zip'
        with zipfile.ZipFile(archive_path, 'w') as archive:
            archive.writestr('dump/client_dll.json', self.schema)
            archive.writestr('dump/offsets.json', self.offsets)
            archive.writestr('dump/ks_profiles.json', profile_path.read_bytes())
        with self.assertRaisesRegex(ValueError, 'каталог способностей ведёт разработчик'):
            b.collect_files([archive_path], allow_profiles=False)

    def test_recognizes_nested_zip_without_extracting_and_stages_one_selection(self):
        archive_path = self.root / 'patch.zip'
        with zipfile.ZipFile(archive_path, 'w') as archive:
            for name, data in self.files.items():
                archive.writestr('dump/' + name, data)
            archive.writestr('dump/unrelated.json', b'{"anything":true}')
            archive.writestr('../outside.txt', b'not extracted')
        files, ignored = b.collect_files([archive_path])
        self.assertEqual(set(files), set(self.files))
        self.assertIn('dump/unrelated.json', ignored)
        result = b.stage_files(self.root, files, ignored)
        self.assertEqual({name: (self.root / name).read_bytes() for name in u.FILES}, self.before)
        self.assertTrue((result['stage'] / 'buttons.json').is_file())
        self.assertEqual(result['optional']['info_build_number'], 48)
        self.assertTrue((self.root / b.STAGE_POINTER).is_file())

    def test_clean_start_applies_staged_bundle_in_preview_and_creates_backup(self):
        result = b.stage_files(self.root, self.files)
        self.assertEqual(result['changes'], [])
        self.assertEqual(b.apply_staged(self.root), 0)
        self.assertFalse((self.root / b.STAGE_POINTER).exists())
        self.assertNotEqual((self.root / 'ks_profiles.json').read_bytes(), self.before['ks_profiles.json'])
        settings = Settings(self.root / 'ks_profiles.json')
        self.assertFalse(settings.auto_cast)
        self.assertIsNone(settings.live_modifier_layout)
        applied = json.loads((result['stage'] / 'applied.json').read_text())
        backup = Path(applied['backup'])
        self.assertTrue((backup / 'receipt.json').is_file())
        self.assertTrue((result['stage'] / 'buttons.json').is_file())

    def test_optional_user_profiles_are_validated_and_installed_safely(self):
        profile = json.loads(self.before['ks_profiles.json'])
        profile['settings']['auto_cast'] = True
        self.files['ks_profiles.json'] = json.dumps(profile).encode()
        b.stage_files(self.root, self.files)
        b.apply_staged(self.root)
        settings = Settings(self.root / 'ks_profiles.json')
        self.assertFalse(settings.auto_cast)
        self.assertIsNone(settings.live_modifier_layout)

    def test_bad_offsets_or_duplicate_filenames_fail_closed(self):
        offsets = json.loads(self.offsets)
        offsets['client.dll']['dwEntityList'] = '0x1234'
        with self.assertRaisesRegex(ValueError, 'не совпадают'):
            b.offsets_manifest(json.dumps(offsets).encode(), self.schema)
        with tempfile.TemporaryDirectory() as directory:
            # A ZIP can contain duplicate basenames in different subfolders; the
            # importer must reject that ambiguity without extracting either file.
            archive_path = Path(directory) / 'dup.zip'
            with zipfile.ZipFile(archive_path, 'w') as archive:
                archive.writestr('one/client_dll.json', self.schema)
                archive.writestr('two/client_dll.json', self.schema)
                archive.writestr('offsets.json', self.offsets)
            with self.assertRaisesRegex(ValueError, 'несколько файлов'):
                b.collect_files([archive_path])

    def test_staged_file_hash_mismatch_prevents_apply(self):
        result = b.stage_files(self.root, self.files)
        (result['stage'] / 'client_dll.json').write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, 'SHA256 не совпал'):
            b.apply_staged(self.root)
        self.assertTrue((self.root / b.STAGE_POINTER).exists())
        self.assertEqual({name: (self.root / name).read_bytes() for name in u.FILES}, self.before)

    def test_invalid_profiles_are_rejected_before_staging(self):
        profile = json.loads(self.before['ks_profiles.json'])
        profile['settings']['stale_seconds'] = -1
        self.files['ks_profiles.json'] = json.dumps(profile).encode()
        with self.assertRaises(ValueError):
            b.stage_files(self.root, self.files)
        self.assertFalse((self.root / b.STAGE_POINTER).exists())
        self.assertEqual({name: (self.root / name).read_bytes() for name in u.FILES}, self.before)

    def test_existing_pending_import_is_not_overwritten(self):
        b.stage_files(self.root, self.files)
        with self.assertRaisesRegex(ValueError, 'Уже есть ожидающий пакет'):
            b.stage_files(self.root, self.files)
        self.assertEqual(b.discard_staged(self.root), 0)
        self.assertFalse((self.root / b.STAGE_POINTER).exists())


if __name__ == '__main__':
    unittest.main()
