"""Bounded, read-only cast-field journal. Observations NEVER authorize input.
The reader retains transitions so sender/console throttling cannot hide a short
observed phase. Casts occurring entirely between reader samples remain unknown.
"""
from collections import deque
import json


class CastJournal:
    LIMIT = 64

    def __init__(self):
        self.events = deque(maxlen=self.LIMIT)
        self.states = {}
        self.sequence = 0

    def add(self, kind, now, **fields):
        self.sequence += 1
        text = json.dumps(dict(kind=kind, at=round(now, 6), **fields),
                          ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        self.events.append((self.sequence, text))

    def observe(self, key, now, signature, **fields):
        old = self.states.get(key)
        self.states[key] = (signature, now)
        if old is None or old[0] != signature or not 0 <= now-old[1] <= .15:
            self.add('cast_fields', now, **fields)

    def retain(self, keys):
        self.states = {key: value for key, value in self.states.items() if key in keys}

    def snapshot(self):
        return tuple(self.events)
