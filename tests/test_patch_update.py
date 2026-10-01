import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import patch_update as u
from ks_core import Settings


class PatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in u.FILES:
            shutil.copyfile(u.ROOT / name, self.root / name)
        self.before = {n: (self.root / n).read_bytes() for n in u.FILES}
        self.schema = self.root / 'new-schema.json'
        self.schema.write_bytes(self.before['client_dll.json'])
        self.manifest = self.root / 'globals.json'
        self.manifest.write_text(json.dumps(u.template(self.root)))

    def make_plan(self):
        return u.plan(self.root, self.schema, self.manifest)[0]

    def assert_original(self):
        self.assertEqual(self.before, {n: (self.root / n).read_bytes() for n in u.FILES})

    def test_invalid_gameplay_config_rejected_before_commit(self):
        path = self.root / 'ks_profiles.json'
        cfg = json.loads(path.read_bytes())
        cfg['settings']['stale_seconds'] = -1
        path.write_text(json.dumps(cfg))
        with self.assertRaises(ValueError): self.make_plan()
        self.assertFalse((self.root / u.LOCK).exists())

    def test_current_schema_has_no_offset_diff(self):
        _, changes = u.plan(self.root, self.schema, self.manifest)
        self.assertEqual(changes, [])
        self.assertEqual(len(u.FIELDS), 71)
        self.assert_original()

    def test_apply_preview_and_rollback_byte_exact(self):
        backup = u.transaction(self.root, self.make_plan())
        s = Settings(self.root / 'ks_profiles.json')
        self.assertFalse(s.auto_cast)
        self.assertIsNone(s.live_modifier_layout)
        old = json.loads(self.before['ks_profiles.json'])
        new = json.loads((self.root / 'ks_profiles.json').read_bytes())
        self.assertEqual(old['abilities'], new['abilities'])
        self.assertFalse((self.root / u.LOCK).exists())
        u.rollback(self.root, backup)
        self.assert_original()

    def test_changed_public_field_only(self):
        schema = json.loads(self.schema.read_bytes())
        schema['client.dll']['classes']['C_BaseEntity']['fields']['m_iHealth'] += 4
        self.schema.write_text(json.dumps(schema))
        data, changes = u.plan(self.root, self.schema)
        self.assertEqual(len(changes), 1)
        self.assertIn('M_I_HEALTH', changes[0])
        self.assertEqual(u.assignments(data['dota_esp.py'].decode(), {'M_I_HEALTH'})['M_I_HEALTH'].value, 0x350)
        self.assert_original()

    def test_manifest_bound_to_exact_schema(self):
        self.schema.write_bytes(self.schema.read_bytes() + b'\n')
        with self.assertRaises(ValueError): self.make_plan()
        self.assert_original()

    def test_missing_field_rejected(self):
        d = json.loads(self.schema.read_bytes())
        del d['client.dll']['classes']['C_BaseEntity']['fields']['m_iHealth']
        with self.assertRaises(KeyError): u.schema_values(json.dumps(d).encode())

    def test_non_integer_offsets_rejected(self):
        for v in (True, -1, 0, 1.25, 0x100001, 'not-int'):
            with self.subTest(v=v), self.assertRaises(ValueError): u.number(v, 0x100000)

    def test_duplicate_json_keys_and_nonfinite(self):
        for s in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}'):
            with self.assertRaises(ValueError): u.load(s)

    def test_constant_rewrite_preserves_comments(self):
        s = '"""Русский текст"""\nVALUE = 0x123  # exact comment\n'
        new, _ = u.replace_constants(s, {'VALUE': 77})
        self.assertEqual(new, s.replace('0x123', '0x4d'))
        for s in ('VALUE = 1\nVALUE = 2\n', 'VALUE = dangerous()\n', 'X = 1\n'):
            with self.assertRaises(ValueError): u.replace_constants(s, {'VALUE': 77})

    def test_unknown_global_and_bad_build_rejected(self):
        m = json.loads(self.manifest.read_bytes())
        m['client.dll']['unknown'] = 123
        with self.assertRaises(ValueError): u.manifest_values(json.dumps(m).encode(), self.schema.read_bytes())
        m = json.loads(self.manifest.read_bytes()); m['build'] = True
        with self.assertRaises(ValueError): u.manifest_values(json.dumps(m).encode(), self.schema.read_bytes())

    def test_lock_blocks_second_writer(self):
        (self.root / u.LOCK).write_text('already pending')
        with self.assertRaises(FileExistsError): u.transaction(self.root, self.make_plan())
        self.assert_original()

    def test_mid_commit_failure_restores_original(self):
        atomic = u.atomic
        failed = False
        def flaky(path, data):
            nonlocal failed
            if path == self.root / 'client_dll.json' and not failed:
                failed = True
                raise OSError('simulated disk error')
            atomic(path, data)
        with patch.object(u, 'atomic', flaky):
            with self.assertRaises(OSError): u.transaction(self.root, self.make_plan())
        self.assert_original()
        self.assertFalse((self.root / u.LOCK).exists())

    def test_backup_failure_leaves_installation_unlocked(self):
        with patch.object(u, 'atomic', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): u.transaction(self.root, self.make_plan())
        self.assert_original()
        self.assertFalse((self.root / u.LOCK).exists())

    def test_recovery_from_partial_commit(self):
        backup = u.transaction(self.root, self.make_plan())
        (self.root / 'dota_esp.py').write_bytes(self.before['dota_esp.py'])
        (self.root / u.LOCK).write_text(str(backup.relative_to(self.root)))
        u.rollback(self.root, backup)
        self.assert_original()

    def test_rollback_refuses_profile_edits(self):
        backup = u.transaction(self.root, self.make_plan())
        p = self.root / 'ks_profiles.json'; p.write_bytes(p.read_bytes() + b'\n')
        with self.assertRaises(ValueError): u.rollback(self.root, backup)

    def test_rollback_refuses_corrupt_backup(self):
        backup = u.transaction(self.root, self.make_plan())
        (backup / 'dota_esp.py').write_text('corrupt')
        with self.assertRaises(ValueError): u.rollback(self.root, backup)

    def test_rollback_confined_to_installation(self):
        with self.assertRaises(ValueError): u.rollback(self.root, self.root / 'arbitrary')

    def test_cli_dry_run_and_missing_globals_never_write(self):
        with patch.object(u, 'ROOT', self.root):
            self.assertEqual(u.main(['--schema', str(self.schema)]), 0)
            self.assertEqual(u.main(['--schema', str(self.schema), '--apply']), 1)
        self.assert_original()

    def test_cli_template_does_not_overwrite(self):
        with patch.object(u, 'ROOT', self.root):
            self.assertEqual(u.main(['--template', str(self.manifest)]), 1)
        self.assert_original()


if __name__ == '__main__':
    unittest.main()
