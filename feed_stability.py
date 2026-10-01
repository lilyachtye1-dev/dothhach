"""Small, platform-independent guards for the ESP feed worker."""
import os
import queue
import threading
import time

ENABLE_QUICK_EDIT_MODE = 0x0040
ENABLE_EXTENDED_FLAGS = 0x0080
FULL_SNAPSHOT_SECONDS = 2.5
EMPTY_SNAPSHOT_RETRY_SECONDS = 1.0


class AsyncSnapshotWorker:
    """Run expensive read-only entity snapshots away from the live-feed reader."""

    def __init__(self, capture):
        self._capture = capture
        self._requests = queue.Queue(maxsize=1)
        self._results = queue.Queue(maxsize=1)
        self._lock = threading.Lock()
        self._inflight = False
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, name="esp-full-snapshot", daemon=True)

    def start(self):
        self._thread.start()

    @property
    def in_flight(self):
        with self._lock:
            return self._inflight

    def request(self):
        with self._lock:
            if not self._running or self._inflight:
                return False
            self._inflight = True
        try:
            self._requests.put_nowait(True)
            return True
        except queue.Full:
            with self._lock:
                self._inflight = False
            return False

    def poll(self, timeout=0):
        try:
            result = (self._results.get(timeout=timeout) if timeout > 0
                      else self._results.get_nowait())
        except queue.Empty:
            return None
        with self._lock:
            self._inflight = False
        return result

    def stop(self):
        with self._lock:
            self._running = False
        try:
            self._requests.put_nowait(None)
        except queue.Full:
            pass

    def _loop(self):
        while True:
            try:
                request = self._requests.get(timeout=0.1)
            except queue.Empty:
                with self._lock:
                    if not self._running:
                        return
                continue
            with self._lock:
                if not self._running:
                    return
            if request is None:
                return
            started = time.perf_counter()
            value, error = None, ''
            try:
                value = self._capture()
            except Exception as exc:
                error = type(exc).__name__ + ': ' + str(exc)
            elapsed_ms = (time.perf_counter() - started) * 1000
            try:
                self._results.put((value, error, elapsed_ms), timeout=0.1)
            except queue.Full:
                # The reader already has an unconsumed result. Drop this one
                # rather than blocking the worker or accumulating stale scans.
                with self._lock:
                    self._inflight = False


def snapshot_due(now, last_full, initial, has_heroes,
                 full_interval=FULL_SNAPSHOT_SECONDS,
                 empty_retry=EMPTY_SNAPSHOT_RETRY_SECONDS):
    """Throttle expensive full scans, including while the roster is empty.

    A missing/transitioning roster must not turn the reader into a tight loop of
    full entity scans. The first scan is still immediate; after that, an empty
    roster is retried once per second and a populated roster every 2.5 seconds.
    """
    if initial:
        return True
    interval = full_interval if has_heroes else empty_retry
    return now - last_full >= interval


def mode_without_quick_edit(mode):
    """Return a Windows console mode with mouse-selection pausing disabled."""
    return (int(mode) | ENABLE_EXTENDED_FLAGS) & ~ENABLE_QUICK_EDIT_MODE


def disable_console_quick_edit():
    """Disable conhost Quick Edit so selecting feed-console text cannot suspend it.

    Returns True when a console mode was read and left in the requested state;
    returns False when stdin is redirected or the Win32 operation is unavailable.
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetConsoleMode.restype = wintypes.BOOL
        kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.SetConsoleMode.restype = wintypes.BOOL

        # STD_INPUT_HANDLE is the signed constant -10 represented as a DWORD.
        handle = kernel32.GetStdHandle(ctypes.c_uint32(-10).value)
        if not handle:
            return False
        current = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(current)):
            return False
        desired = mode_without_quick_edit(current.value)
        return desired == current.value or bool(kernel32.SetConsoleMode(handle, desired))
    except Exception:
        return False
