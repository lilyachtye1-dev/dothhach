"""Build-pinned camera-distance override profile from the submitted code report.
MPL-2.0; see LICENSE-MPL-2.0.txt. Does not execute or patch game code.
"""
from dataclasses import dataclass
import hashlib
import math
import struct
from .locator import discover,u64,UnsupportedDistance

# PE timestamp, size, vtable RVA, manager slot RVA, signature RVA, update RVA.
PIN=(1790705915,115027968,0x491ded8,0x61ff4e0,0x1c39719,0x1c367c0)
UPDATE_BYTES=0xb18
UPDATE_SHA256='b323a3bcf694d2cb9c32b1ef31240e5aee9e319144345c131db9a3e05a800c37'


@dataclass(frozen=True)
class VerifiedCamera:
    camera:object
    allow_automatic=True
    def __getattr__(self,name):return getattr(self.camera,name)
    def validate(self,mem):
        self.camera.validate(mem)
        image=self.image
        actual=(image.timestamp,image.size,self.vtable-image.base,self.slot-image.base,self.signature_address-image.base)
        if actual!=PIN[:5] or self.typename!='.?AVCDOTA_Camera@@':
            raise ValueError('Сборка камеры не поддерживается профилем exp4; запись запрещена')
        address=u64(mem,self.vtable+3*8)
        if address!=image.base+PIN[5] or not image.section(address,UPDATE_BYTES) or not image.executable(address):
            raise ValueError('Изменился метод обновления камеры; запись запрещена')
        if hashlib.sha256(mem.read(address,UPDATE_BYTES)).hexdigest()!=UPDATE_SHA256:
            raise ValueError('Код расчёта дистанции отличается от проверенного; запись запрещена')
        # A positive interpolation timer lets the game overwrite the override.
        # Do not compete with that transition, including on restoration.
        timer=struct.unpack('<f',mem.read(self.obj+0xdc,4))[0]
        if not math.isfinite(timer) or timer>0:
            raise ValueError('Игра выполняет переход камеры; управление зумом остановлено')


def locate_verified(mem,base,module_size,cancel=lambda:False):
    camera=VerifiedCamera(discover(mem,base,module_size,cancel))
    camera.validate(mem)
    value=struct.unpack('<f',camera.initial)[0]
    if not math.isfinite(value) or not (value==-1 or 600<=value<=2600):
        raise UnsupportedDistance(camera)
    if cancel():raise InterruptedError('Поиск отменён')
    if mem.read(camera.address,4)!=camera.initial:raise ValueError('Поле камеры изменилось во время проверки')
    return camera
