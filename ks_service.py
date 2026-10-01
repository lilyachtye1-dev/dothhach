"""KS5 service: UI-only arming, death pause, priority input and cooldown feedback."""
from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler
import time
import json
import secrets
from ks_core import Settings, Toggle, CastLedger, choose, fresh
from ks_reader import CombatReader
from ks_input import WinInput


class KillSteal:
    def __init__(self, root, reader_module, backend=None, preview=False):
        root = Path(root)
        self.ui_session = secrets.token_hex(16)
        self.ui_revision = 0
        self.ui_disabled = set()
        self.ui_menu_open = False
        self.ui_arm_pending = False
        self.ui_arm_requested_at = 0.0
        self.ui_arm_timeout = 20.0
        self.ui_control_path = root / 'ui_session_controls.json'
        self.ui_poll_at = 0
        self.ui_mtime = None
        self.preview_requested = bool(preview)
        self.dodger = None
        self.last_hit = None
        self.dodge_reader = None
        self.status, self.detail = 'KS5: запуск', ''
        self.target_idx, self.error = -1, ''
        self.settings = self.reader = self.backend = None
        self.toggle, self.ledger = Toggle(), CastLedger()
        self.stale_since = None
        self.last_hero = None
        self.off_reason = 'После запуска всегда OFF'
        self.last_log = ''
        self.last_log_at = self.last_data_log = 0
        self.last_feedback = ''
        self.log = logging.getLogger('killsteal4.' + str(id(self)))
        self.log.setLevel(logging.INFO)
        self.log.propagate = False
        try:
            handler = RotatingFileHandler(root / 'killsteal.log', maxBytes=1024000, backupCount=1, encoding='utf-8')
            handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
            self.log.addHandler(handler)
            self.settings = Settings(root / 'ks_profiles.json')
            for warning in self.settings.warnings:
                self.log.warning(warning)
                print('[KS5 WARNING] ' + warning, flush=True)
            if preview:
                self.settings.auto_cast = False
            self.reader = CombatReader(reader_module, root / 'client_dll.json', self.settings)
            self.backend = backend if backend is not None else WinInput()
            from dodge_service import Dodger
            from dodge_reader import ThreatReader
            self.dodger = Dodger(self)
            from last_hit_service import LastHit
            self.last_hit = LastHit(self, root)
            self.dodge_reader = ThreatReader(self.reader, root / 'client_dll.json')
            observer=getattr(self.backend,'modifiers',None)
            self.log.info('DEFENSE physical modifier observer ready=%s (no text logging)',bool(observer and observer.snapshot() is not None))
            self.log.info('DODGER PROVIDER alpha7: phase reposition revalidation / bounded modifier retry / verified manual activity; Chaos Bolt diagnostic only')
            self.log.info('START KS5 toggle=OFF auto_cast=%s profiles=%d', self.settings.auto_cast, len(self.settings.profiles))
        except Exception as exc:
            self.error = str(exc)
            self.status = 'KS5 DISABLED: ' + self.error
            print(self.status, flush=True)

    def capture(self, dot, heroes):
        if self.error:
            return heroes
        enriched = self.reader.capture(dot, heroes)
        frame = self.dodge_reader.capture(dot, enriched) if self.dodge_reader else None
        for h in enriched:
            if h.get('is_local'):
                h['_dodge'] = frame
            h['_ks_viewport'] = tuple(dot.screen)
        if self.last_hit:self.last_hit.set_hint(enriched)
        return enriched

    def tick(self, heroes, reader_age):
        self.target_idx = -1
        if self.error:
            cancel=getattr(self.backend,'finish_last_hit_mouse_priority',None)
            if cancel:cancel('service error',resume=False)
            return
        try:
            self.poll_ui_controls()
            self.menu_foreground=bool(getattr(self.backend,'menu_context',lambda h,p:False)(
                getattr(self,'ui_menu_hwnd',0),getattr(self,'ui_menu_pid',0)))
            if self.backend.down(45) or self.menu_foreground:self.menu_transition_until=time.perf_counter()+.5
            mouse_watch=getattr(self.backend,'watch_last_hit_mouse_priority',None)
            if mouse_watch:
                if self.ui_menu_open or self.menu_foreground:self.backend.finish_last_hit_mouse_priority('menu',resume=False)
                else:mouse_watch(time.perf_counter())
            held = self.dodger.tick(heroes, reader_age) if self.dodger else False
            self._tick(heroes, reader_age, defense_hold=held)
            if self.last_hit:
                try:
                    self.last_hit.tick(heroes,reader_age,blocked=bool(held or self.ledger.pending or not self.ledger.ready(time.perf_counter())))
                except Exception as exc:
                    self.last_hit.enabled=False;self.last_hit.fault=True
                    self.last_hit.status='OFF · '+str(exc)[:160]
                    cancel=getattr(self.backend,'finish_last_hit_mouse_priority',None)
                    if cancel:cancel('Last Hit error',resume=False)
                    self.log.exception('LASTHIT failed closed')
        except Exception as exc:
            cancel=getattr(self.backend,'finish_last_hit_mouse_priority',None)
            if cancel:cancel('sender error',resume=False)
            self.toggle.enabled = False
            self.off_reason = 'Ошибка: ' + str(exc)
            self.status = 'KS5 OFF | ' + self.off_reason
            self.log.exception('tick failed, OFF')
        self._log_status()

    def _log_status(self, force=False):
        now = time.perf_counter()
        if self.status != self.last_log and (force or now - self.last_log_at >= .8):
            self.last_log, self.last_log_at = self.status, now
            self.log.info(self.status)
            print('[' + self.status + ']', flush=True)

    def _tick(self, heroes, reader_age, defense_hold=False):
        s, b = self.settings, self.backend
        now = time.perf_counter()
        # Killsteal is controlled only by the in-program button; no keyboard toggle.
        context = b.context()
        records = [h['_ks'] for h in heroes if h.get('_ks') is not None]
        local = [u for u in records if u.local]
        raw_local = [h for h in heroes if h.get('is_local')]
        local_h = raw_local[0] if len(raw_local) == 1 else {}
        me = local[0] if len(local) == 1 else None
        hard = ''
        stop_keys = 'End' if b.down(35) else ''
        menu_pause=(self.ui_menu_open or getattr(self,'menu_foreground',False) or b.down(45) or (context is None and now<getattr(self,'menu_transition_until',0.)))
        if self.ui_menu_open or getattr(self,'menu_foreground',False):stop_keys='End' if b.down(35) else ''
        if stop_keys:
            hard = 'Нажато ' + stop_keys
        elif context is None and not menu_pause:
            hard = 'Dota не в фокусе'
        elif len(raw_local) > 1 or len(local) > 1:
            hard = 'Локальный герой неоднозначен'
        elif me is not None and self.last_hero is not None and me.entity != self.last_hero:
            hard = 'Сменился локальный герой'
        if me is not None:
            self.last_hero = me.entity
        age_ok = 0 <= reader_age <= s.stale
        data_ok = bool(age_ok and me is not None and fresh(me, now, s.stale))
        geometry_ok = bool(context and tuple(local_h.get('_ks_viewport', ())) == tuple(context[3:5]))
        if not data_ok or (not geometry_ok and not menu_pause):
            if self.stale_since is None:
                self.stale_since = now
            if now - self.stale_since >= s.stale_off:
                hard = hard or 'Данные/геометрия недоступны более %.1f сек' % s.stale_off
        else:
            self.stale_since = None
        self.ledger.sync(me if data_ok else None, now)
        if self.ledger.message and self.ledger.message != self.last_feedback:
            self.last_feedback = self.ledger.message
            self.log.info('FEEDBACK %s', self.last_feedback)
        self.detail = ('Ваш герой: %s | мана %.0f | источников: %d' %
                       (me.name.removeprefix('npc_dota_hero_'), me.mana, len(me.actions))) if me else 'Локальный герой ещё не прочитан'
        if me:
            for profile in s.profiles.values():
                if profile.stack_scaling and profile.hero == me.name.removeprefix('npc_dota_hero_'):
                    rule = profile.stack_scaling
                    n = dict(me.stack_values).get(rule['modifier'], '?')
                    t = dict(me.talent_levels).get(rule['talent'])
                    self.detail += ' | Стаки: %s; талант +%g: %s' % (n, rule['talent_bonus'], {0:'нет',1:'изучен'}.get(t,'?'))
        if me and not me.inventory_known:
            self.detail += ' | свой инвентарь неизвестен: только способности'
        if local_h.get('_ks_error'):
            self.detail += ' | ' + local_h['_ks_error']
        if self.ledger.quarantined:
            self.detail += ' | Источников без подтверждения CD: %d' % len(self.ledger.quarantined)
        if now - self.last_data_log >= 3:
            self.last_data_log = now
            self.log.info('DATA %s', repr([{
                'idx': h.get('idx'), 'local': h.get('is_local'), 'name': h.get('designer'),
                'error': h.get('_ks_error'), 'inventory': repr(h.get('_ks_inventory')),
                'warnings': h.get('_ks_warnings'), 'busy': h.get('_ks_busy_reason'), 'busy_verified': h.get('_ks_busy_verified'), 'active_casts': h.get('_ks_active_casts'), 'combat_visible': h.get('ks_combat_visible'),
                'visibility_known': h.get('ks_visibility_known'), 'stacks': h.get('_ks_stacks'), 'talents': h.get('_ks_talents'), 'screen': h.get('screen'),
                'abilities': repr(h['_ks'].actions) if h.get('_ks') and h.get('is_local') else ''
            } for h in heroes]))
        # Ordinary gameplay input is not a blanket veto. Enter/Tab/Win only
        # suppress sending while physically held; they never latch KS OFF.
        manual = ', '.join(name for key,name in ((13,'Enter'),(9,'Tab'),(91,'Win-L'),(92,'Win-R')) if b.down(key))
        may_enable = data_ok and geometry_ok and not manual and not (me and (me.busy or not me.alive))
        was_on = self.toggle.enabled
        enabled = self.toggle.update(False, hard_off=bool(hard), may_enable=False)
        if self.ui_arm_pending and not menu_pause and now - self.ui_arm_requested_at > self.ui_arm_timeout:
            self.ui_arm_pending = False
            self.ui_arm_requested_at = 0.0
            self.off_reason = 'Запрос включения из меню истёк; открой программу и нажми кнопку ещё раз'
            self.log.warning('MENU arm request expired')
        if self.ui_arm_pending and not menu_pause and not hard and may_enable:
            self.toggle.enabled = True
            enabled = True
            self.ui_arm_pending = False
            self.ui_arm_requested_at = 0.0
            self.log.info('MENU arm accepted after safety checks')
        if hard:
            self.off_reason = hard
        elif was_on and not enabled:
            self.off_reason = 'Killsteal выключен'
        elif enabled and not was_on:
            self.off_reason = ''
            self.log.info('TOGGLE ON')
        if was_on and not enabled:
            self.log.info('TOGGLE OFF %s', self.off_reason)
        mode = 'AUTO' if s.auto_cast else 'PREVIEW'
        prefix = 'KS5 %s %s' % (mode, 'ON' if enabled else 'OFF')
        if not enabled:
            reason = hard or manual or self.off_reason or 'Нет свежих данных для включения'
            if self.ui_arm_pending and not hard:
                reason = 'Ожидание безопасного включения из меню'
                if self.ui_menu_open:
                    reason += ': закрой меню'
                elif not data_ok or not geometry_ok:
                    reason += ': ждём свежие данные/фокус игры'
                elif me is not None and not me.alive:
                    reason += ': герой мёртв'
                elif manual or (me and me.busy):
                    reason += ': пауза ручного ввода/занятости'
            self.status = prefix + ' | Нажми кнопку Killsteal в меню | ' + reason
            return
        if menu_pause:
            self.status=prefix+' | ПАУЗА: меню / ожидание фокуса; включённое состояние сохранено'
            return
        if defense_hold:
            self.status = prefix + ' | ПАУЗА: Dodger / защитное окно'
            return
        if not data_ok:
            self.status = prefix + ' | ПАУЗА: нет свежего кадра'
            return
        if not geometry_ok:
            self.status = prefix + ' | ПАУЗА: геометрия проекции не совпадает с окном'
            return
        if me is not None and not me.alive:
            self.status = prefix + ' | ПАУЗА: герой мёртв; включённое состояние сохранено до возрождения'
            return
        if manual:
            self.status = prefix + ' | ПАУЗА: удерживается ' + manual + '; включённое состояние сохранено'
            return
        if me.busy:
            self.status = prefix + ' | Герой занят: ' + local_h.get('_ks_busy_reason', '?')
            return
        if self.ledger.pending is not None:
            self.status = prefix + ' | Ожидается изменение CD: ' + self.ledger.pending.action.name
            return
        if not self.ledger.ready(now):
            self.status = prefix + ' | Ожидание попадания / интервал между кастами'
            return
        notes = []
        decision = choose(records, s, now, context[3:5], notes, self.ledger.quarantined, self.ui_disabled)
        if decision is None:
            self.status = prefix + ' | ' + ('; '.join(notes[:2]) or 'Нет добиваемой цели')
            return
        self.target_idx = decision.target.idx
        self.detail += ' | База/дальность: JSON; amp не учтён'
        if decision.profile.stack_scaling:
            r = decision.profile.stack_scaling
            n = dict(me.stack_values).get(r['modifier'])
            t = dict(me.talent_levels).get(r['talent'])
            each = r['per_stack'] + (r['talent_bonus'] if t == 1 else 0)
            self.detail += ' | Стаки: %s × %g; талант: %s' % (n, each, {0:'нет',1:'изучен'}.get(t,'?'))
            if n != '?' and t is None:
                self.detail += ' | Урон — нижняя оценка без бонуса таланта'
        label = '%s -> %s: ~%.0f / нужно %.0f' % (
            decision.profile.name, decision.target.name.removeprefix('npc_dota_hero_'), decision.damage, decision.required)
        if not s.auto_cast:
            self.status = prefix + ' | ' + label + ' | БЕЗ ВВОДА'
            if not decision.target.inventory_known:
                self.detail += ' | Инвентарь цели неизвестен; оценка без предметов, AUTO запрещён'
            return
        if not fresh(me, time.perf_counter(), s.stale):
            self.status = prefix + ' | ПАУЗА: решение устарело'
            return
        started = time.perf_counter()
        try:
            result, reason = b.cast(decision, s, context,
                lambda: self.toggle.enabled and not self.ui_menu_open and not getattr(self,'menu_foreground',False) and not b.down(decision.key))
        except Exception as exc:
            result, reason = 'failed', 'Ошибка backend: ' + str(exc)
        if result == 'skipped':
            self.status = prefix + ' | Ввод не отправлен: ' + reason
            return
        # Reserve exactly once for sent OR uncertain/partially inserted input.
        self.ledger.begin(decision, s, started)
        if result != 'sent':
            self.toggle.enabled = False
            self.off_reason = 'Ошибка ввода; повтор автоматически запрещён'
        self.status = 'KS5 %s | %s | %s' % ('SENT' if result == 'sent' else 'FAILED / OFF', label, reason)
        self._log_status(force=True)

    def poll_ui_controls(self):
        now = time.perf_counter()
        if now < self.ui_poll_at:
            return
        self.ui_poll_at = now + .05
        try:
            stat = self.ui_control_path.stat()
            marker = (stat.st_mtime_ns, stat.st_size)
            if marker == self.ui_mtime or stat.st_size > 8192:
                return
            self.ui_mtime = marker
            from ks_dashboard import apply_control
            doc = json.loads(self.ui_control_path.read_text(encoding='utf-8-sig'))
            if apply_control(self, doc):
                self.log.info('MENU open=%s disabled=%s; KS=%s DODGER=%s; visibility is a pause', self.ui_menu_open, sorted(self.ui_disabled),self.toggle.enabled,bool(self.dodger and self.dodger.enabled))
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError) as exc:
            self.log.warning('MENU request ignored: %s', exc)

    def wire_fields(self):
        clean = self.status.replace('\t', ' ').replace('\n', ' ').replace('\r', ' ')[:300]
        detail = self.detail.replace('\t', ' ').replace('\n', ' ').replace('\r', ' ')[:500]
        return '\t%s\t%d\t%d\t%s' % (clean, self.target_idx, int(self.toggle.enabled), detail)
