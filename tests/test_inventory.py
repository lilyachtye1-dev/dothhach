import struct
import unittest
from types import SimpleNamespace
from ks_inventory import Inventory, read_inventory

class Memory:
    def __init__(self, handles=None, layout=(0, 8)):
        self.base, self.data = 0x10000, 0x20000
        self.mem = {}
        self.entities = {}
        if handles is None:
            self.put(self.base, bytes(32))
        else:
            h = bytearray(32)
            struct.pack_into('<i', h, layout[0], len(handles))
            struct.pack_into('<Q', h, layout[1], self.data)
            self.put(self.base, h)
            self.put(self.data, struct.pack('<' + 'I' * len(handles), *handles))

    def put(self, address, data):
        for i, value in enumerate(data):
            self.mem[address + i] = value

    def rb(self, address, count):
        try:
            return bytes(self.mem[address + i] for i in range(count))
        except KeyError:
            return None

    def get_entity(self, idx):
        return self.entities.get(idx)


class InventoryTests(unittest.TestCase):
    def test_empty_allocated_vector(self):
        d = Memory([0xFFFFFFFF] * 27)
        inv = read_inventory(d, d.base)
        self.assertTrue(inv.known)
        self.assertEqual(inv.entries, ())

    def test_zero_length_vector_with_metadata(self):
        d = Memory()
        d.put(d.base + 24, struct.pack('<Q', 0x50000))
        self.assertTrue(read_inventory(d, d.base).known)

    def test_other_layout_preserves_slot(self):
        handles = [0xFFFFFFFF] * 21
        handles[3] = 0x18005
        d = Memory(handles, (8, 0))
        d.entities[5] = (0x50000, 'item_dagon_3')
        inv = read_inventory(d, d.base)
        self.assertTrue(inv.known)
        self.assertEqual(inv.entries[0], (3, 0x18005, 0x50000, 'item_dagon_3'))

    def test_stash_unresolved_is_irrelevant(self):
        handles = [0xFFFFFFFF] * 27
        handles[9] = 0x18005
        d = Memory(handles)
        self.assertTrue(read_inventory(d, d.base).known)

    def test_active_slot_unresolved_is_unknown_not_empty(self):
        handles = [0xFFFFFFFF] * 27
        handles[0] = 0x18005
        d = Memory(handles)
        inv = read_inventory(d, d.base)
        self.assertFalse(inv.known)
        self.assertIn('slot 0', inv.reason)

    def test_neutral_slot_is_checked(self):
        handles = [0xFFFFFFFF] * 27
        handles[16] = 0x18005
        d = Memory(handles)
        self.assertFalse(read_inventory(d, d.base).known)

    def test_unreadable_header_not_empty(self):
        d = Memory()
        del d.mem[d.base]
        self.assertFalse(read_inventory(d, d.base).known)

    def test_unreadable_vector_not_empty(self):
        d = Memory([0xFFFFFFFF] * 21)
        del d.mem[d.data]
        self.assertFalse(read_inventory(d, d.base).known)

    def test_ambiguous_layouts_rejected(self):
        d = Memory([0xFFFFFFFF] * 21)
        second = 0x30000
        d.put(d.base + 16, struct.pack('<Q', second))
        handles = [0xFFFFFFFF] * 21
        handles[0] = 0x18005
        d.put(second, struct.pack('<' + 'I' * 21, *handles))
        d.entities[5] = (0x50000, 'item_dagon')
        self.assertIn('ambiguous', read_inventory(d, d.base).reason)

    def test_reader_exception_becomes_unknown(self):
        d = SimpleNamespace(rb=lambda a, n: (_ for _ in ()).throw(RuntimeError('denied')))
        self.assertFalse(read_inventory(d, 0x10000).known)


