# -*- coding: utf-8 -*-
"""
esp_feed.py — читает Dota 2 (через dota_esp.Dot) и шлёт данные плашек
по UDP (127.0.0.1:27971) для C#-оверлея esp_overlay.cs.

Запуск (два окна):
  1) py -3.11 esp_feed.py            <- этот файл
  2) esp_overlay.exe                 <- скомпилированный C# (см. инструкцию)

Никаких новых pip-зависимостей не нужно.
"""
import os
import copy
import math
import json
import sys
import time
import socket
import threading
import urllib.request

from dota_esp import Dot
import dota_esp
from ks_service import KillSteal
from ks_dashboard import wire_dashboard
from zoom.integrated import CameraService,attach_state
from esp_visibility import overlay_hero_visible
from feed_stability import AsyncSnapshotWorker, disable_console_quick_edit, snapshot_due
import re

# Schema offset is a display radius, NOT C_DOTABaseAbility cast range.
# Extracted from the attached client_dll.json (2026-09-26): 4888 == 0x1318.
RANGE_DISPLAY_OFFSET = 4888
try:
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "client_dll.json"), encoding="utf-8") as schema_file:
        RANGE_DISPLAY_OFFSET = int(json.load(schema_file)["client.dll"]["classes"]
                                   ["C_DOTA_BaseNPC"]["fields"]["m_flRangeDisplayDist"])
except (OSError, ValueError, KeyError, TypeError):
    pass


def valid_world_position(pos):
    try:
        return len(pos) >= 2 and all(math.isfinite(float(v)) and abs(v) < 100000 for v in pos[:2])
    except (TypeError, ValueError, OverflowError):
        return False


PORT = 27971
ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "dota_icons", "abilities")
ABILITY_ICON_URL = (
    "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/abilities/%s.png"
)


class IconDL:
    """Фоново качает PNG иконок способностей в dota_icons/abilities/."""

    def __init__(self):
        os.makedirs(ICON_DIR, exist_ok=True)
        self._pending = set()
        self._failed = set()
        self._lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True).start()

    def want(self, name):
        if not re.fullmatch(r'[a-z0-9_]{1,100}', name) or name in self._failed:
            return
        path = os.path.join(ICON_DIR, name + ".png")
        if os.path.exists(path):
            return
        with self._lock:
            self._pending.add(name)

    def _loop(self):
        while True:
            with self._lock:
                name = next(iter(self._pending), None)
                if name is not None:
                    self._pending.discard(name)
            if name is None:
                time.sleep(0.2)
                continue
            path = os.path.join(ICON_DIR, name + ".png")
            if not os.path.exists(path):
                try:
                    if name.startswith('npc_dota_hero_'):
                        url = 'https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/%s.png' % name.removeprefix('npc_dota_hero_')
                    elif name.startswith('item_'):
                        url = 'https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/items/%s.png' % name.removeprefix('item_')
                    else:
                        url = ABILITY_ICON_URL % name
                    req = urllib.request.Request(
                        url,
                        headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=10) as r:
                        data = r.read()
                    if len(data) > 100:
                        temporary = path + '.part'
                        with open(temporary, "wb") as f:
                            f.write(data)
                        os.replace(temporary, path)
                except Exception:
                    self._failed.add(name)
            time.sleep(0.03)


class Feed:
    def __init__(self, dot, backend=None, preview=False):
        self.d = dot
        # Full entity discovery can take about a second on a live match. Give it
        # an independent read-only Dot state so it cannot stall the 90 Hz reader.
        try:
            self._snapshot_dot = Dot()
        except Exception as exc:
            # A shallow Dot clone keeps separate Python caches while sharing the
            # already-open read-only process handle as a safe fallback.
            self._snapshot_dot = copy.copy(dot)
            self._snapshot_dot._names = dict(getattr(dot, '_names', {}))
            self._snapshot_dot._force_log = []
            print('[feed] snapshot reader fallback to shared process handle: ' + str(exc), flush=True)
        self._snapshot_worker = AsyncSnapshotWorker(
            lambda: self._snapshot_dot.snapshot(full_scan=False))
        self._snapshot_error_key = ''
        self._snapshot_error_log_at = 0.0
        self.ks = KillSteal(os.path.dirname(os.path.abspath(__file__)), dota_esp, backend, preview=preview)
        if not self.ks.error:
            if self.ks.settings.auto_cast:
                print('[DODGER MODE] AUTO: ввод разрешён только после кнопки на странице Dodger. Начальное состояние OFF.',flush=True)
            else:
                reason='PREVIEW.bat / --preview' if preview else 'ks_profiles.json: settings.auto_cast=false'
                print('[DODGER MODE] PREVIEW — НАЖАТИЙ НЕ БУДЕТ. Причина: '+reason,flush=True)
        if self.ks.last_hit:self.ks.last_hit.attach(dot)
        self.camera_service=CameraService(os.path.dirname(os.path.abspath(__file__)),self.ks.ui_session,preview=preview)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addr = ("127.0.0.1", PORT)
        self.icons = IconDL()
        self._lock = threading.Lock()
        self._heroes = []
        self._my_team = None
        self._curtime = 0.0
        self._updated_at = 0.0
        self._reader_error_key = ''
        self._reader_error_log_at = 0.0
        self._reader_perf_log_at = 0.0
        self._sender_perf_log_at = 0.0
        self._running = True

    def _inspection_data(self, heroes):
        # Called in the reader thread, never from UDP sender.
        for h in heroes:
            if not h.get("is_local"):
                continue
            radius = 0.0
            if h.get("ent"):
                try:
                    raw = self.d.f32(h["ent"] + RANGE_DISPLAY_OFFSET)
                    if math.isfinite(raw) and 64 <= raw <= 25000:
                        radius = raw
                except Exception:
                    pass
            h["inspection_display_range"] = radius
        return self.ks.capture(self.d, heroes)

    # ---------- reader (fast path каждый тик; полный поиск отдельно) ----------
    def _reader_loop(self):
        last_full = 0.0
        full_pending = True
        while self._running:
            t0 = time.perf_counter()
            fast_ms = snapshot_ms = 0.0
            ran_snapshot = False
            try:
                self.d.screen = self.d._screen_size()
                with self._lock:
                    base = copy.deepcopy(self._heroes)
                    my_team = self._my_team
                if base:
                    phase = time.perf_counter()
                    heroes, _mp = self.d.overlay_fast_update(base, None, my_team)
                    heroes = self._inspection_data(heroes)
                    current_time = self.d.curtime()
                    fast_ms = (time.perf_counter() - phase) * 1000
                    with self._lock:
                        self._heroes = heroes
                        self._curtime = current_time
                        self._updated_at = time.perf_counter()

                # Consume a completed entity scan without making the live reader
                # wait for it. The scan can take >1 s on a full 10-player match.
                completed = self._snapshot_worker.poll()
                if completed is not None:
                    s, snapshot_error, snapshot_ms = completed
                    ran_snapshot = True
                    last_full = time.perf_counter()
                    if snapshot_error:
                        now = time.perf_counter()
                        if (snapshot_error != self._snapshot_error_key or
                                now - self._snapshot_error_log_at >= 5.0):
                            print('[feed] snapshot err: ' + snapshot_error, flush=True)
                            self._snapshot_error_key = snapshot_error
                            self._snapshot_error_log_at = now
                    else:
                        self._snapshot_error_key = ''
                        self._snapshot_error_log_at = 0.0
                    if s:
                        heroes = [dict(h) for h in s["heroes"]
                                  if not h.get("illusion")]
                        # Snapshot addresses may have been recycled while the
                        # background walk was running. Keep only identities that
                        # still match the current entity table.
                        current_heroes = []
                        for h in heroes:
                            idx, entity = h.get("idx"), h.get("ent")
                            if not idx or not entity:
                                current_heroes.append(h)
                                continue
                            current = self.d.get_entity(idx)
                            if current and current[0] == entity:
                                current_heroes.append(h)
                        my_team = (s.get("local") or {}).get("team")
                        heroes, _mp = self.d.overlay_fast_update(
                            current_heroes, s.get("my_pos"), my_team)
                        heroes = self._inspection_data(heroes)
                        with self._lock:
                            self._heroes = heroes
                            self._my_team = my_team
                            self._curtime = s.get("curtime") or 0.0
                            self._updated_at = time.perf_counter()
                        for h in heroes:
                            for ab in (h.get("abilities") or []):
                                nm = (ab.get("name") or "").lower()
                                if nm:
                                    self.icons.want(nm)

                if (snapshot_due(t0, last_full, full_pending, bool(base)) and
                        not self._snapshot_worker.in_flight):
                    if self._snapshot_worker.request():
                        # Throttle both empty-roster and failed snapshots. A
                        # request is asynchronous, so the live reader continues.
                        full_pending = False
                        last_full = time.perf_counter()
            except Exception as e:
                key = type(e).__name__ + ': ' + str(e)
                now = time.perf_counter()
                if key != self._reader_error_key or now - self._reader_error_log_at >= 5.0:
                    print(f"[feed] reader err: {key}")
                    if key != self._reader_error_key:
                        import traceback
                        traceback.print_exc()
                    self._reader_error_key = key
                    self._reader_error_log_at = now
                elapsed = time.perf_counter() - t0
                if elapsed >= 0.08 and now - self._reader_perf_log_at >= 0.5:
                    self._reader_perf_log_at = now
                    print(f"[perf] reader cycle={elapsed*1000:.1f}ms fast={fast_ms:.1f}ms "
                          f"snapshot={snapshot_ms:.1f}ms full={int(ran_snapshot)} error=1", flush=True)
                time.sleep(0.05)
                continue
            self._reader_error_key = ''
            self._reader_error_log_at = 0.0
            dt = time.perf_counter() - t0
            if dt >= 0.08 or ran_snapshot:
                now = time.perf_counter()
                if now - self._reader_perf_log_at >= 0.5:
                    self._reader_perf_log_at = now
                    with self._lock:
                        hero_count = len(self._heroes)
                    print(f"[perf] reader cycle={dt*1000:.1f}ms fast={fast_ms:.1f}ms "
                          f"snapshot={snapshot_ms:.1f}ms full={int(ran_snapshot)} "
                          f"heroes={hero_count} async=1", flush=True)
            time.sleep(max(0.001, 1.0 / 90.0 - dt) if dt < 0.05 else 0.002)

    # ---------- sender ----------
    def _send_loop(self):
        last_diag = 0.0
        ui_at, ui_line = 0.0, ''
        while self._running:
            t0 = time.perf_counter()
            hero_count = visible_count = 0
            reader_age = 99999.0
            try:
                with self._lock:
                    heroes = list(self._heroes)
                    my_team = self._my_team
                    ct = self._curtime
                    updated_at = self._updated_at
                hero_count = len(heroes)
                visible_count = sum(1 for h in heroes if not h.get("is_local") and overlay_hero_visible(h))
                # Keep heartbeats flowing even while snapshot() is slow.
                # Send the age separately: only cursor automation depends on freshness.
                reader_age = max(0.0, time.perf_counter() - updated_at) if updated_at else 99999.0
                self.camera_service.pulse()
                self.ks.tick(heroes, reader_age)
                local = next((h for h in heroes if h.get("is_local")), {})
                display_range = local.get("inspection_display_range", 0.0)
                lines = [f"ESP1\t{ct:.1f}\t{my_team if my_team else 0}\t{display_range:.1f}\t{reader_age:.3f}" + self.ks.wire_fields()]
                for h in heroes:
                    if not overlay_hero_visible(h):
                        continue
                    is_local = bool(h.get("is_local"))
                    sc = h.get("screen")
                    if not sc:
                        if not is_local:
                            continue
                        sc = (-100000.0, -100000.0)  # keep local world position/abilities off-screen
                    pos = h.get("pos")
                    has_world = valid_world_position(pos)
                    wx, wy = (float(pos[0]), float(pos[1])) if has_world else (0.0, 0.0)
                    name = (h.get("designer") or "?").replace("\t", " ").replace("\n", " ")
                    tp = 1 if (h.get("tp") or {}).get("ready") else 0
                    abl = []
                    for ab in (h.get("abilities") or [])[:6]:
                        an = (ab.get("name") or "").lower()
                        if an:
                            abl.append(f"{an}:{ab.get('cd') or 0:.1f}:{ab.get('cd_len') or 0:.1f}:{ab.get('level') or 0}:{1 if ab.get('cast') else 0}")
                    lines.append("\t".join((
                        str(h.get("idx") or 0),
                        f"{sc[0]:.1f}", f"{sc[1]:.1f}",
                        str(h.get("team") or 0),
                        "1" if h.get("is_local") else "0",
                        "1" if h.get("alive") else "0",
                        str(int(h.get("hp") or 0)), str(int(h.get("maxhp") or 0)),
                        f"{h.get('mana') or 0:.0f}", f"{h.get('maxmana') or 0:.0f}",
                        str(h.get("level") or 0),
                        f"{h.get('respawn') or 0:.1f}",
                        name, str(tp), ";".join(abl),
                        f"{h.get('mr') or 0:.3f}",
                        ";".join(h.get("items") or []),
                        f"{wx:.3f}", f"{wy:.3f}", "1" if has_world else "0",
                    )))
                if t0 - ui_at >= .1:
                    ui_at = t0
                    ui_line = attach_state(wire_dashboard(self.ks, heroes, reader_age),self.camera_service.snapshot())
                    for hero in heroes:
                        if hero.get('is_local'):
                            self.icons.want(hero.get('designer') or '')
                            for ab in hero.get('abilities') or []:
                                self.icons.want(ab.get('name') or '')
                            inventory = hero.get('_ks_inventory')
                            if inventory is not None and inventory.known:
                                for _, _, _, name in inventory.entries:
                                    self.icons.want(name)
                if ui_line:
                    lines.append(ui_line)
                pkt = "\n".join(lines).encode("utf-8", "ignore")
                if len(pkt) < 65000:
                    self.sock.sendto(pkt, self.addr)
                    if t0 - last_diag >= 3.0:
                        last_diag = t0
                        screen_count = sum(1 for h in heroes if h.get("screen"))
                        print(f"[feed] tx heroes={len(lines)-1}/{len(heroes)} "
                              f"screen={screen_count} reader_age={reader_age:.3f}s "
                              f"bytes={len(pkt)}", flush=True)
            except Exception as e:
                print(f"[feed] send err: {e}")
            spend = time.perf_counter() - t0
            if spend >= 0.05:
                now = time.perf_counter()
                if now - self._sender_perf_log_at >= 0.5:
                    self._sender_perf_log_at = now
                    print(f"[perf] sender cycle={spend*1000:.1f}ms heroes={hero_count} "
                          f"visible_enemies={visible_count} reader_age={reader_age:.3f}s "
                          f"ks_on={int(self.ks.toggle.enabled)}", flush=True)
            time.sleep(max(0.001, 1.0 / 60.0 - spend))

    def run(self):
        print(f"[feed] udp -> 127.0.0.1:{PORT} | Ctrl+C to stop")
        self._snapshot_worker.start()
        threading.Thread(target=self._reader_loop, name="esp-fast-reader", daemon=True).start()

        def _diag():
            time.sleep(6)
            from dota_esp import ptr_ok, handle_idx
            with self._lock:
                loc = next((h for h in self._heroes
                            if h.get("is_local") and h.get("ent")), None)
            if not loc:
                print("[feed] diag: нет локального героя")
                return
            ep = loc["ent"]
            print(f"[feed] diag hero={loc.get('designer')} ep={hex(ep)}")
            for off in range(0xC00, 0xC80, 16):
                raw = self.d.rb(ep + off, 16)
                if raw:
                    print(f"[feed]   +{off:03X} " + raw.hex(" "))
            print("[feed] scan CUtlVector-ов рядом:")
            for off in range(0xB80, 0xE00, 8):
                ptr = self.d.u64(ep + off)
                size = self.d.i32(ep + off + 8)
                if not (ptr_ok(ptr) and 1 <= size <= 16):
                    continue
                hs = self.d._read_handle_array(ptr, size)
                names = []
                for hh in hs[:8]:
                    idx = handle_idx(hh)
                    ent = self.d.get_entity(idx) if idx else None
                    names.append(ent[1] if ent else f"#{idx}")
                print(f"[feed]   off={hex(off)} size={size} -> {names}")

        def _watch_overlay():
            """фид сам гаснет, когда закрыт esp_overlay.exe"""
            import subprocess
            seen = False
            while self._running:
                time.sleep(1)
                try:
                    out = subprocess.check_output(
                        ["tasklist", "/FI", "IMAGENAME eq esp_overlay.exe", "/NH"],
                        creationflags=0x08000000,
                        stderr=subprocess.DEVNULL).decode("cp1251", "ignore")
                    present = "esp_overlay.exe" in out
                except Exception:
                    present = True
                if present:
                    seen = True
                elif seen:
                    print("[feed] оверлей закрыт -> выход")
                    self._running = False
                    self.camera_service.quit.set()
                    return

        # _diag is retained for manual debugging, not run concurrently with reader.
        threading.Thread(target=_watch_overlay, daemon=True).start()
        try:
            self.camera_service.start()
            self._send_loop()
        except KeyboardInterrupt:
            self._running = False
            self._snapshot_worker.stop()
            print("\n[feed] stopped")
        finally:
            self._running = False
            self.camera_service.close()
            self._snapshot_worker.stop()


def main():
    # In classic Windows consoles Quick Edit pauses the process while text is
    # selected. The sender runs in this process, so a console selection would
    # stop UDP heartbeats and make the WPF overlay clear its HUD as stale.
    if disable_console_quick_edit():
        print("[feed] Quick Edit отключён: выделение консоли не остановит фид", flush=True)
    from pathlib import Path
    if Path(__file__).with_name(".patch_pending").exists():
        raise RuntimeError("Incomplete patch transaction: restore the backup with patch_update.py --rollback")
    preview = '--preview' in sys.argv
    print("KS5 HACHBOB: Insert = меню; Killsteal управляется кнопкой в программе; F6 и Esc его не выключают. End = выход. Quickcast НА НАЖАТИЕ.")
    print("Профили/клавиши: ks_profiles.json. После запуска OFF. Режим: " + ('PREVIEW' if preview else 'из JSON'))
    print("Новые таблицы урона — примеры; сверяй со своим патчем. Чат/меню не распознаются.")
    from ks_input import WinInput
    input_backend = WinInput()  # Set DPI awareness BEFORE Dot captures screen geometry.
    d = Dot()
    for a in sys.argv:
        if a.startswith("--aboff="):
            try:
                d._ab_off = int(a.split("=", 1)[1], 16)
            except Exception:
                pass
    Feed(d, backend=input_backend, preview=preview).run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[feed] stopped")
    except Exception:
        import traceback
        print("[feed] CRASH:")
        traceback.print_exc()
        input("Нажми Enter, чтобы закрыть...")
