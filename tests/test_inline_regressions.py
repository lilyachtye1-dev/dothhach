"""FIX3 regressions. Only the first 32 bytes are from the submitted log.
All extra bytes/entities/game values in these tests are SYNTHETIC, not a dump.
"""
from dataclasses import replace
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

from ks_core import Settings, choose
from ks_inventory import read_inventory
from ks_reader import CombatReader
from ks_input import WinInput
from test_inventory import Memory
from helpers import fixture, fake_module

ROOT = Path(__file__).resolve().parents[1]
BOUNTY_HEADER = bytes.fromhex('19 00 00 00 ' + 'ff ff ff ff ' * 7)
LION_HEADER = bytes.fromhex('19 00 00 00 1a 01 5c 00 19 81 0f 01 ' + 'ff ff ff ff ' * 5)


def inline_memory(prefix=BOUNTY_HEADER, complete=True):
    memory = Memory()
    memory.put(memory.base, prefix)
    if complete:
        # Synthetic remaining 18 empty slots. Their actual bytes are not in the log.
        memory.put(memory.base + len(prefix), b'\xff' * (104 - len(prefix)))
    return memory


class Fix3InventoryTests(unittest.TestCase):
    def test_full_empty_inline_inventory(self):
        d = inline_memory()
        inv = read_inventory(d, d.base)
        self.assertTrue(inv.known)
        self.assertEqual(inv.layout, 'inline/count0/data4/n25')
        self.assertEqual(inv.entries, ())

    def test_logged_prefix_alone_cannot_prove_empty_inventory(self):
        d = inline_memory(complete=False)
        inv = read_inventory(d, d.base)
        self.assertFalse(inv.known)
        self.assertIn('incomplete', inv.reason)

    def test_lion_inline_slots_not_shifted(self):
        d = inline_memory(LION_HEADER)
        # Synthetic entity names; the real names of these two items are not logged.
        d.entities[282] = (0x50000, 'item_test_one')
        d.entities[281] = (0x60000, 'item_test_two')
        looked_up = []
        resolve = d.get_entity
        def lookup(idx):
            looked_up.append(idx)
            return resolve(idx)
        d.get_entity = lookup
        inv = read_inventory(d, d.base)
        self.assertTrue(inv.known)
        self.assertEqual(inv.entries, ((0, 0x005C011A, 0x50000, 'item_test_one'),
                                      (1, 0x010F8119, 0x60000, 'item_test_two')))
        self.assertNotIn(25, looked_up)  # the count must NEVER be a slot/handle

    def test_nonempty_slot_after_logged_prefix_is_checked(self):
        d = inline_memory()
        d.put(d.base + 4 + 16 * 4, struct.pack('<I', 0x18005))
        inv = read_inventory(d, d.base)
        self.assertFalse(inv.known)
        self.assertIn('slot 16', inv.reason)

    def test_unknown_item_not_silently_empty(self):
        d = inline_memory(LION_HEADER)
        inv = read_inventory(d, d.base)
        self.assertFalse(inv.known)
        self.assertIn('slot 0', inv.reason)

    def test_stash_unknown_does_not_invalidate_combat_slots(self):
        d = inline_memory()
        d.put(d.base + 4 + 9 * 4, struct.pack('<I', 0x18005))
        self.assertTrue(read_inventory(d, d.base).known)

    def test_changing_unlogged_tail_is_rejected(self):
        d = inline_memory()
        original = d.rb
        full_reads = 0
        def read(addr, size):
            nonlocal full_reads
            data = original(addr, size)
            if addr == d.base and size == 104:
                full_reads += 1
                if full_reads == 2:
                    return data[:-4] + bytes(4)
            return data
        d.rb = read
        inv = read_inventory(d, d.base)
        self.assertFalse(inv.known)
        self.assertIn('changed', inv.reason)

    def test_count_beyond_schema_capacity_is_rejected(self):
        d = inline_memory()
        d.put(d.base, struct.pack('<i', 26))
        inv = read_inventory(d, d.base, field_size=104)
        self.assertFalse(inv.known)

    def test_invalid_field_extent_rejected(self):
        d = inline_memory()
        self.assertFalse(read_inventory(d, d.base, field_size=105).known)

    def test_schema_extent_is_104_bytes(self):
        r = CombatReader(fake_module(), ROOT / 'client_dll.json')
        self.assertEqual(r.inventory_bytes, 104)

    def test_empty_inline_target_passes_inventory_gate(self):
        d = inline_memory()
        inv = read_inventory(d, d.base)
        s = Settings(ROOT / 'ks_profiles.json')
        s.auto_cast = True
        me, enemy = fixture()
        a = replace(me.actions[0], level=1, cooldown=0, mana=200)
        me = replace(me, mana=675, actions=(a,))
        enemy = replace(enemy, name='npc_dota_hero_bounty_hunter', hp=151, mr=.27,
                        inventory_known=inv.known, items=tuple(e[3] for e in inv.entries))
        picked = choose((me, enemy), s, 10.1, (1920, 1080))
        self.assertIsNotNone(picked)
        self.assertEqual(picked.key, 82)
        self.assertAlmostEqual(picked.damage, 438)

    def test_exact_interruption_name(self):
        backend = WinInput.__new__(WinInput)
        backend.down = lambda code: code == 0x02
        self.assertEqual(backend.interruption_reason(), 'ПКМ')
        self.assertTrue(backend.interrupted())


if __name__ == '__main__':
    unittest.main()
