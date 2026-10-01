"""exp6: verified GetZFar override data path; no GameMode discovery or code writes.
MPL-2.0. The exact method was supplied in the user's exp3 read-only report.
"""
from dataclasses import dataclass
import hashlib
import math
import struct
from . import scene
from .locator import read_image,u64,pointer


@dataclass(frozen=True)
class Layout:
    selector_ref:int
    gate_ref:int
    split_base:int
    guard:int
    cache:int


def layout_from_code(code,address):
    def rip(offset,prefix,length):
        if code[offset:offset+len(prefix)]!=prefix:raise ValueError('Не совпала инструкция выбора FarZ')
        return address+offset+length+struct.unpack_from('<i',code,offset+len(prefix))[0]
    result=Layout(rip(0x06,b'\x48\x8b\x05',7),rip(0x23,b'\x48\x8b\x05',7),
                  rip(0x3e,b'\x48\x8d\x0d',7),rip(0x68,b'\x39\x05',6),rip(0x74,b'\x48\x8d\x05',7))
    if result.guard!=result.cache+4:raise ValueError('Не совпала структура статического FarZ')
    return result


class DirectFarZ:
    def __init__(self,context,cancel=lambda:False):
        self.context=context;self.mem=context.mem;self.image=context.image;self.cancel=cancel
        self.active=False;self.original=None;self.expected=None;self.attempted=False
        self.layout=None;self.field=None;self.guard_value=None;self.selection=None;self.path=None
    def code_layout(self):
        # For restoration validate module/static storage, not an obsolete camera
        # or entity. These fields have module lifetime, unlike GameMode objects.
        if not self.mem.alive() or read_image(self.mem,self.image.base,self.image.size)!=self.image:
            raise ValueError('Модуль сменился; старое значение FarZ не записывается')
        address=self.image.base+scene.FARZ_RVA;s=self.image.section(address,scene.FARZ_BYTES)
        if not s or not s.executable:raise ValueError('Метод FarZ вне кода client.dll')
        code=self.mem.read(address,scene.FARZ_BYTES)
        if hashlib.sha256(code).hexdigest()!=scene.FARZ_SHA256:raise ValueError('Код FarZ отличается от проверенного')
        layout=layout_from_code(code,address)
        for a,n in ((layout.selector_ref,8),(layout.gate_ref,8),(layout.guard,4),(layout.cache,4),(layout.split_base,48)):
            s=self.image.section(a,n)
            if not s or s.executable:raise ValueError('Данные выбора FarZ вне секций данных client.dll')
        return layout
    def select(self,layout):
        # Mirror the observed instructions; no arbitrary cvar/value search.
        p=u64(self.mem,layout.selector_ref);q=u64(self.mem,layout.gate_ref)
        if not pointer(p) or not pointer(q):raise ValueError('Данные выбора камеры ещё не готовы')
        selector=struct.unpack('<i',self.mem.read(p+0x58,4))[0]
        gate=self.mem.read(q+0x58,1)[0]
        if not -1<=selector<=31 or gate not in (0,1):raise ValueError('Неизвестный режим выбора FarZ')
        field=layout.split_base+24*selector if gate and 0<selector<2 else layout.cache
        return field,(p,q,selector,gate)
    def storage(self):
        if self.code_layout()!=self.layout:raise ValueError('Расположение данных FarZ изменилось')
        s=self.image.section(self.field,4)
        if not s or s.executable or not s.writable or not self.mem.writable(self.field,4):
            raise ValueError('Активный FarZ не в RW-данных; защиту страниц не меняем')
        if self.field==self.layout.cache:
            guard=struct.unpack('<i',self.mem.read(self.layout.guard,4))[0]
            # MSVC static-local guard: 0=not initialized, -1=initializing,
            # completed initialization records a negative epoch below -1.
            if guard>=-1 or guard!=self.guard_value:raise ValueError('Инициализация FarZ не завершена или изменилась')
    def live(self):
        self.context.validate_farz();self.storage()
        field,selection=self.select(self.layout)
        if field!=self.field or selection!=self.selection:raise ValueError('Игра сменила источник FarZ; режим остановлен')
    def describe(self):
        return {'path':self.path,'field_rva':hex(self.field-self.image.base) if self.field else None,
                'original_hex':self.original.hex() if self.original is not None else None,
                'target_hex':self.expected.hex() if self.expected is not None else None,
                'guard':self.guard_value,'selector':self.selection[2:] if self.selection else None,
                'write_attempted':self.attempted}
    def start(self):
        if self.active or self.original is not None:raise ValueError('Сначала выключи FarZ')
        self.context.validate_farz();self.layout=self.code_layout()
        self.field,self.selection=self.select(self.layout)
        self.path='initialized default slot' if self.field==self.layout.cache else 'split-screen slot'
        self.guard_value=struct.unpack('<i',self.mem.read(self.layout.guard,4))[0] if self.field==self.layout.cache else None
        self.storage()
        old=self.mem.read(self.field,4);value=struct.unpack('<f',old)[0]
        if not math.isfinite(value) or not (value==-1 or 32<=value<=65536):
            raise ValueError('Неизвестное исходное значение FarZ: %g'%value)
        target=struct.pack('<f',max(18000.,value));self.original=old;self.expected=target
        try:
            self.live()
            if self.mem.read(self.field,4)!=old:raise ValueError('FarZ изменился перед записью')
            if self.cancel():raise InterruptedError('Включение FarZ отменено')
            if old!=target:
                self.attempted=True;self.mem.write(self.field,target)
                self.live()
                if self.mem.read(self.field,4)!=target:raise ValueError('Запись FarZ не подтвердилась')
            self.active=True
            return int(self.attempted)
        except Exception as exc:
            try:self.stop()
            except Exception as restore:raise scene.RestoreError(str(exc)+'; возврат FarZ: '+str(restore)) from exc
            raise
    def poll(self):
        self.live()
        if self.original is None or self.mem.read(self.field,4)!=self.expected:
            raise ValueError('Игра изменила FarZ; постоянного перезаписывания нет')
    def stop(self):
        self.active=False
        try:
            if self.original is None or not self.attempted:return 'FarZ выключен; своих записей нет'
            self.storage();current=self.mem.read(self.field,4)
            if current==self.original:return 'Исходный FarZ уже установлен'
            if current!=self.expected:raise ValueError('Чужое изменение FarZ не затирается')
            self.storage()
            if self.mem.read(self.field,4)!=current:raise ValueError('FarZ снова изменился перед возвратом')
            self.mem.write(self.field,self.original);self.storage()
            if self.mem.read(self.field,4)!=self.original:raise ValueError('Возврат FarZ не подтвердился')
            return 'Исходный режим FarZ восстановлен'
        except Exception as exc:raise scene.RestoreError(str(exc)) from exc
        finally:
            self.original=self.expected=None;self.attempted=False
