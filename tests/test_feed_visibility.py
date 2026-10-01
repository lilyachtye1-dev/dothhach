import unittest

from esp_visibility import overlay_hero_visible


class FeedVisibilityTests(unittest.TestCase):
    def test_fogged_or_hidden_opponents_never_reach_overlay(self):
        self.assertFalse(overlay_hero_visible({"screen": (400, 300), "fog": True}))
        self.assertFalse(overlay_hero_visible({"screen": (400, 300), "hide": True}))
        self.assertFalse(overlay_hero_visible({"screen": None}))

    def test_visible_opponents_and_offscreen_local_hero_are_allowed(self):
        self.assertTrue(overlay_hero_visible({"screen": (400, 300)}))
        self.assertTrue(overlay_hero_visible({"screen": None, "is_local": True}))

    def test_illusions_are_never_added_to_overlay_roster(self):
        self.assertFalse(overlay_hero_visible({"screen": (400, 300), "illusion": True}))


if __name__ == "__main__":
    unittest.main()
