"""One writer, explicit enable, conditional restoration; no constant overwrite loop.
This Source Code Form is subject to the MPL-2.0 in LICENSE-MPL-2.0.txt.
"""
import math
import struct


def target_bytes(value):
    if type(value) not in (int,float) or not math.isfinite(value) or not 1000<=value<=2200:
        raise ValueError('Дистанция должна быть от 1000 до 2200')
    return struct.pack('<f',value)


def decode(raw,allow_automatic=False):
    value=struct.unpack('<f',raw)[0]
    if allow_automatic and value==-1:return value
    if not math.isfinite(value) or not 600<=value<=2600:raise ValueError('Поле больше не похоже на дистанцию; запись остановлена')
    return value


class Control:
    def __init__(self,mem,camera,cancel=lambda:False):
        self.mem=mem;self.camera=camera;self.cancel=cancel;self.active=False;self.fault=False
        self.original=None;self.expected=None;self.attempted=None
    def arm(self,target):
        target_bytes(target)
        if self.active or self.original is not None or self.fault:raise ValueError('Сначала выключи зум / найди камеру заново')
        self.camera.validate(self.mem)
        original=self.mem.read(self.camera.address,4);decode(original,getattr(self.camera,'allow_automatic',False))
        self.original=self.expected=original;self.active=True
        try:return self.apply(target)
        except Exception:
            self.active=False;self.fault=True
            raise
    def apply(self,target):
        raw=target_bytes(target)
        if not self.active:raise ValueError('Зум не включён')
        try:
            self.camera.validate(self.mem)
            current=self.mem.read(self.camera.address,4)
            if current!=self.expected:raise ValueError('Игра или другая программа изменила поле; не перезаписываем')
            if current==raw:return decode(raw)
            # Recheck after reads, immediately before the only write.
            self.camera.validate(self.mem)
            if self.mem.read(self.camera.address,4)!=current:raise ValueError('Поле изменилось перед записью')
            if self.cancel():raise InterruptedError('Включение/изменение отменено')
            self.attempted=raw
            self.mem.write(self.camera.address,raw)
            self.camera.validate(self.mem)
            if self.mem.read(self.camera.address,4)!=raw:raise ValueError('Запись не подтвердилась; повтор запрещён')
            self.expected=raw;self.attempted=None
            return decode(raw)
        except Exception:
            self.active=False;self.fault=True
            raise
    def poll(self):
        self.camera.validate(self.mem)
        raw=self.mem.read(self.camera.address,4)
        if self.active and raw!=self.expected:
            self.active=False;self.fault=True
            raise ValueError('Игра изменила дистанцию. Зум отключён, постоянного перезаписывания нет')
        return decode(raw,getattr(self.camera,'allow_automatic',False))
    def stop(self):
        self.active=False
        if self.original is None:return 'Зум выключен; записей нет'
        if self.expected==self.original and self.attempted is None:
            self.original=self.expected=None
            return 'Зум выключен; возврат не требуется'
        try:
            self.camera.validate(self.mem)
            current=self.mem.read(self.camera.address,4)
            if current==self.original:return 'Исходное значение уже установлено'
            if current not in (self.expected,self.attempted):
                raise ValueError('Возврат пропущен: поле изменено не нами')
            self.camera.validate(self.mem)
            if self.mem.read(self.camera.address,4)!=current:raise ValueError('Возврат пропущен: поле снова изменилось')
            self.mem.write(self.camera.address,self.original)
            if self.mem.read(self.camera.address,4)!=self.original:raise ValueError('Возврат исходного значения не подтвердился')
            return 'Автоматическая дистанция игры восстановлена' if self.original==struct.pack('<f',-1) else 'Исходная дистанция восстановлена'
        except Exception:
            self.fault=True
            raise
        finally:
            # Never replay a saved value into a new camera/process on reconnect.
            self.original=self.expected=self.attempted=None
