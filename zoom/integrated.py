"""Native-menu adapter for the accepted Zoom/Scene controls. No game input.
MPL-2.0. All process IO stays on this dedicated memory worker, not the KS sender.
"""
import ctypes as C
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import threading
import time
from .control import Control
from .farz import DirectFarZ
from .scene import Context,Group
from .verified import locate_verified
from .win32 import API,Memory,instance_lock


KEYS={'version','session','revision','utc_ms','distance','haze'}


def validate(doc,session,now_ms):
    return bool(isinstance(doc,dict) and set(doc)==KEYS and type(doc['version']) is int and doc['version']==1 and
                doc['session']==session and type(session) is str and len(session)==32 and
                type(doc['revision']) is int and 0<=doc['revision']<=2147483647 and
                type(doc['utc_ms']) is int and -500<=now_ms-doc['utc_ms']<=2000 and
                type(doc['distance']) is int and (doc['distance']==0 or 1000<=doc['distance']<=2200 and doc['distance']%25==0) and
                type(doc['haze']) is bool)


def attach_state(line,state):
    # Leave all existing KS/LH telemetry untouched; add only a new DTO member.
    if not line.startswith('UI1\t{'):return line
    return 'UI1\t{"zoom":'+json.dumps(state,ensure_ascii=False,separators=(',',':'),allow_nan=False)+','+line[5:]


def scene_lock():
    # Same mutex as accepted standalone Scene, without importing its Tk UI.
    k=C.WinDLL('kernel32',use_last_error=True)
    k.CreateMutexW.argtypes=[C.c_void_p,C.c_int,C.c_wchar_p];k.CreateMutexW.restype=C.c_void_p
    k.CloseHandle.argtypes=[C.c_void_p];k.CloseHandle.restype=C.c_int
    handle=k.CreateMutexW(None,False,'Local\\HACHBOB_Scene_Controls');error=C.get_last_error()
    if not handle:raise OSError('Не удалось заблокировать Scene')
    if error==183:k.CloseHandle(handle);raise OSError('Закрой отдельное окно SCENE перед включением камеры в HACHBOB')
    return lambda:k.CloseHandle(handle)


class NativeEngine:
    def __init__(self):
        self.api=None;self.mem=None;self.camera=None;self.context=None
        self.zoom=None;self.farz=None;self.haze=None;self.releases=[];self.distance=0;self.stage=''
    def end_pressed(self):return bool(self.api and self.api.end_pressed())
    def connect(self,cancel):
        if self.mem:return
        try:
            self.stage='Подключение · проверяю другие окна Zoom / Scene'
            try:self.releases.append(instance_lock())
            except OSError as exc:
                if 'уже запущено' in str(exc):raise OSError('Закрой старое окно ZOOM или второй HACHBOB и повтори настройку') from exc
                raise
            self.releases.append(scene_lock())
            self.api=API();pid=self.api.dota_pid();self.mem=Memory(self.api,pid)
            module=self.api.module(pid,'client.dll')
            self.stage='Проверяю версию игры и камеру…'
            self.camera=locate_verified(self.mem,module.base,module.size,cancel)
            self.context=Context(self.mem,self.camera)
        except Exception:
            self.stop_all();raise
    def disable_zoom(self):
        errors=[]
        # Return camera override first; restore module-lifetime FarZ even when
        # the old camera is no longer valid. Each accepted controller decides.
        for name in ('zoom','farz'):
            control=getattr(self,name)
            if control:
                try:control.stop()
                except Exception as exc:errors.append(str(exc))
            setattr(self,name,None)
        self.distance=0
        return errors
    def apply(self,distance,haze,cancel):
        if not distance and not haze:
            errors=self.stop_all()
            if errors:raise ValueError('Возврат не подтверждён: '+'; '.join(errors))
            return
        self.connect(cancel)
        if cancel():raise InterruptedError('Запрос камеры отменён')
        if distance:
            if self.zoom is None:
                self.stage='Подключаю Zoom + ZFar…'
                self.farz=DirectFarZ(self.context,cancel);self.farz.start()
                self.zoom=Control(self.mem,self.camera,cancel);self.zoom.arm(distance)
            elif distance!=self.distance:
                self.zoom.cancel=cancel;self.zoom.apply(distance)
            self.distance=distance
        else:
            errors=self.disable_zoom()
            if errors:raise ValueError('Возврат не подтверждён: '+'; '.join(errors))
        if cancel():raise InterruptedError('Запрос камеры отменён')
        if haze and self.haze is None:
            self.stage='Ищу параметры атмосферной дымки…'
            self.haze=Group(self.context,'haze',cancel);self.haze.start()
        elif not haze and self.haze is not None:
            old=self.haze;self.haze=None
            try:old.stop()
            except Exception as exc:raise ValueError('Возврат дымки не подтверждён: '+str(exc)) from exc
    def poll(self):
        for c in (self.zoom,self.farz,self.haze):
            if c:c.poll()
    def stop_all(self):
        errors=self.disable_zoom()
        if self.haze:
            try:self.haze.stop()
            except Exception as exc:errors.append(str(exc))
        self.haze=None
        if self.mem:
            try:self.mem.close()
            except Exception as exc:errors.append(str(exc))
        self.mem=None;self.camera=None;self.context=None
        for release in reversed(self.releases):
            try:release()
            except Exception as exc:errors.append(str(exc))
        self.releases=[]
        return errors


class CameraService:
    def __init__(self,root,session,preview=False,engine=None):
        self.root=Path(root);self.session=session;self.preview=preview
        self.path=self.root/'zoom_controls.json';self.engine=engine or NativeEngine()
        self.quit=threading.Event();self.done=threading.Event();self.thread=None
        self.lock=threading.Lock();self.host_at=time.monotonic();self.revision=-1;self.last_payload=None;self.lease=0
        self.cached_command=None;self.cached_at=0;self.cancel_detail=''
        self.state={'revision':-1,'distance':0,'haze':False,'farz':False,'busy':False,'available':True,
                    'status':'Авто · камера игры','error':'','restore_error':'','preview':bool(preview)}
        self.log=logging.getLogger('native-zoom.'+session);self.log.setLevel(logging.INFO);self.log.propagate=False
        try:
            handler=RotatingFileHandler(self.root/'zoom'/'integrated.log',maxBytes=128*1024,backupCount=1,encoding='utf-8')
            handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'));self.log.addHandler(handler)
        except OSError:self.log.addHandler(logging.NullHandler())
    def publish(self,**changes):
        with self.lock:self.state.update(changes)
    def snapshot(self):
        with self.lock:state=dict(self.state)
        if state['busy']:state['status']=getattr(self.engine,'stage','') or 'Применяю настройки…'
        return state
    def pulse(self):self.host_at=time.monotonic()
    def read_command(self):
        now_ms=int(time.time()*1000)
        try:
            if self.path.stat().st_size>2048:
                self.cached_command=None;return None
            doc=json.loads(self.path.read_text(encoding='utf-8-sig'))
            if not validate(doc,self.session,now_ms):
                self.cached_command=None;return None
            self.cached_command=dict(doc);self.cached_at=time.monotonic()
            return doc
        except OSError as exc:
            # File.Replace/read sharing can briefly overlap on Windows. Use only
            # a previously validated heartbeat for at most 250 ms, never beyond
            # its original wall-clock expiry. Missing/invalid files get no grace.
            transient=isinstance(exc,PermissionError) or getattr(exc,'winerror',0) in (5,32,33)
            if transient and self.cached_command is not None and 0<=time.monotonic()-self.cached_at<=.25 and validate(self.cached_command,self.session,now_ms):
                return dict(self.cached_command)
            self.cached_command=None;return None
        except (ValueError,TypeError):
            self.cached_command=None;return None
    def cancelled(self,revision):
        reason=''
        if self.quit.is_set():reason='HACHBOB завершает работу'
        elif self.engine.end_pressed():reason='Нажато End'
        elif time.monotonic()-self.host_at>2.5:reason='Нет связи с feed более 2.5 сек'
        else:
            doc=self.read_command()
            if doc is None:reason='Нет свежей команды от меню'
            elif doc['revision']!=revision:reason='Получена другая команда от меню'
            elif (doc['distance'],doc['haze'])!=self.last_payload:reason='Изменено содержимое той же команды'
        if reason:self.cancel_detail=reason
        return bool(reason)
    def off(self,status,error=''):
        errors=self.engine.stop_all()
        if error:print('[Камера] '+status+': '+str(error)[:500],flush=True)
        restore='; '.join(errors)[:500]
        if restore:
            self.log.error('RESTORE NOT CONFIRMED %s',restore)
            print('[Камера] Возврат не подтверждён: '+restore,flush=True)
        self.publish(distance=0,haze=False,farz=False,busy=False,status=status,error=str(error)[:500],
                     restore_error=restore or self.snapshot()['restore_error'])
    def process(self,doc,now=None):
        now=time.monotonic() if now is None else now
        if doc is None or doc['revision']<self.revision:return
        payload=(doc['distance'],doc['haze'])
        if doc['revision']==self.revision:
            # Heartbeats extend ownership but never replay a failed/revoked arm.
            if payload==self.last_payload:
                self.lease=now+min(2.,max(0,(doc['utc_ms']+2000-int(time.time()*1000))/1000))
            return
        self.revision=doc['revision'];self.last_payload=payload
        self.lease=now+min(2.,max(0,(doc['utc_ms']+2000-int(time.time()*1000))/1000))
        self.cancel_detail=''
        self.log.info('REQUEST revision=%s distance=%s haze=%s',self.revision,*payload)
        self.publish(revision=self.revision,busy=True,error='')
        if self.preview:
            self.off('PREVIEW · запись камеры отключена');return
        try:
            self.engine.apply(*payload,cancel=lambda:self.cancelled(doc['revision']))
            if self.cancelled(doc['revision']):raise InterruptedError('Запрос камеры отменён')
            # Discovery can take longer than a lease. Its cancellation callback
            # checks live UI heartbeats; carry the latest one into monitoring.
            latest=self.read_command()
            if latest is None or latest['revision']!=self.revision:raise InterruptedError('Нет свежей команды')
            self.lease=time.monotonic()+min(2.,max(0,(latest['utc_ms']+2000-int(time.time()*1000))/1000))
            self.publish(distance=doc['distance'],haze=doc['haze'],farz=bool(doc['distance']),busy=False,
                         status=('Зум %s · ZFar автоматически'%doc['distance'] if doc['distance'] else 'Авто · камера игры')+(' · дымка отключена' if doc['haze'] else ''))
            self.log.info('APPLIED revision=%s distance=%s haze=%s',self.revision,*payload)
        except InterruptedError as exc:
            reason=self.cancel_detail or str(exc) or 'Операция отменена'
            self.log.warning('CANCEL revision=%s: %s',self.revision,reason)
            self.off('Камера не применена',reason)
        except Exception as exc:
            self.log.exception('CAMERA OFF');self.off('Камера отключена',exc)
            # Controllers may already have attempted rollback before raising.
            if 'возврат' in str(exc).lower() or 'восстанов' in str(exc).lower():self.publish(restore_error=str(exc)[:500])
    def step(self):
        if self.engine.end_pressed():self.quit.set();return
        now=time.monotonic()
        if now-self.host_at>2.5:
            if self.snapshot()['distance'] or self.snapshot()['haze']:self.off('Нет связи с feed; камера возвращена')
            return
        self.process(self.read_command(),now)
        state=self.snapshot()
        if state['distance'] or state['haze']:
            if time.monotonic()>self.lease:
                self.off('Нет связи с меню; камера возвращена');return
            try:self.engine.poll()
            except Exception as exc:
                self.log.exception('CAMERA MONITOR');self.off('Камера отключена',exc)
    def run(self):
        failure=''
        try:
            while not self.quit.is_set():
                self.step();self.quit.wait(.05)
        except Exception as exc:
            self.log.exception('CAMERA SERVICE');failure=str(exc)[:500]
        finally:
            try:self.off('Сервис камеры остановлен · перезапусти HACHBOB' if failure else 'Камера выключена',failure)
            finally:self.publish(available=False);self.done.set()
    def start(self):
        if self.thread is None:
            print('[Camera native FIX1] Auto/OFF; log: zoom/integrated.log',flush=True)
            self.log.info('START native FIX1 session=%s',self.session)
            self.thread=threading.Thread(target=self.run,name='HACHBOB camera memory',daemon=False);self.thread.start()
    def close(self):
        self.quit.set()
        if self.thread:
            self.thread.join() # do not terminate the process before conditional restore
        else:self.off('Камера выключена');self.publish(available=False);self.done.set()
        for handler in list(self.log.handlers):handler.close();self.log.removeHandler(handler)
