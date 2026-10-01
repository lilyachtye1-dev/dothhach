"""Inventory decoder for the supplied schema; no offset scanning/mixing.
Unlike Dot.inventory_handles, accepts a readable vector with all empty handles.
Unknown != empty. Only combat-relevant slots need an entity name.
"""
from dataclasses import dataclass, replace
import struct

ACTIVE_SLOTS = (0, 1, 2, 3, 4, 5, 16)


@dataclass(frozen=True)
class Inventory:
    known: bool
    entries: tuple = ()  # (slot, handle, entity address, designer name)
    layout: str = ''
    reason: str = ''
    header_hex: str = ''


def pointer(value):
    return isinstance(value, int) and 0x10000 <= value < (1 << 48) and value % 4 == 0


def read_inventory(dot, base, field_size=104):
    try:
        result = _read_inventory(dot, base, field_size)
        if not result.known:
            raw = dot.rb(base, 32)
            result = replace(result, header_hex=raw.hex(' ') if raw else 'UNREADABLE')
        return result
    except Exception as exc:
        return Inventory(False, reason='inventory read failed: ' + str(exc))


def _read_inventory(dot, base, field_size):
    header = dot.rb(base, 32)
    if header is None or len(header) != 32:
        return Inventory(False, reason='inventory header unreadable')
    candidates, errors = [], []
    seen = set()
    # Same layouts considered by the original reader, but each is validated.
    for count_off, pointer_off in ((0, 8), (8, 0), (16, 0), (0, 16), (0, 24)):
        count = struct.unpack_from('<i', header, count_off)[0]
        address = struct.unpack_from('<Q', header, pointer_off)[0]
        if not 0 <= count <= 64 or (count and not pointer(address)):
            continue
        if count == 0:
            # Do NOT let speculative zero counts mask a damaged nonempty vector.
            # Canonical zero count/null data is valid even with trailing metadata.
            if (count_off, pointer_off) != (0, 8) or address != 0:
                continue
            candidates.append(Inventory(True, layout='count0/data8:empty'))
            continue
        if (count, address) in seen:
            continue
        seen.add((count, address))
        raw = dot.rb(address, count * 4)
        if raw is None or len(raw) != count * 4:
            errors.append('vector data unreadable')
            continue
        handles = struct.unpack('<' + 'I' * count, raw)
        try:
            entries = resolve_active_entries(dot, handles)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        # Reject collection torn by a slot/container change.
        if dot.rb(address, count * 4) != raw:
            errors.append('inventory changed during read')
            continue
        candidates.append(Inventory(True, entries, 'count%d/data%d' % (count_off, pointer_off)))
    # FIX3: observed header 19 00 00 00 <handle0> <handle1> ... .
    # The schema field occupies 104 bytes: count32 + 25 inline handles32.
    # Do NOT interpret count as slot zero; slot zero starts at base+4.
    if not isinstance(field_size, int) or field_size < 8 or field_size > 260 or (field_size - 4) % 4:
        return Inventory(False, reason='invalid schema inventory field size')
    count = struct.unpack_from('<i', header, 0)[0]
    capacity = (field_size - 4) // 4
    if 1 <= count <= capacity:
        size = 4 + count * 4
        block = dot.rb(base, size)
        if block is None or len(block) != size:
            errors.append('inline inventory incomplete (%d bytes required)' % size)
        elif block[:min(size, 32)] != header[:min(size, 32)]:
            errors.append('inline inventory header changed during read')
        else:
            handles = struct.unpack_from('<' + 'I' * count, block, 4)
            try:
                entries = resolve_active_entries(dot, handles)
                if dot.rb(base, size) != block:
                    errors.append('inline inventory changed during read')
                else:
                    candidates.append(Inventory(True, entries, 'inline/count0/data4/n%d' % count))
            except ValueError as exc:
                errors.append('inline ' + str(exc))
    if dot.rb(base, 32) != header:
        return Inventory(False, reason='inventory header changed during read')
    if not candidates:
        return Inventory(False, reason=errors[0] if errors else 'inventory layout unknown')
    signatures = {c.entries for c in candidates}
    if len(signatures) != 1:
        return Inventory(False, reason='ambiguous inventory layouts')
    return candidates[0]


def resolve_active_entries(dot, handles):
    entries = []
    for slot in ACTIVE_SLOTS:
        if slot >= len(handles):
            continue
        handle = handles[slot]
        if handle in (0, 0xFFFFFFFF):
            continue
        idx = handle & 0x7FFF
        entity = dot.get_entity(idx) if 0 < idx < 32768 else None
        if not entity or not entity[1] or not entity[1].startswith('item_'):
            raise ValueError('slot %d handle=0x%08X unresolved' % (slot, handle))
        entries.append((slot, handle, entity[0], entity[1]))
    return tuple(entries)
