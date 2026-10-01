"""Targeted read-only continuation for the submitted exp2 camera report.
MPL-2.0; see LICENSE-MPL-2.0.txt. No new writable camera profile.
"""
from datetime import datetime,timezone
from pathlib import Path
import time
from .locator import discover,u64
from .probe import FORMAT,save,float_field,code_block

# Evidence pin, not an approved distance layout or anti-cheat guarantee.
EXPECTED=(1790705915,115027968,0x491ded8,'.?AVCDOTA_Camera@@')
TARGETS=(
    (1, 0x1c08ac0, 512, bytes.fromhex('40534883ec30488bd9e8e230d4ff84c075064883c4305bc3488d0d09f45d04e8')),
    (3, 0x1c367c0, 8192, bytes.fromhex('488bc4555357488d68a14881ece00000000f2978c8488bf9440f2940b8440f29')),
    (9, 0x1c24ac0, 768, bytes.fromhex('48896c24104889742420574883ec308bea488bf1e82752190033ff4885c0742a')),
    (13, 0x1c03f20, 256, bytes.fromhex('48895c24184889742420574883ec50488bf90f29742440488d0d7a3e5e04488b')),
    (19, 0x1c02270, 768, bytes.fromhex('40534883ec600f297c2440488bd9488d0d935b5e04440f294424300f28f9e81d')),
    (20, 0x1c02040, 768, bytes.fromhex('40534883ec600f297c2440488bd9488d0dd35d5e04440f294424300f28f9e84d')),
    (21, 0x1c07d50, 768, bytes.fromhex('40534883ec60488b0573005e04488bd90f297c2440440f29442430440f28c148')),
    (22, 0x1c07fa0, 512, bytes.fromhex('4883ec48488b0525fe5d040f297424300f28f10f297c242048634858488b05b5')),
)
OBJECT_BYTES=0x328


def capture_details(mem,camera,cancel=lambda:False):
    start=time.monotonic();deadline=start+3
    image=camera.image
    actual=(image.timestamp,image.size,camera.vtable-image.base,camera.typename)
    if actual!=EXPECTED:raise ValueError('Сборка/тип камеры отличается от присланного отчёта; чтение по старому профилю остановлено')
    result={'format':FORMAT,'build':'experimental3-targeted-camera-details',
            'at_utc':datetime.now(timezone.utc).isoformat(),'complete':False,
            'approved_for_write':False,'memory_writes_by_probe':0,
            'client':{'base':hex(image.base),'size':image.size,'timestamp':image.timestamp},
            'camera':{'type':camera.typename,'address':hex(camera.obj),
                      'vtable_rva':hex(camera.vtable-image.base),
                      'manager_slot_rva':hex(camera.slot-image.base),
                      'signature_rva':hex(camera.signature_address-image.base)},
            'limits':{'object_bytes':OBJECT_BYTES,'vtable_slots_max':64,
                      'short_code_bytes_max':160,'targeted_code_bytes_max':sum(t[2] for t in TARGETS),
                      'targeted_code_windows_max':len(TARGETS),'deadline_seconds':3},
            'note':'Code windows may contain neighbours or truncated methods; not named/approved accessors. No heap traversal, value scan, writes or remote calls.',
            'samples':[],'targeted_code':[],'candidate_vtable_slots':[],'functions':{},'errors':[]}
    def check():
        if cancel():raise InterruptedError('Диагностика отменена')
        if time.monotonic()>deadline:raise TimeoutError('Превышено время дочитывания камеры')
    def slot_address(index):
        slot=camera.vtable+index*8;section=image.section(slot,8)
        if not section or section.executable or section.writable:raise ValueError('vtable slot вне read-only данных')
        return slot,u64(mem,slot)
    try:
        check();camera.validate(mem)
        # Verify all observed method identities BEFORE longer code reads.
        for index,rva,size,prefix in TARGETS:
            check();slot,address=slot_address(index)
            if address!=image.base+rva or not image.executable(address):raise ValueError('Изменился адрес метода '+str(index))
            if mem.read(address,len(prefix))!=prefix:raise ValueError('Изменился код метода '+str(index))
        for _ in range(2):
            check();camera.validate(mem);raw=mem.read(camera.obj,OBJECT_BYTES);camera.validate(mem)
            result['samples'].append({'t_ms':round((time.monotonic()-start)*1000,2),
                'raw_0x000_0x328':raw.hex(),
                'fields':{hex(o):float_field(raw[o:o+4]) for o in (0x70,0x74,0x150,0x154,0x158,0x15c,0x160,0x264,0x270,0x2d0,0x2d4,0x2d8,0x2dc,0x2e0,0x2e4,0x2e8)}})
        for index,rva,size,prefix in TARGETS:
            check();slot,address=slot_address(index)
            if address!=image.base+rva:raise ValueError('Адрес метода изменился во время сбора')
            section=image.section(address,size)
            if not section or not section.executable:raise ValueError('Окно метода выходит за секцию кода')
            raw=mem.read(address,size)
            if not raw.startswith(prefix) or mem.read(address,size)!=raw or u64(mem,slot)!=address:
                raise ValueError('Нестабильное чтение кода метода '+str(index))
            result['targeted_code'].append({'slot_index':index,'rva':hex(rva),'bytes':size,'hex':raw.hex()})
        # Inspect up to 64 consecutive executable pointers, stopping at the
        # first non-code slot. Exact vtable length is not inferred from this.
        for index in range(64):
            check();slot,address=slot_address(index)
            if not image.executable(address):
                result['candidate_vtable_slots'].append({'index':index,'status':'stopped at non-code slot'});break
            raw=code_block(mem,image,address)
            if u64(mem,slot)!=address:raise ValueError('Таблица методов изменилась')
            rva=hex(address-image.base)
            result['candidate_vtable_slots'].append({'index':index,'rva':rva})
            result['functions'][rva]={'hex':raw.hex(),'bytes':len(raw)}
        camera.validate(mem);check();result['complete']=True
    except InterruptedError:raise
    except Exception as exc:result['errors'].append(str(exc)[:200])
    result['elapsed_ms']=round((time.monotonic()-start)*1000,2)
    return result


def main():
    from .win32 import API,Memory
    mem=None;root=Path(__file__).resolve().parent
    print('[ZOOM DETAILS exp3] Только чтение продолжений функций. Зум не включается.',flush=True)
    try:
        api=API();pid=api.dota_pid();mem=Memory(api,pid,readonly=True)
        module=api.module(pid,'client.dll')
        camera=discover(mem,module.base,module.size,api.end_pressed)
        report=capture_details(mem,camera,api.end_pressed)
        path=save(report,root/'zoom_details.json')
        print('Отчёт сохранён: '+str(path),flush=True)
        if not report['complete']:print('Отчёт частичный: '+'; '.join(report['errors']),flush=True)
        print('Пришли zoom_details.json. Запись в память игры не выполнялась.',flush=True)
        return 0
    except Exception as exc:
        print('Отчёт не сохранён: '+str(exc),flush=True)
        print('Пришли этот текст ошибки. Запись в игру не выполнялась.',flush=True)
        return 1
    finally:
        if mem:mem.close()


if __name__=='__main__':raise SystemExit(main())
