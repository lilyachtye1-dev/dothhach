"""Windows cast backend: ordinary SendInput, no injection or memory writes.
Spell paths use quickcast. Last Hit supports quick or normal Attack. Native menu identity is known;
in-game chat/shop/menus and selected-unit state are not decoded.
"""
import os
import time
import ctypes as C
from ctypes import wintypes as W
from ks_core import fresh, Decision
from dodge_input import keyboard_reason, mouse_independent_command, defensive_command


class WinInput:
    def __init__(self):
        if os.name != 'nt':
            raise OSError('Windows is required for input')
        self.u = C.WinDLL('user32', use_last_error=True)
        signatures = {
            'FindWindowW': ([W.LPCWSTR, W.LPCWSTR], W.HWND),
            'GetForegroundWindow': ([], W.HWND),
            'GetWindowThreadProcessId': ([W.HWND,C.POINTER(W.DWORD)], W.DWORD),
            'IsIconic': ([W.HWND], W.BOOL),
            'GetClientRect': ([W.HWND, C.POINTER(W.RECT)], W.BOOL),
            'ClientToScreen': ([W.HWND, C.POINTER(W.POINT)], W.BOOL),
            'GetCursorPos': ([C.POINTER(W.POINT)], W.BOOL),
            'SetCursorPos': ([C.c_int, C.c_int], W.BOOL),
            'GetSystemMetrics': ([C.c_int], C.c_int),
            'GetAsyncKeyState': ([C.c_int], C.c_short),
        }
        for name, (args, ret) in signatures.items():
            f = getattr(self.u, name)
            f.argtypes, f.restype = args, ret
        # Must use physical pixel coordinates, matching Dot.w2s and the WPF overlay.
        self.u.SetProcessDPIAware()
        class MOUSEINPUT(C.Structure):
            _fields_ = [('dx', W.LONG), ('dy', W.LONG), ('mouseData', W.DWORD),
                        ('dwFlags', W.DWORD), ('time', W.DWORD), ('dwExtraInfo', C.c_size_t)]
        class KEYBDINPUT(C.Structure):
            _fields_ = [('wVk', W.WORD), ('wScan', W.WORD), ('dwFlags', W.DWORD),
                        ('time', W.DWORD), ('dwExtraInfo', C.c_size_t)]
        class HARDWAREINPUT(C.Structure):
            _fields_ = [('uMsg', W.DWORD), ('wParamL', W.WORD), ('wParamH', W.WORD)]
        class UNION(C.Union):
            _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT), ('hi', HARDWAREINPUT)]
        class INPUT(C.Structure):
            _anonymous_ = ('data',)
            _fields_ = [('type', W.DWORD), ('data', UNION)]
        self.INPUT = INPUT
        expected_size = 40 if C.sizeof(C.c_void_p) == 8 else 28
        if C.sizeof(INPUT) != expected_size:
            raise RuntimeError('incorrect INPUT layout')
        self.u.SendInput.argtypes = [W.UINT, C.POINTER(INPUT), C.c_int]
        self.u.SendInput.restype = W.UINT
        from ks_key_state import WinModifierMonitor
        self.modifiers = WinModifierMonitor()

    def down(self, key):
        return bool(self.u.GetAsyncKeyState(key) & 0x8000)

    def context(self):
        hw = self.u.FindWindowW(None, 'Dota 2')
        if not hw or self.u.GetForegroundWindow() != hw or self.u.IsIconic(hw):
            return None
        r, origin = W.RECT(), W.POINT()
        if not self.u.GetClientRect(hw, C.byref(r)) or not self.u.ClientToScreen(hw, C.byref(origin)):
            return None
        if r.right <= r.left or r.bottom <= r.top:
            return None
        return hw, origin.x, origin.y, r.right - r.left, r.bottom - r.top

    def menu_context(self, handle, pid):
        if not handle or not pid or self.u.GetForegroundWindow()!=handle:return False
        owner=W.DWORD()
        return bool(self.u.GetWindowThreadProcessId(handle,C.byref(owner)) and owner.value==pid)

    def interruption_reason(self):
        keys = ((0x0D, 'Enter'), (0x09, 'Tab'),
                (0x10, 'Shift'), (0x11, 'Ctrl'), (0x12, 'Alt'),
                (0x5B, 'Win-L'), (0x5C, 'Win-R'), (0x01, 'ЛКМ'), (0x02, 'ПКМ'))
        return ', '.join(name for code, name in keys if self.down(code))

    def interrupted(self):
        return bool(self.interruption_reason())

    def hard_stop_reason(self):
        keys = ((0x0D, 'Enter'), (0x09, 'Tab'), (0x23, 'End'))
        return ', '.join(name for code, name in keys if self.down(code))

    def cast(self, decision, settings, expected_context, armed):
        """Return sent/skipped/failed. 'sent' means input inserted, not a kill.
        'skipped' guarantees no key was submitted, so it must NOT create a pending cast.
        """
        self.finish_last_hit_mouse_priority('higher-priority cast')
        if getattr(settings,'defensive_input',False):
            if not defensive_command(decision,settings):return 'skipped','защитный профиль / бинд / источник не подтверждён'
            return self.cast_defense(decision, settings, expected_context, armed)
        independent = mouse_independent_command(decision, settings)
        if not independent and isinstance(decision, Decision):
            return self._cast_priority(decision, settings, expected_context, armed, killsteal=True)
        def guard():
            blocked = (keyboard_reason(self, getattr(settings,'counterspell_block_keys',()))
                       if independent else self.interrupted())
            return (settings.auto_cast and bool(decision.key) and armed() and
                    not (settings.toggle_vk and self.down(settings.toggle_vk)) and not self.hard_stop_reason() and not self.down(45) and
                    not blocked and not self.down(decision.key) and
                    self.context() == expected_context and
                    fresh(decision.target, time.perf_counter(), settings.stale))
        if not guard():
            return 'skipped', 'контекст/клавиши изменились до ввода'
        if decision.profile.needs_cursor:
            x = round(expected_context[1] + decision.target.screen[0])
            offset = settings.aim_y if decision.profile.cast == 'unit' else 0
            y = round(expected_context[2] + decision.target.screen[1] + offset)
            if not self.u.SetCursorPos(x, y):
                return 'skipped', 'SetCursorPos failed'
            actual = W.POINT()
            if not self.u.GetCursorPos(C.byref(actual)) or abs(actual.x - x) > 2 or abs(actual.y - y) > 2:
                return 'skipped', 'курсор ограничен окном/экраном'
        if not guard():
            return 'skipped', 'контекст изменился после наведения'
        pair = (self.INPUT * 2)()
        pair[0].type = pair[1].type = 1  # INPUT_KEYBOARD, never INPUT_MOUSE
        pair[0].ki.wVk = pair[1].ki.wVk = decision.key
        pair[1].ki.dwFlags = 0x0002  # KEYEVENTF_KEYUP
        count = self.u.SendInput(2, pair, C.sizeof(self.INPUT))
        if count != 2:
            release = self.INPUT()
            release.type, release.ki.wVk, release.ki.dwFlags = 1, decision.key, 0x0002
            self.u.SendInput(1, C.byref(release), C.sizeof(self.INPUT))
            return 'failed', 'SendInput %d/2; OFF, результат неизвестен' % count
        return 'sent', 'quickcast key-down/up; ожидается CD, убийство не подтверждено'

    def _keys(self, events):
        if not events:return 0
        packet=(self.INPUT*len(events))()
        for i,(key,up) in enumerate(events):
            packet[i].type=1;packet[i].ki.wVk=key;packet[i].ki.dwFlags=(2 if up else 0)|(1 if key in (0xA3,0xA5) else 0)
        return self.u.SendInput(len(events),packet,C.sizeof(self.INPUT))

    def _modifier_snapshot(self):
        from ks_key_state import MODIFIERS
        active=frozenset(k for k in MODIFIERS if self.down(k))
        # Aggregate-only modifier state cannot be safely restored by side.
        if any(self.down(k) for k in (16,17,18)) and not active:return None
        monitor=getattr(self,'modifiers',None)
        physical=monitor.snapshot() if monitor else None
        if physical is not None and physical!=active:return None
        if not active:return active
        return active if physical==active else None

    def _restore_modifiers(self, touched):
        if not touched:return True
        monitor=getattr(self,'modifiers',None)
        for _ in range(2):
            physical=monitor.snapshot() if monitor else None
            if physical is None:return False  # Never invent key-down from an old snapshot.
            events=[(k,k not in physical) for k in sorted(touched) if self.down(k)!=(k in physical)]
            if not events:return True
            if self._keys(events)!=len(events):return False
        physical=monitor.snapshot()
        return physical is not None and all(self.down(k)==(k in physical) for k in touched)

    def cast_defense(self, decision, settings, expected_context, armed):
        return self._cast_priority(decision, settings, expected_context, armed)

    def _cast_priority(self, decision, settings, expected_context, armed, killsteal=False):
        """Shared bounded priority transaction for KS and validated defenses.
        Ordinary letters/mouse do not veto. One spell key pair.
        Shift/Ctrl/Alt are temporarily neutralized only with live physical tracking;
        cleanup follows physical releases, not a blind restore of the old state.
        """
        self.finish_last_hit_mouse_priority('higher-priority cast')
        def guard():
            caster=getattr(decision,'caster',decision.target)
            return (settings.auto_cast and bool(decision.key) and armed() and
                    not (self.down(35) if killsteal else self.hard_stop_reason()) and
                    not any(self.down(k) for k in ((13,9,45,91,92,decision.key) if killsteal else (45,91,92,decision.key))) and
                    self.context()==expected_context and fresh(caster,time.perf_counter(),settings.stale) and
                    fresh(decision.target,time.perf_counter(),settings.stale))
        if not guard():return 'skipped','контекст / меню / собственная клавиша / Win'
        if self._modifier_snapshot() is None:return 'skipped','нет свежего физического состояния Shift/Ctrl/Alt'
        original=None;aim=None;touched=();attempted=False
        self_cast=bool(getattr(decision.profile,'self_cast',False))
        try:
            if decision.profile.needs_cursor:
                old=W.POINT()
                if not self.u.GetCursorPos(C.byref(old)):return 'skipped','курсор не прочитан'
                original=(old.x,old.y)
                offset=settings.aim_y if decision.profile.cast=='unit' else 0
                aim=(round(expected_context[1]+decision.target.screen[0]),round(expected_context[2]+decision.target.screen[1]+offset))
                if not self.u.SetCursorPos(*aim):return 'skipped','SetCursorPos failed'
                actual=W.POINT()
                if not self.u.GetCursorPos(C.byref(actual)) or (actual.x,actual.y)!=aim:
                    return 'skipped','курсор изменился / ограничен'
            if not guard():return 'skipped','контекст изменился перед вводом'
            modifiers=self._modifier_snapshot()
            if modifiers is None:return 'skipped','модификаторы изменились перед вводом'
            monitor=getattr(self,'modifiers',None)
            if self_cast and (not monitor or monitor.snapshot() is None):
                return 'skipped','self-cast: нет наблюдения физических модификаторов'
            if aim is not None:
                actual=W.POINT()
                if not self.u.GetCursorPos(C.byref(actual)) or (actual.x,actual.y)!=aim:
                    return 'skipped','цель наведения изменилась перед вводом'
            touched=tuple(sorted(set(modifiers)|({0xA4} if self_cast else set())))
            prefix=[(k,True) for k in sorted(modifiers)]+([(0xA4,False)] if self_cast else [])
            events=prefix+[(decision.key,False),(decision.key,True)]+([(0xA4,True)] if self_cast else [])
            attempted=True
            count=self._keys(events)
            if count!=len(events):
                if count>len(prefix):self._keys([(decision.key,True)])
                return 'failed','частичный ввод; повтор запрещён'
            if not self._restore_modifiers(touched):return 'failed','ввод отправлен; восстановление модификаторов не подтверждено; OFF'
            touched=()
            return 'sent',('Alt self-cast' if self_cast else 'KS quickcast' if killsteal else 'защитный quickcast')+'; ручные клавиши/мышь не паузят; ожидается CD'
        except Exception as exc:
            if attempted:
                try:self._keys([(decision.key,True)])
                except Exception:pass
                return 'failed','ошибка приоритетного ввода: '+str(exc)
            return 'skipped','ошибка до ввода: '+str(exc)
        finally:
            if touched:
                try:self._restore_modifiers(touched)
                except Exception:pass
            if not attempted and original is not None and aim is not None:
                # Undo an UNUSED aim only; sent targeted casts retain their aim as before.
                # Optional Alt self-targeting never enters this cursor branch.
                try:
                    actual=W.POINT()
                    if self.context()==expected_context and self.u.GetCursorPos(C.byref(actual)) and (actual.x,actual.y)==aim:
                        self.u.SetCursorPos(*original)
                except Exception:pass

    def last_hit_attack(self, screen, key, expected_context, allowed, quick=False):
        """One bounded NORMAL Attack-key + left-click transaction. Quick Attack OFF.
        Mouse4/5 may stay held. No second sender, no injection, no game orders API.
        """
        if quick:return self.last_hit_quick_attack(screen,key,expected_context,allowed)
        def guard():
            return (allowed() and self.context()==expected_context and not self.down(key) and
                    not any(self.down(k) for k in (1,9,13,35,45,91,92)))
        if not guard():return 'skipped','кнопка отпущена / меню / собственная клавиша'
        if len(screen)!=2 or not all(isinstance(x,(int,float)) for x in screen):return 'skipped','нет точки атаки'
        if not (8<screen[0]<expected_context[3]-8 and .09*expected_context[4]<screen[1]<.79*expected_context[4]):
            return 'skipped','точка вне игровой области'
        original=W.POINT();aim=None;touched=();attempted=False
        if not self.u.GetCursorPos(C.byref(original)):return 'skipped','курсор не прочитан'
        try:
            if self._modifier_snapshot() is None:return 'skipped','нет физических модификаторов'
            aim=(round(expected_context[1]+screen[0]),round(expected_context[2]+screen[1]))
            if not self.u.SetCursorPos(*aim):return 'skipped','наведение не выполнено'
            if not guard():return 'skipped','отмена после наведения'
            actual=W.POINT()
            if not self.u.GetCursorPos(C.byref(actual)) or (actual.x,actual.y)!=aim:return 'skipped','курсор сместился'
            mods=self._modifier_snapshot()
            if mods is None:return 'skipped','модификаторы изменились'
            touched=tuple(sorted(mods));prefix=[(k,True) for k in touched]
            events=prefix+[(key,False),(key,True)]
            packet=(self.INPUT*(len(events)+2))()
            for i,(vk,up) in enumerate(events):
                packet[i].type=1;packet[i].ki.wVk=vk
                packet[i].ki.dwFlags=(2 if up else 0)|(1 if vk in (0xA3,0xA5) else 0)
            packet[len(events)].type=packet[len(events)+1].type=0
            packet[len(events)].mi.dwFlags=0x0002;packet[len(events)+1].mi.dwFlags=0x0004
            if not guard():return 'skipped','отмена непосредственно перед вводом'
            if not self.u.GetCursorPos(C.byref(actual)) or (actual.x,actual.y)!=aim:return 'skipped','курсор изменился перед атакой'
            if self._modifier_snapshot()!=mods:return 'skipped','модификаторы изменились перед атакой'
            attempted=True;count=self.u.SendInput(len(packet),packet,C.sizeof(self.INPUT))
            if count!=len(packet):
                if count>len(prefix):self._keys([(key,True)])
                if count>len(events):
                    release=self.INPUT();release.type=0;release.mi.dwFlags=0x0004
                    self.u.SendInput(1,C.byref(release),C.sizeof(self.INPUT))
                return 'failed','частичный ввод; Last Hit выключен'
            if not self._restore_modifiers(touched):return 'failed','восстановление модификаторов не подтверждено'
            touched=()
            return 'sent','атака отправлена; добивание не подтверждено'
        except Exception as exc:
            if attempted:
                try:
                    self._keys([(key,True)])
                    release=self.INPUT();release.type=0;release.mi.dwFlags=0x0004
                    self.u.SendInput(1,C.byref(release),C.sizeof(self.INPUT))
                except Exception:pass
            return ('failed' if attempted else 'skipped'),str(exc)[:140]
        finally:
            if touched:
                try:self._restore_modifiers(touched)
                except Exception:pass
            if not attempted and aim is not None:
                try:
                    actual=W.POINT()
                    if self.context()==expected_context and self.u.GetCursorPos(C.byref(actual)) and (actual.x,actual.y)==aim:
                        self.u.SetCursorPos(original.x,original.y)
                except Exception:pass


    def last_hit_quick_attack(self,screen,key,expected_context,allowed,click=None,return_cursor=False,manual_generation=None):
        """Atomic absolute aim + attack key, no LMB and no cursor lock.
        One final memory/IPC permission check, then live input guards. Same sender.
        Game Quick Attack must be ON. Never silently falls back to normal attack.
        """
        def context_ok():
            return (self.context()==expected_context and (click=='enemy' or not self.down(key)) and
                    not any(self.down(k) for k in (1,2,9,13,35,45,91,92)))
        if not context_ok():return 'skipped','контекст / собственная клавиша'
        if len(screen)!=2 or not all(isinstance(x,(int,float)) for x in screen):return 'skipped','нет точки атаки'
        if not (8<screen[0]<expected_context[3]-8 and .09*expected_context[4]<screen[1]<.79*expected_context[4]):
            return 'skipped','точка вне игровой области'
        touched=();attempted=False;original=None;motion=None;intent=None;aim=None
        try:
            left,top,width,height=(self.u.GetSystemMetrics(k) for k in (76,77,78,79))
            x=round(expected_context[1]+screen[0]);y=round(expected_context[2]+screen[1])
            if width<2 or height<2 or not left<=x<left+width or not top<=y<top+height:
                return 'skipped','геометрия виртуального экрана'
            mods=self._modifier_snapshot()
            if mods is None:return 'skipped','нет физических модификаторов'
            prefix=[(k,True) for k in sorted(mods)]
            # Win32 absolute coordinates target the centre of the physical pixel.
            packet=(self.INPUT*(len(prefix)+(5 if click=='deny' else 3)))()
            for i,(vk,up) in enumerate(prefix):
                packet[i].type=1;packet[i].ki.wVk=vk
                packet[i].ki.dwFlags=2|(1 if vk in (0xA3,0xA5) else 0)
            move=len(prefix);packet[move].type=0
            packet[move].mi.dx=min(65535,int((x-left+.5)*65536/width))
            packet[move].mi.dy=min(65535,int((y-top+.5)*65536/height))
            packet[move].mi.dwFlags=0x0001|0x8000|0x4000|0x2000
            if click!='enemy':
                for i,up in ((move+1,False),(move+2,True)):
                    packet[i].type=1;packet[i].ki.wVk=key;packet[i].ki.dwFlags=2 if up else 0
            mouse_down=move+1 if click=='enemy' else move+3
            if click:
                packet[mouse_down].type=packet[mouse_down+1].type=0
                packet[mouse_down].mi.dwFlags=8 if click=='enemy' else 2
                packet[mouse_down+1].mi.dwFlags=16 if click=='enemy' else 4
            if not allowed() or not context_ok():return 'skipped','отмена перед быстрой атакой'
            if self._modifier_snapshot()!=mods:return 'skipped','модификаторы изменились'
            # Save the latest USER location after the expensive permission read.
            # Restore only if neither physical movement nor another command occurred.
            if return_cursor:
                motion=self.cursor_token();intent=self.activity_token()
                original=W.POINT()
                if motion is None or intent is None or not self.u.GetCursorPos(C.byref(original)):
                    return 'skipped','нет наблюдения мыши для возврата курсора'
                aim=(x,y)
            if not context_ok():return 'skipped','контекст изменился перед кликом'
            if manual_generation is not None and self.activity_token()!=manual_generation:
                return 'skipped','новая ручная команда перед кликом'
            if return_cursor and self.cursor_token()!=motion:
                original=None  # user moved: still attack, but do not restore an old point
            if self._modifier_snapshot()!=mods:return 'skipped','модификаторы изменились перед кликом'
            touched=tuple(sorted(mods));attempted=True
            self._lh_cursor_return=None  # a newer Last Hit transaction supersedes cleanup
            count=self.u.SendInput(len(packet),packet,C.sizeof(self.INPUT))
            if count!=len(packet):
                if click!='enemy' and count>move+1:self._keys([(key,True)])
                if click and count>mouse_down:self._last_hit_mouse_up(click)
                return 'failed','частичный ввод атаки; повтор запрещён'
            if not self._restore_modifiers(touched):return 'failed','восстановление модификаторов не подтверждено'
            touched=()
            if click:return 'sent',('ПКМ по врагу' if click=='enemy' else 'A/бинд + ЛКМ по союзнику')+'; добивание не подтверждено'
            return 'sent','быстрая атака '+chr(key)+'; без ЛКМ; добивание не подтверждено'
        except Exception as exc:
            if attempted:
                try:
                    if click!='enemy':self._keys([(key,True)])
                    if click:self._last_hit_mouse_up(click)
                except Exception:pass
            return ('failed' if attempted else 'skipped'),str(exc)[:140]
        finally:
            if touched:
                try:self._restore_modifiers(touched)
                except Exception:pass
            if return_cursor and attempted and original is not None:
                # SendInput insertion is NOT a game-consumption acknowledgement.
                # Never teleport back in the same transaction: Dota may sample
                # the cursor on its next input frame. Same sender drains one slot.
                at=time.perf_counter()
                self._lh_cursor_return=(expected_context,(original.x,original.y),aim,
                                        motion,intent,at+.060,at+.180)

    def flush_last_hit_cursor(self,now,allowed):
        pending=getattr(self,'_lh_cursor_return',None)
        if pending is None:return None
        context,original,aim,motion,intent,due,expires=pending
        try:
            # Discard on interference even before due, not just on the return tick.
            if (now>expires or not allowed() or self.context()!=context or
                    self.cursor_token()!=motion or self.activity_token()!=intent or
                    any(self.down(k) for k in (1,2,9,13,35,45,91,92))):
                self._lh_cursor_return=None;return 'discarded'
            if now<due:return 'waiting'
            self._lh_cursor_return=None
            actual=W.POINT()
            if not self.u.GetCursorPos(C.byref(actual)):return 'unreadable'
            if abs(actual.x-aim[0])>1 or abs(actual.y-aim[1])>1:return 'moved'
            if (not allowed() or time.perf_counter()>expires or self.context()!=context or self.cursor_token()!=motion or
                    self.activity_token()!=intent):return 'discarded'
            return 'restored' if self.u.SetCursorPos(*original) else 'failed'
        except Exception:
            self._lh_cursor_return=None;return 'failed'

    def _last_hit_mouse_up(self,click):
        release=self.INPUT();release.type=0;release.mi.dwFlags=16 if click=='enemy' else 4
        self.u.SendInput(1,C.byref(release),C.sizeof(self.INPUT))

    def last_hit_target_click(self,screen,key,context,allowed,deny=False):
        return self.last_hit_quick_attack(screen,key,context,allowed,click='deny' if deny else 'enemy')

    def last_hit_rmb(self,screen,context,allowed,deny=False,return_cursor=True):
        # Allied RMB is an attack ONLY with Dota's right-click-allies=attack setting.
        # No Attack key and no LMB are ever emitted by this path.
        generation=self.activity_token()
        if generation is None:return 'skipped','нет наблюдения ручных команд'
        result,note=self.last_hit_quick_attack(screen,0,context,allowed,click='enemy',
                                             return_cursor=return_cursor,manual_generation=generation)
        if result=='sent':note=('ПКМ по союзнику' if deny else 'ПКМ по врагу')+'; добивание не подтверждено'
        return result,note

    def _last_hit_buttons(self,events):
        packet=(self.INPUT*len(events))()
        for i,(key,up) in enumerate(events):
            if key not in (1,2):raise ValueError('mouse button must be LMB/RMB')
            packet[i].type=0;packet[i].mi.dwFlags=(4 if up else 2) if key==1 else (16 if up else 8)
        return self.u.SendInput(len(packet),packet,C.sizeof(self.INPUT)) if events else 0

    def finish_last_hit_mouse_priority(self,reason,resume=True):
        window=getattr(self,'_lh_mouse_priority',None)
        if window is None:return None
        self._lh_mouse_priority=None;self._lh_cursor_return=None
        window.finish(reason,resume)
        return window

    def watch_last_hit_mouse_priority(self,now):
        window=getattr(self,'_lh_mouse_priority',None)
        if window is None:return None
        from last_hit_mouse import snapshot
        if (now>=window.deadline or self.context()!=window.context or
                self.activity_token()!=window.token or snapshot(self) is None or
                any(self.down(k) for k in (9,13,35,45,91,92))):
            return self.finish_last_hit_mouse_priority('deadline / context / manual command')
        try:window.maintain(now)
        except Exception:return self.finish_last_hit_mouse_priority('cursor error',resume=False)
        return None

    def last_hit_priority_rmb(self,screen,context,allowed,seconds,deny=False,return_cursor=True):
        from last_hit_mouse import MouseWindow,point,play_point,snapshot
        if getattr(self,'_lh_mouse_priority',None) is not None:return 'skipped','окно мыши уже занято'
        state=snapshot(self)
        if state is None:return 'skipped','нет физического состояния ЛКМ/ПКМ'
        held,versions=state
        if held!=frozenset(k for k in (1,2) if self.down(k)):
            return 'skipped','состояние мыши ещё не согласовано'
        if not held:return self.last_hit_rmb(screen,context,allowed,deny,return_cursor)
        if (len(screen)!=2 or not all(isinstance(v,(int,float)) for v in screen) or
                not 8<screen[0]<context[3]-8 or not .09*context[4]<screen[1]<.79*context[4]):
            return 'skipped','нет игровой точки атаки'
        if not allowed() or self.context()!=context:return 'skipped','отмена перед приоритетом мыши'
        original=point(self);token=self.activity_token();motion=self.cursor_token()
        if token is None or motion is None or not play_point(context,original):return 'skipped','мышь вне игровой области / нет наблюдения'
        if snapshot(self)!=state:return 'skipped','физическая мышь изменилась перед передачей'
        try:
            window=MouseWindow(self,context,held,versions,original,
                               (round(context[1]+screen[0]),round(context[2]+screen[1])),token,motion,seconds)
        except ValueError:return 'skipped','замах / поворот длиннее разрешённого окна мыши'
        self._lh_mouse_priority=window;self._lh_cursor_return=None
        try:
            if self._last_hit_buttons([(k,True) for k in sorted(held)])!=len(held):
                self.finish_last_hit_mouse_priority('partial neutralization')
                return 'failed','неполное отпускание мыши; повтор запрещён'
            result,note=self.last_hit_rmb(screen,context,lambda:allowed() and self.activity_token()==token and time.perf_counter()+seconds<=window.deadline,
                                         deny=deny,return_cursor=False)
            if result!='sent':self.finish_last_hit_mouse_priority('attack '+result)
            else:
                window.sent=True
                # Permission reads may be expensive: FIX1's 60ms starts AFTER
                # attack insertion, not before borrowing the held buttons.
                inserted=time.perf_counter()
                window.cursor_due=inserted+.060
                window.due=min(window.deadline,inserted+seconds)
                note+='; короткий приоритет мыши'
            return result,note
        except Exception as exc:
            self.finish_last_hit_mouse_priority('exception')
            return 'failed',str(exc)[:140]

    def cursor_token(self):
        monitor=getattr(self,'modifiers',None)
        return monitor.motion_token() if monitor and hasattr(monitor,'motion_token') else None

    def activity_token(self):
        monitor=getattr(self,'modifiers',None)
        return monitor.activity_token() if monitor else None

    def last_hit_stop(self,key,context,allowed):
        """Stop only an owned Last Hit order. Caller revalidates manual generation."""
        def guard():
            return (self.context()==context and not self.down(key) and
                    not any(self.down(k) for k in (1,2,9,13,35,45,91,92)))
        if not guard():return 'skipped','Stop: контекст / клавиши'
        touched=();attempted=False
        try:
            mods=self._modifier_snapshot()
            if mods is None:return 'skipped','Stop: нет наблюдения модификаторов'
            events=[(k,True) for k in sorted(mods)]+[(key,False),(key,True)]
            if not allowed() or not guard() or self._modifier_snapshot()!=mods:return 'skipped','Stop отменён владельцем/контекстом'
            touched=tuple(sorted(mods));attempted=True
            count=self._keys(events)
            if count!=len(events):
                if count>len(mods):self._keys([(key,True)])
                return 'failed','частичный Stop'
            if not self._restore_modifiers(touched):return 'failed','Stop: модификаторы не восстановлены'
            touched=()
            return 'sent','Stop после своей атаки'
        except Exception as exc:
            if attempted:
                try:self._keys([(key,True)])
                except Exception:pass
            return ('failed' if attempted else 'skipped'),str(exc)[:140]
        finally:
            if touched:
                try:self._restore_modifiers(touched)
                except Exception:pass
