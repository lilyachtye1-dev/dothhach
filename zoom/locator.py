"""Experimental external camera discovery. No writes, injections or convars.
Camera-manager signature/LEA + slot 0x20 and distance +0x2e4 adapted from
tranqu1lizer/external_patcher, core/dota_sdk.hpp, commit d565a98f6e9ca2856496a294c2b4b6e9e70cc413.
This file is subject to MPL-2.0 (LICENSE-MPL-2.0.txt).
Additional structural checks do NOT prove this old field's semantics on a new build.
"""
from dataclasses import dataclass
import math
import struct
import time

PATTERN=(0x48,0x89,0x74,0x24,0x40,0x48,0x89,0x7c,0x24,0x50,0x48,0x8d,None,None,None,None,None,0x48,0x8b,0x04,0xc8,0x8b,0x04,0x02,0x39)
DISTANCE_OFFSET=0x2e4  # SetDistance from the pinned source, NOT its stale GetDistance(+0x270).
CAMERA_TYPES={'.?AVCDOTA_Camera@@','.?AVCDOTACamera@@','.?AVCDotaCamera@@'}


def pointer(n):return type(n) is int and 0x10000<=n<0x0000800000000000 and n%8==0


def u64(mem,address):return struct.unpack('<Q',mem.read(address,8))[0]


def matches(data):
    start=0;prefix=bytes(PATTERN[:12]);hits=[]
    while True:
        at=data.find(prefix,start)
        if at<0:break
        start=at+1
        if at+len(PATTERN)<=len(data) and all(b is None or data[at+i]==b for i,b in enumerate(PATTERN)):
            hits.append(at)
    return hits


@dataclass(frozen=True)
class Section:
    start:int
    size:int
    flags:int
    def contains(self,a,n=1):return self.start<=a and a+n<=self.start+self.size
    @property
    def executable(self):return bool(self.flags&0x20000000)
    @property
    def writable(self):return bool(self.flags&0x80000000)


@dataclass(frozen=True)
class Image:
    base:int
    size:int
    timestamp:int
    sections:tuple
    def contains(self,a,n=1):return self.base<=a and a+n<=self.base+self.size
    def section(self,a,n=1):return next((s for s in self.sections if s.contains(a,n)),None)
    def executable(self,a):
        s=self.section(a);return bool(s and s.executable)


def read_image(mem,base,module_size):
    dos=mem.read(base,64)
    if dos[:2]!=b'MZ':raise ValueError('client.dll: неверный DOS header')
    pe=struct.unpack_from('<I',dos,60)[0]
    if not 64<=pe<=0x100000 or pe+24>module_size:raise ValueError('client.dll: неверный PE offset')
    header=mem.read(base+pe,24)
    if header[:4]!=b'PE\0\0':raise ValueError('client.dll: неверный PE header')
    machine,count,stamp=struct.unpack_from('<HHI',header,4)
    optional_size=struct.unpack_from('<H',header,20)[0]
    if machine!=0x8664 or not 1<=count<=96 or not 112<=optional_size<=4096:raise ValueError('Нужен x64 client.dll')
    optional=mem.read(base+pe+24,optional_size)
    image_size=struct.unpack_from('<I',optional,56)[0]
    if struct.unpack_from('<H',optional)[0]!=0x20b or not 0x10000<=image_size<=512*1024*1024 or image_size!=module_size:
        raise ValueError('Размер/тип client.dll не согласован')
    table_at=pe+24+optional_size
    if table_at+count*40>image_size:raise ValueError('Таблица секций вне образа')
    table=mem.read(base+table_at,count*40);sections=[]
    for i in range(count):
        vsize,rva=struct.unpack_from('<II',table,i*40+8)
        flags=struct.unpack_from('<I',table,i*40+36)[0]
        if not vsize:continue
        if rva<0x1000 or rva+vsize>image_size:raise ValueError('Некорректная секция client.dll')
        section=Section(base+rva,vsize,flags)
        if any(section.start<s.start+s.size and s.start<section.start+section.size for s in sections):
            raise ValueError('Пересекающиеся секции client.dll')
        sections.append(section)
    return Image(base,image_size,stamp,tuple(sections))


def camera_type(mem,image,obj):
    vtable=u64(mem,obj)
    section=image.section(vtable-8,32)
    if not section or section.executable or section.writable:raise ValueError('Камера: vtable не в read-only данных client.dll')
    locator=u64(mem,vtable-8)
    if not image.contains(locator,24):raise ValueError('Камера: RTTI locator вне client.dll')
    raw=mem.read(locator,24)
    signature,offset,cd_offset,type_rva,hierarchy,self_rva=struct.unpack('<IIIIII',raw)
    if signature!=1 or offset!=0 or self_rva+image.base!=locator or not image.contains(image.base+type_rva,17):
        raise ValueError('Камера: неизвестный RTTI layout')
    name=mem.read(image.base+type_rva+16,128).split(b'\0',1)[0].decode('ascii',errors='strict')
    if name not in CAMERA_TYPES:raise ValueError('Камера: неподдержанный RTTI '+name[:80])
    for n in range(3):
        if not image.executable(u64(mem,vtable+8*n)):raise ValueError('Камера: vtable не указывает на код client.dll')
    return vtable,name


@dataclass(frozen=True)
class Camera:
    image:Image
    signature_address:int
    signature_bytes:bytes
    slot:int
    obj:int
    vtable:int
    typename:str
    initial:bytes
    @property
    def address(self):return self.obj+DISTANCE_OFFSET
    def validate(self,mem):
        if not mem.alive():raise ValueError('Dota завершена; старый адрес больше не используется')
        if read_image(mem,self.image.base,self.image.size)!=self.image:
            raise ValueError('Модуль client.dll изменился; запись запрещена')
        if mem.read(self.signature_address,len(PATTERN))!=self.signature_bytes:
            raise ValueError('Код поиска камеры изменился')
        if u64(mem,self.slot)!=self.obj:raise ValueError('Объект камеры сменился; старый адрес не трогаем')
        vtable,name=camera_type(mem,self.image,self.obj)
        if (vtable,name)!=(self.vtable,self.typename):raise ValueError('Идентичность камеры изменилась')
        if not mem.writable(self.address,4):raise ValueError('Поле камеры не является доступными RW-данными')


def discover(mem,base,module_size,cancel=lambda:False):
    image=read_image(mem,base,module_size)
    sections=[s for s in image.sections if s.executable]
    if not sections or sum(s.size for s in sections)>96*1024*1024:raise ValueError('Слишком большой/неизвестный код client.dll')
    found=set();deadline=time.monotonic()+20
    for section in sections:
        pos=0;tail=b''
        while pos<section.size:
            if cancel():raise InterruptedError('Поиск отменён')
            if time.monotonic()>deadline:raise ValueError('Превышено время поиска камеры')
            size=min(256*1024,section.size-pos)
            raw=mem.read(section.start+pos,size) # Incomplete code scan must fail, not declare false uniqueness.
            data=tail+raw;origin=section.start+pos-len(tail)
            found.update(origin+n for n in matches(data))
            if len(found)>1:raise ValueError('Найдено несколько сигнатур; запись запрещена')
            tail=data[-(len(PATTERN)-1):];pos+=size
    if len(found)!=1:raise ValueError('Сигнатура камеры не найдена. Профиль исходника не подходит этой сборке')
    address=next(iter(found));raw=mem.read(address,len(PATTERN))
    if (raw[12]&0xc7)!=0x05:raise ValueError('Инструкция LEA не является RIP-relative')
    manager=address+17+struct.unpack_from('<i',raw,13)[0]
    slot=manager+0x20;section=image.section(slot,8)
    if not pointer(slot) or not section or section.executable or not section.writable:
        raise ValueError('Camera manager не в RW-данных client.dll')
    obj=u64(mem,slot)
    if not pointer(obj) or image.contains(obj):raise ValueError('Объект камеры не готов. Войди в пробу героя')
    vtable,name=camera_type(mem,image,obj)
    if not mem.writable(obj+DISTANCE_OFFSET,4):raise ValueError('Поле камеры не доступно для записи данных')
    value=mem.read(obj+DISTANCE_OFFSET,4)
    camera=Camera(image,address,raw,slot,obj,vtable,name,value)
    camera.validate(mem)
    if mem.read(camera.address,4)!=value:raise ValueError('Камера меняется во время поиска; повтори позже')
    return camera


class UnsupportedDistance(ValueError):
    def __init__(self,camera):
        self.camera=camera
        value=struct.unpack('<f',camera.initial)[0]
        super().__init__('Камера найдена, но поле +0x2E4 (%g) не подтверждено как дистанция. Запись запрещена'%value)


def locate(mem,base,module_size,cancel=lambda:False):
    camera=discover(mem,base,module_size,cancel)
    number=struct.unpack('<f',camera.initial)[0]
    if not math.isfinite(number) or not 600<=number<=2600:raise UnsupportedDistance(camera)
    return camera
