"""Optional scene data controls; separate from accepted distance control.
MPL-2.0. No code patches, remote calls, input injection or Fog-of-War fields.
"""
from dataclasses import dataclass
import hashlib
import math
import struct
import time
from .locator import pointer,u64

ENTITY_SYSTEM_RVA=0x653dbb0
FARZ_RVA=0x1c07d50
FARZ_BYTES=0x245
FARZ_SHA256='f2e19a3151ffae295bf598be1f7f3097de42f1943cd1b415bf564438f3ef52c5'
SCHEMA_SHA256='fabedd63041971c8ee09243fb229abd896d259b8e7351a61c1700f594868b1c6'
# Reviewed named fields in the matching schema, not camera +0x70 / +0x2a4.
KINDS={
 'dota_gamemode':('C_DOTABaseGameMode',0x760,'farz'),
 'env_fog_controller':('C_FogController',0x620,'haze'), # m_fog + maxdensity
 'env_cubemap_fog':('C_EnvCubemapFog',0x618,'haze'), # m_flFogMaxOpacity
 'env_volumetric_fog_controller':('C_EnvVolumetricFogController',0x5f0,'haze'), # m_flScattering
}
MAX_ENTITIES=64


def string(mem,address):
    if type(address) is not int or not 0x10000<=address<0x0000800000000000:raise ValueError('Invalid name pointer')
    raw=mem.read(address,96)
    if b'\0' not in raw:raise ValueError('Unterminated entity name')
    return raw.split(b'\0',1)[0].decode('ascii')


def typename(mem,image,obj):
    vt=u64(mem,obj);s=image.section(vt-8,32)
    if not s or s.executable or s.writable:raise ValueError('Entity vtable is not RO client.dll data')
    col=u64(mem,vt-8)
    if not image.contains(col,24):raise ValueError('Entity RTTI outside client.dll')
    sig,off,cd,typ,hierarchy,self_rva=struct.unpack('<IIIIII',mem.read(col,24))
    if sig!=1 or off!=0 or image.base+self_rva!=col or not image.contains(image.base+typ+16,96):
        raise ValueError('Unknown entity RTTI layout')
    if not image.executable(u64(mem,vt)):raise ValueError('Entity method outside client.dll')
    raw=mem.read(image.base+typ+16,96)
    if b'\0' not in raw:raise ValueError('Unterminated RTTI name')
    return vt,raw.split(b'\0',1)[0].decode('ascii')


class Context:
    def __init__(self,mem,camera):
        self.mem=mem;self.camera=camera;self.image=camera.image
        self.global_address=self.image.base+ENTITY_SYSTEM_RVA
        s=self.image.section(self.global_address,8)
        if not s or s.executable or not s.writable:raise ValueError('Entity system global outside RW data')
        self.esys=u64(mem,self.global_address)
        if not pointer(self.esys) or self.image.contains(self.esys):raise ValueError('Entity system is not ready')
        self.validate()
    def validate(self):
        self.camera.validate(self.mem)
        if u64(self.mem,self.global_address)!=self.esys:raise ValueError('Сцена сменилась; старые адреса не трогаем')
    def validate_farz(self):
        self.validate();address=u64(self.mem,self.camera.vtable+21*8)
        if address!=self.image.base+FARZ_RVA or not self.image.section(address,FARZ_BYTES) or not self.image.executable(address):
            raise ValueError('Не совпал метод расчёта FarZ')
        if hashlib.sha256(self.mem.read(address,FARZ_BYTES)).hexdigest()!=FARZ_SHA256:
            raise ValueError('Код расчёта FarZ не совпал; запись запрещена')
    def scan(self,cancel=lambda:False,kind=None):
        self.validate();start=time.monotonic();found=[]
        def check():
            if cancel():raise InterruptedError('Поиск сцены отменён')
            if time.monotonic()-start>5:raise TimeoutError('Поиск сцены превысил 5 секунд')
        # Fixed, typed entity table: <=64 chunks, NOT a process-memory scan.
        for b in range(64):
            check();chunk=u64(self.mem,self.esys+0x10+b*8)
            if not chunk:continue
            if not pointer(chunk) or self.image.contains(chunk):raise ValueError('Invalid entity-table chunk')
            raw=self.mem.read(chunk,512*0x70)
            for i in range(512):
                if i%32==0:check()
                at=i*0x70;ep=struct.unpack_from('<Q',raw,at)[0]
                if not pointer(ep) or self.image.contains(ep):continue
                handle=struct.unpack_from('<I',raw,at+16)[0];index=b*512+i
                if not index or handle in (0,0xffffffff) or handle&0x7fff!=index:continue
                name_ptr=struct.unpack_from('<Q',raw,at+32)[0]
                try:name=string(self.mem,name_ptr)
                except (ValueError,OSError,UnicodeError):continue
                if name not in KINDS or (kind is not None and KINDS[name][2]!=kind):continue
                cls,off,group=KINDS[name];vt,typ=typename(self.mem,self.image,ep)
                if typ!='.?AV'+cls+'@@':raise ValueError('Неподдержанный RTTI '+name+': '+typ)
                e=Entity(self,ep,chunk+at,chunk,index,handle,name,vt,typ)
                e.validate();found.append(e)
                if len(found)>MAX_ENTITIES:raise ValueError('Слишком много контроллеров сцены')
            if u64(self.mem,self.esys+0x10+b*8)!=chunk:raise ValueError('Entity chunk changed during scan')
        check();self.validate()
        return found


@dataclass(frozen=True)
class Entity:
    context:object
    address:int
    identity:int
    chunk:int
    index:int
    handle:int
    name:str
    vtable:int
    typename:str
    def validate(self):
        c=self.context;m=c.mem;c.validate()
        if u64(m,c.esys+0x10+(self.index//512)*8)!=self.chunk:raise ValueError('Entity chunk changed')
        raw=m.read(self.identity,40)
        if (struct.unpack_from('<Q',raw)[0]!=self.address or struct.unpack_from('<I',raw,16)[0]!=self.handle or
                u64(m,self.address+16)!=self.identity):raise ValueError('Идентичность контроллера сцены изменилась')
        if string(m,struct.unpack_from('<Q',raw,32)[0])!=self.name:raise ValueError('Имя контроллера сцены изменилось')
        if typename(m,c.image,self.address)!=(self.vtable,self.typename):raise ValueError('Тип контроллера сцены изменился')
        if KINDS[self.name][2]=='farz':c.validate_farz()
    @property
    def field(self):return self.address+KINDS[self.name][1]


def original_bytes(entity):
    entity.validate();mem=entity.context.mem
    if not mem.writable(entity.field,4):raise ValueError('Параметр сцены не в RW-данных')
    raw=mem.read(entity.field,4);v=struct.unpack('<f',raw)[0]
    group=KINDS[entity.name][2]
    if group=='farz':valid=(v in (-1,0) or 32<=v<=65536)
    elif entity.name=='env_volumetric_fog_controller':valid=0<=v<=100
    else:valid=0<=v<=1
    if not math.isfinite(v) or not valid:raise ValueError('Неизвестное значение '+entity.name)
    return raw


class RestoreError(ValueError):
    pass


class Group:
    def __init__(self,context,kind,cancel=lambda:False):
        if kind not in ('farz','haze'):raise ValueError('Unknown scene option')
        self.context=context;self.kind=kind;self.cancel=cancel;self.records=[];self.active=False
    def start(self):
        if self.records or self.active:raise ValueError('Сначала выключи режим')
        entities=[e for e in self.context.scan(self.cancel,self.kind) if KINDS[e.name][2]==self.kind]
        if not entities:raise ValueError('Поддерживаемые параметры '+('FarZ' if self.kind=='farz' else 'дымки')+' в сцене не найдены')
        if self.kind=='farz' and len(entities)!=1:raise ValueError('GameMode найден неоднозначно; запись запрещена')
        if len({e.field for e in entities})!=len(entities):raise ValueError('Duplicate scene fields')
        plan=[]
        for e in entities:
            old=original_bytes(e);v=struct.unpack('<f',old)[0]
            target=struct.pack('<f',max(18000.,v) if self.kind=='farz' else 0.)
            plan.append([e,old,target,False])
        try:
            for record in plan:
                e,old,target,_=record;e.validate();m=self.context.mem
                if m.read(e.field,4)!=old:raise ValueError('Параметр изменился перед записью')
                if self.cancel():raise InterruptedError('Включение отменено')
                self.records.append(record)
                if old==target:continue
                record[3]=True # include an attempted/possibly partial write
                m.write(e.field,target);e.validate()
                if m.read(e.field,4)!=target:raise ValueError('Изменение сцены не подтвердилось')
            self.active=True
            return sum(int(r[3]) for r in self.records)
        except Exception as exc:
            try:self.stop()
            except Exception as restore:raise RestoreError(str(exc)+'; возврат: '+str(restore)) from exc
            raise
    def poll(self):
        self.context.validate()
        for e,old,target,attempted in self.records:
            e.validate()
            if self.context.mem.read(e.field,4)!=target:raise ValueError('Игра изменила '+self.kind+'; постоянного перезаписывания нет')
    def stop(self):
        self.active=False;errors=[]
        for e,old,target,attempted in reversed(self.records):
            if not attempted:continue
            try:
                e.validate();m=self.context.mem;current=m.read(e.field,4)
                if current==old:continue
                if current!=target:raise ValueError('Чужое изменение '+e.name+' не затирается')
                e.validate()
                if m.read(e.field,4)!=current:raise ValueError('Поле снова изменилось перед возвратом')
                m.write(e.field,old);e.validate()
                if m.read(e.field,4)!=old:raise ValueError('Исходное значение не восстановлено')
            except Exception as exc:errors.append(str(exc))
        self.records=[]
        if errors:raise RestoreError('; '.join(errors)[:500])
        return 'Исходные параметры возвращены'
