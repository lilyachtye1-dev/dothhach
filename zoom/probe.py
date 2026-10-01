"""Bounded camera-only diagnostic. No memory writes, value scan or automatic approval.
This Source Code Form is subject to MPL-2.0 in LICENSE-MPL-2.0.txt.
"""
from datetime import datetime,timezone
from pathlib import Path
import json
import math
import struct
import time
from .locator import discover,u64

FORMAT='hachbob.camera.readonly.v1'
MAX_REPORT=128*1024


def float_field(raw):
    n=struct.unpack('<f',raw)[0]
    return {'hex':raw.hex(),'f32':n if math.isfinite(n) else None,'finite':math.isfinite(n)}


def code_block(mem,image,address):
    section=image.section(address)
    if not section or not section.executable:raise ValueError('not executable client.dll code')
    size=min(160,section.start+section.size-address)
    raw=mem.read(address,size)
    if mem.read(address,size)!=raw:raise ValueError('code changed during read')
    return raw


def method_capture(mem,image,address):
    # Up to two internal jump thunks. Never follow arbitrary heap pointers or
    # remote-call anything. Data slots must stay in read-only image sections.
    blocks=[];seen=set()
    for _ in range(3):
        if address in seen:break
        seen.add(address)
        raw=code_block(mem,image,address)
        blocks.append({'rva':hex(address-image.base),'hex':raw.hex()})
        destination=None
        if raw[:1]==b'\xe9' and len(raw)>=5:
            destination=address+5+struct.unpack_from('<i',raw,1)[0]
        elif raw[:2]==b'\xff\x25' and len(raw)>=6:
            slot=address+6+struct.unpack_from('<i',raw,2)[0]
            section=image.section(slot,8)
            if section and not section.executable and not section.writable:destination=u64(mem,slot)
        if destination is None or not image.executable(destination):break
        address=destination
    return blocks


def capture(mem,camera,cancel=lambda:False):
    """Only current object 0x000..0x3ff and 32 candidate vtable slots.
    Raw bytes are evidence, NOT approved offsets. A vtable length is not known,
    so probed slots are not all asserted to belong to this class.
    """
    started=time.monotonic();deadline=started+3.
    report={'format':FORMAT,'build':'experimental2-camera-probe','at_utc':datetime.now(timezone.utc).isoformat(),
            'approved_for_write':False,'memory_writes_by_probe':0,'complete':False,
            'scope':'camera object + bounded client.dll code only; no broad process/value scan',
            'client':{'base':hex(camera.image.base),'size':camera.image.size,'timestamp':camera.image.timestamp},
            'camera':{'address':hex(camera.obj),'type':camera.typename,'vtable_rva':hex(camera.vtable-camera.image.base),
                      'manager_slot_rva':hex(camera.slot-camera.image.base),'signature_rva':hex(camera.signature_address-camera.image.base),
                      'signature_hex':camera.signature_bytes.hex()},
            'source_profile':{'write_offset':'0x2e4','upstream_old_getter_offset':'0x270','both_unapproved_on_this_build':True},
            'samples':[],'candidate_vtable_slots':[],'functions':{},'errors':[]}
    def check():
        if cancel():raise InterruptedError('Диагностика отменена')
        if time.monotonic()>deadline:raise TimeoutError('camera diagnostic deadline')
    try:
        for index in range(3):
            check();camera.validate(mem)
            raw=mem.read(camera.obj,0x400)
            camera.validate(mem);check()
            fields={hex(off):float_field(raw[off:off+4]) for off in range(0x200,0x380,4)}
            report['samples'].append({'t_ms':round((time.monotonic()-started)*1000,2),'raw_0x000_0x400':raw.hex(),'fields':fields})
            if index<2:time.sleep(.06)
        # Slots are read only when contained in the same RO data section.
        for index in range(32):
            check();slot=camera.vtable+index*8;section=camera.image.section(slot,8)
            if not section or section.executable or section.writable:break
            row={'index':index}
            try:
                address=u64(mem,slot)
                if not camera.image.executable(address):
                    row['status']='not executable client.dll address'
                else:
                    rva=hex(address-camera.image.base);row.update(rva=rva,status='code captured')
                    if rva not in report['functions']:report['functions'][rva]=method_capture(mem,camera.image,address)
                if u64(mem,slot)!=address:raise ValueError('vtable slot changed')
            except (ValueError,OSError) as exc:row['status']='unavailable: '+str(exc)[:140]
            report['candidate_vtable_slots'].append(row)
        camera.validate(mem);check()
        report['complete']=True
    except InterruptedError:raise
    except Exception as exc:report['errors'].append(str(exc)[:200])
    report['elapsed_ms']=round((time.monotonic()-started)*1000,2)
    return report


def save(report,path):
    if report.get('format')!=FORMAT or report.get('approved_for_write') is not False:raise ValueError('Invalid diagnostic format')
    raw=(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode('utf-8')
    if len(raw)>MAX_REPORT:raise ValueError('Diagnostic report size limit')
    path=Path(path);temporary=path.with_suffix(path.suffix+'.tmp')
    try:temporary.write_bytes(raw);temporary.replace(path)
    finally:
        if temporary.exists():temporary.unlink()
    return path


def main():
    from .win32 import API,Memory
    root=Path(__file__).resolve().parent;mem=None
    print('[ZOOM CHECK exp2] Только чтение. Изменять масштаб или искать адреса вручную не нужно.',flush=True)
    print('Dota должна быть запущена, лучше находиться в пробе обычного героя.',flush=True)
    try:
        api=API();pid=api.dota_pid();mem=Memory(api,pid,readonly=True)
        module=api.module(pid,'client.dll');camera=discover(mem,module.base,module.size,api.end_pressed)
        report=capture(mem,camera,api.end_pressed)
        path=save(report,root/'zoom_probe.json')
        print('Отчёт сохранён: '+str(path),flush=True)
        print('Пришли zoom_probe.json. Это диагностика, зум здесь не включается.',flush=True)
        if not report['complete']:print('Отчёт частичный: '+'; '.join(report['errors']),flush=True)
        return 0
    except Exception as exc:
        print('Диагностика остановлена: '+str(exc),flush=True)
        print('Запись в игру не выполнялась. Если объект не готов, войди в пробу героя и повтори.',flush=True)
        return 1
    finally:
        if mem:mem.close()


if __name__=='__main__':raise SystemExit(main())
