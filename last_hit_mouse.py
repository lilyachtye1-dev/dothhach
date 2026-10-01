"""One bounded mouse-button loan on the EXISTING sender thread.
No BlockInput, no hook swallowing, no timer/thread sending input. Physical button
tracking is independent of injected transitions. Game auto-repeat is paused by
logical button-up, not by pretending that game-generated orders are observable.
"""
import ctypes as C
from ctypes import wintypes as W
import math
import time


def point(backend):
    p=W.POINT()
    return (p.x,p.y) if backend.u.GetCursorPos(C.byref(p)) else None


def play_point(context,p):
    return bool(context and p and 8<p[0]-context[1]<context[3]-8 and
                .09*context[4]<p[1]-context[2]<.79*context[4])


def snapshot(backend):
    observer=getattr(backend,'modifiers',None)
    return observer.mouse_snapshot() if observer and hasattr(observer,'mouse_snapshot') else None


class MouseWindow:
    MAX_SECONDS=.90
    def __init__(self,backend,context,held,versions,original,aim,token,motion,seconds):
        if not isinstance(seconds,(int,float)) or not math.isfinite(seconds) or not .12<=seconds<=.85:
            raise ValueError('mouse window duration outside .12..85s')
        self.backend=backend;self.context=context;self.held=frozenset(held);self.versions=dict(versions)
        self.original=original;self.aim=aim;self.token=token;self.motion=motion
        self.started=time.perf_counter();self.due=self.started+seconds
        self.deadline=self.started+self.MAX_SECONDS;self.cursor_due=self.started+.060
        self.cursor_done=False;self.closed=False;self.sent=False
        self.reason='';self.resumed=();self.failed=False

    def restore_cursor(self):
        b=self.backend
        if self.cursor_done:return True
        if b.context()!=self.context:return False
        current=point(b)
        if current is None:return False
        # A real movement or new command owns the current point; do not warp it.
        if b.cursor_token()!=self.motion or b.activity_token()!=self.token or abs(current[0]-self.aim[0])>1 or abs(current[1]-self.aim[1])>1:
            self.cursor_done=True;return True
        self.cursor_done=bool(b.u.SetCursorPos(*self.original))
        return self.cursor_done

    def maintain(self,now):
        if not self.closed and now>=self.cursor_due:self.restore_cursor()

    def finish(self,reason,resume=True):
        try:self._finish(reason,resume)
        except Exception:
            self.closed=True;self.failed=True;self.reason=reason+' / cleanup failed'
            key=getattr(self,'attempting_key',None)
            if key is not None:
                try:self.backend._last_hit_buttons([(key,True)])
                except Exception:pass

    def _finish(self,reason,resume=True):
        if self.closed:return
        self.closed=True;self.reason=reason;b=self.backend
        # Never replay a held mouse press over the desktop, a menu or a HUD.
        if not resume or b.context()!=self.context or any(b.down(k) for k in (9,13,35,45,91,92)):
            return
        if not self.restore_cursor():return
        restored=[]
        for key in sorted(self.held):
            state=snapshot(b)
            if (state is None or b.context()!=self.context or not play_point(self.context,point(b)) or
                    any(b.down(k) for k in (9,13,35,45,91,92))):break
            held,versions=state
            # A release/repress is a NEW physical gesture. It must not receive
            # a delayed synthetic down from the original gesture.
            if key not in held or versions.get(key)!=self.versions.get(key) or b.down(key):continue
            self.attempting_key=key
            if b._last_hit_buttons([(key,False)])!=1:
                b._last_hit_buttons([(key,True)]);self.failed=True;break
            restored.append(key)
            state=snapshot(b)
            if state is None or key not in state[0] or state[1].get(key)!=self.versions.get(key):
                b._last_hit_buttons([(key,True)])
            self.attempting_key=None
        self.resumed=tuple(restored)
