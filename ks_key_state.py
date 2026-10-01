"""Physical modifier observer for a bounded defensive key transaction.
Modifier states plus a manual-input generation counter; no text or key history is logged.
The observer never sends input. All SendInput remains on the existing sender.
"""
import ctypes as C
from ctypes import wintypes as W
import threading
import time

MODIFIERS=(0xA0,0xA1,0xA2,0xA3,0xA4,0xA5)


class PhysicalModifiers:
    def __init__(self):
        self.lock=threading.RLock();self.keys=set();self.alive=False;self.at=0.
        self.buttons=set();self.button_versions={1:0,2:0};self.mouse_ready=False
        self.order_seq=0;self.motion_seq=0;self.orders_ready=False;self._held=set()
    def event(self,key,down,injected=False):
        if injected:return
        with self.lock:
            if key in MODIFIERS:
                if down:self.keys.add(key)
                else:self.keys.discard(key)
            else:
                if down and key not in self._held:self.order_seq+=1
                if down:self._held.add(key)
                else:self._held.discard(key)
    def mouse_event(self,message,injected=False):
        if injected:return
        transitions={0x201:(1,True),0x202:(1,False),0x204:(2,True),0x205:(2,False)}
        if message in transitions:
            key,down=transitions[message]
            with self.lock:
                if down!=(key in self.buttons):self.button_versions[key]+=1
                if down:self.buttons.add(key)
                else:self.buttons.discard(key)
        if message==0x200:
            with self.lock:self.motion_seq+=1
        # Only button-down/wheel intent, not motion and not button history.
        if message in (0x201,0x204,0x207,0x20b,0x20a,0x20e):
            with self.lock:self.order_seq+=1
    def mouse_snapshot(self):
        with self.lock:
            if not self.mouse_ready or not self.orders_ready or not self.alive or not 0<=time.perf_counter()-self.at<=.25:return None
            return frozenset(self.buttons),dict(self.button_versions)
    def activity_token(self):
        with self.lock:
            if not self.orders_ready or not self.alive or not 0<=time.perf_counter()-self.at<=.25:return None
            return self.order_seq
    def motion_token(self):
        with self.lock:
            if not self.orders_ready or not self.alive or not 0<=time.perf_counter()-self.at<=.25:return None
            return self.motion_seq
    def snapshot(self):
        with self.lock:
            if not self.alive or not 0<=time.perf_counter()-self.at<=.25:return None
            return frozenset(self.keys)


class WinModifierMonitor(PhysicalModifiers):
    def __init__(self):
        super().__init__()
        self.ready=threading.Event()
        threading.Thread(target=self.run,name='Modifier observer',daemon=True).start()
        self.ready.wait(.5)

    def run(self):
        hook=None;mouse_hook=None
        try:
            u=C.WinDLL('user32',use_last_error=True);k=C.WinDLL('kernel32',use_last_error=True)
            result=C.c_ssize_t
            proc=C.WINFUNCTYPE(result,C.c_int,W.WPARAM,W.LPARAM)
            class KBD(C.Structure):
                _fields_=[('vkCode',W.DWORD),('scanCode',W.DWORD),('flags',W.DWORD),('time',W.DWORD),('extra',C.c_size_t)]
            u.SetWindowsHookExW.argtypes=[C.c_int,proc,W.HINSTANCE,W.DWORD];u.SetWindowsHookExW.restype=W.HANDLE
            u.CallNextHookEx.argtypes=[W.HANDLE,C.c_int,W.WPARAM,W.LPARAM];u.CallNextHookEx.restype=result
            u.UnhookWindowsHookEx.argtypes=[W.HANDLE];u.UnhookWindowsHookEx.restype=W.BOOL
            u.PeekMessageW.argtypes=[C.POINTER(W.MSG),W.HWND,W.UINT,W.UINT,W.UINT];u.PeekMessageW.restype=W.BOOL
            u.TranslateMessage.argtypes=[C.POINTER(W.MSG)];u.DispatchMessageW.argtypes=[C.POINTER(W.MSG)]
            u.DispatchMessageW.restype=result
            u.GetAsyncKeyState.argtypes=[C.c_int];u.GetAsyncKeyState.restype=C.c_short
            k.GetModuleHandleW.argtypes=[W.LPCWSTR];k.GetModuleHandleW.restype=W.HMODULE
            def callback(code,message,pointer):
                if code>=0 and message in (0x100,0x101,0x104,0x105):
                    try:
                        e=C.cast(pointer,C.POINTER(KBD)).contents
                        key=int(e.vkCode)
                        if key==16:key=0xA1 if e.scanCode==0x36 else 0xA0
                        elif key==17:key=0xA3 if e.flags&1 else 0xA2
                        elif key==18:key=0xA5 if e.flags&1 else 0xA4
                        self.event(key,message in (0x100,0x104),bool(e.flags&0x10))
                    except Exception:
                        self.alive=False
                return u.CallNextHookEx(hook,code,message,pointer)
            class MOUSE(C.Structure):
                _fields_=[('pt',W.POINT),('data',W.DWORD),('flags',W.DWORD),('time',W.DWORD),('extra',C.c_size_t)]
            def mouse_callback(code,message,pointer):
                if code>=0 and message in (0x200,0x201,0x202,0x204,0x205,0x207,0x20b,0x20a,0x20e):
                    try:
                        e=C.cast(pointer,C.POINTER(MOUSE)).contents
                        self.mouse_event(message,bool(e.flags&1))
                    except Exception:self.orders_ready=False
                return u.CallNextHookEx(mouse_hook,code,message,pointer)
            mouse_cb=proc(mouse_callback)
            cb=proc(callback)  # Keep callback strongly referenced for the entire pump.
            hook=u.SetWindowsHookExW(13,cb,k.GetModuleHandleW(None),0)
            if not hook:return
            mouse_hook=u.SetWindowsHookExW(14,mouse_cb,k.GetModuleHandleW(None),0)
            with self.lock:
                self.orders_ready=bool(mouse_hook);self.mouse_ready=bool(mouse_hook)
                self.buttons={key for key in (1,2) if u.GetAsyncKeyState(key)&0x8000}
                self._held={key for key in range(8,256) if key not in MODIFIERS and u.GetAsyncKeyState(key)&0x8000}
                self.keys={key for key in MODIFIERS if u.GetAsyncKeyState(key)&0x8000}
                self.alive=True;self.at=time.perf_counter()
            self.ready.set();msg=W.MSG()
            while self.alive:
                while u.PeekMessageW(C.byref(msg),None,0,0,1):
                    if msg.message==0x12:return
                    u.TranslateMessage(C.byref(msg));u.DispatchMessageW(C.byref(msg))
                with self.lock:self.at=time.perf_counter()
                time.sleep(.002)
        except Exception:
            pass
        finally:
            self.alive=False;self.orders_ready=False;self.mouse_ready=False;self.ready.set()
            if hook:u.UnhookWindowsHookEx(hook)
            if mouse_hook:u.UnhookWindowsHookEx(mouse_hook)
