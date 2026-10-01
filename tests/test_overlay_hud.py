import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OVERLAY = (ROOT / 'esp_overlay.cs').read_text(encoding='utf-8')
MENU = (ROOT / 'ks_menu.cs').read_text(encoding='utf-8')


class OverlayHudContractTests(unittest.TestCase):
    def test_enemy_roster_is_current_visible_feed_limited_to_five(self):
        start = OVERLAY.index('List<Hero> VisibleEnemies()')
        end = OVERLAY.index('static double Clamp', start)
        roster = OVERLAY[start:end]
        self.assertIn('smooth.Values', roster)
        self.assertIn('h.team!=cur.myTeam', roster)
        self.assertIn('.Take(5)', roster)
        self.assertIn('enemyBadgeX=.50', OVERLAY)
        self.assertIn('enemyBadgeY=1.0', OVERLAY)

    def test_static_enemy_cards_render_hp_and_ability_cooldowns(self):
        start = OVERLAY.index('void DrawEnemyBadge(DrawingContext dc,float ppd)')
        end = OVERLAY.index('public bool IsDraggableAt', start)
        panel = OVERLAY[start:end]
        for expected in ('hpFraction', 'enemy.hp', 'enemy.maxhp', 'enemy.abs',
                         'GetIcon(abilityName)', 'cooldown', 'enemyBadgeX', 'enemyBadgeY'):
            with self.subTest(expected=expected):
                self.assertIn(expected, panel)

    def test_panel_and_overhead_plates_are_independent_controls(self):
        start = OVERLAY.index('protected override void OnRender')
        end = OVERLAY.index('List<Hero> VisibleEnemies()', start)
        render = OVERLAY[start:end]
        self.assertIn('DrawPlate(dc,h,px,py,isLocal,isEnemy,ppd);', render)
        self.assertNotIn('enemyBadgeEnabled) continue', render)
        start = MENU.index('void VisualPage()')
        end = MENU.index('void VisualSwitch(', start)
        visual_page = MENU[start:end]
        self.assertIn('overlay.platesEnabled', visual_page)
        self.assertIn('overlay.visual.enemyBadgeEnabled', visual_page)
        self.assertIn('независимо', visual_page)

    def test_overhead_plates_are_clipped_to_dota_and_offscreen_targets_skipped(self):
        start = OVERLAY.index('protected override void OnRender')
        end = OVERLAY.index('List<Hero> VisibleEnemies()', start)
        render = OVERLAY[start:end]
        self.assertIn('new RectangleGeometry(dotaRect)', render)
        self.assertIn('px<dotaRect.Left', render)
        self.assertIn('py<dotaRect.Top', render)
        self.assertIn('px>=dotaRect.Right', render)
        self.assertIn('py>=dotaRect.Bottom', render)

    def test_hero_plate_position_uses_current_projection_without_easing_lag(self):
        start = OVERLAY.index('public void Update(State st, double dt)')
        end = OVERLAY.index('List<int> gone', start)
        update = OVERLAY[start:end]
        self.assertIn('s.sx=h.x;s.sy=h.y;', update)
        self.assertNotIn('s.sx +=', update)
        self.assertNotIn('s.sy +=', update)

    def test_no_hero_pin_fields_and_stale_feed_clears_roster(self):
        for forbidden in ('pinEnemyBadge', 'pinOffsetX', 'pinOffsetY', 'badgeTarget'):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, OVERLAY)
                self.assertNotIn(forbidden, MENU)
        self.assertIn('content.Update(displayUsable&&fresh ? st : new State(), frameDt);', OVERLAY)
        self.assertIn('enemyBadgeX=.50;enemyBadgeY=1.0', OVERLAY)


if __name__ == '__main__':
    unittest.main()
