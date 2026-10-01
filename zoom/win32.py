"""Minimal x64 Win32 process access. No DLL injection, drivers or protection changes.
This Source Code Form is subject to the MPL-2.0 in LICENSE-MPL-2.0.txt.
"""
import ctypes as C
import os
from dataclasses import dataclass

U32=C.c_uint32;U16=C.c_uint16;UPTR=C.c_size_t;HANDLE=C.c_void_p
INVALID=C.c_void_p(-1).value


class PROCESSENTRY32W(C.Structure):
    _fields_=[('dwSize',U32),('cntUsage',U32),('th32ProcessID',U32),('th32DefaultHeapID',UPTR),('th32ModuleID',U32),('cntThreads',U32),('th32ParentProcessID',U32),('pcPriClassBase',C.c_int32),('dwFlags',U32),('szExeFile',C.c_wchar*260)]


class MODULEENTRY32W(C.Structure):
    _fields_=[('dwSize',U32),('th32ModuleID',U32),('th32ProcessID',U32),('GlblcntUsage',U32),('ProccntUsage',U32),('modBaseAddr',HANDLE),('modBaseSize',U32),('hModule',HANDLE),('szModule',C.c_wchar*256),('szExePath',C.c_wchar*260)]


class MBI(C.Structure):
    _fields_=[('BaseAddress',HANDLE),('AllocationBase',HANDLE),('AllocationProtect',U32),('PartitionId',U16),('RegionSize',UPTR),('State',U32),('Protect',U32),('Type',U32)]


@dataclass(frozen=True)
class Module:
    base:int
    size:int
    path:str


class API:
    def __init__(self):
        if os.name!='nt' or C.sizeof(HANDLE)!=8:raise OSError('Нужны Windows и 64-битный Python 3.11')
        self.k=C.WinDLL('kernel32',use_last_error=True);self.u=C.WinDLL('user32',use_last_error=True)
        signatures={
            'CreateToolhelp32Snapshot':([U32,U32],HANDLE),
            'Process32FirstW':([HANDLE,C.POINTER(PROCESSENTRY32W)],C.c_int),
            'Process32NextW':([HANDLE,C.POINTER(PROCESSENTRY32W)],C.c_int),
            'Module32FirstW':([HANDLE,C.POINTER(MODULEENTRY32W)],C.c_int),
            'Module32NextW':([HANDLE,C.POINTER(MODULEENTRY32W)],C.c_int),
            'OpenProcess':([U32,C.c_int,U32],HANDLE),
            'ReadProcessMemory':([HANDLE,HANDLE,HANDLE,UPTR,C.POINTER(UPTR)],C.c_int),
            'WriteProcessMemory':([HANDLE,HANDLE,HANDLE,UPTR,C.POINTER(UPTR)],C.c_int),
            'VirtualQueryEx':([HANDLE,HANDLE,C.POINTER(MBI),UPTR],UPTR),
            'CloseHandle':([HANDLE],C.c_int),
            'WaitForSingleObject':([HANDLE,U32],U32),
            'GetProcessId':([HANDLE],U32),
        }
        for name,(args,result) in signatures.items():
            f=getattr(self.k,name);f.argtypes=args;f.restype=result
        self.u.GetAsyncKeyState.argtypes=[C.c_int];self.u.GetAsyncKeyState.restype=C.c_int16
    def error(self,message):return OSError(message+' (Win32 %d)'%C.get_last_error())
    def snapshot(self,flags,pid):
        h=self.k.CreateToolhelp32Snapshot(flags,pid)
        if h in (None,0,INVALID):raise self.error('Не удалось получить список процессов/модулей')
        return h
    def dota_pid(self):
        h=self.snapshot(2,0);entry=PROCESSENTRY32W();entry.dwSize=C.sizeof(entry);pids=[]
        try:
            ok=self.k.Process32FirstW(h,C.byref(entry))
            while ok:
                if entry.szExeFile.lower()=='dota2.exe':pids.append(int(entry.th32ProcessID))
                ok=self.k.Process32NextW(h,C.byref(entry))
        finally:self.k.CloseHandle(h)
        if not pids:raise ValueError('Запусти Dota и войди в пробу героя, затем нажми «Найти Dota»')
        if len(pids)!=1:raise ValueError('Несколько dota2.exe; выбор процесса неоднозначен')
        return pids[0]
    def module(self,pid,name):
        h=self.snapshot(0x8|0x10,pid);entry=MODULEENTRY32W();entry.dwSize=C.sizeof(entry);found=[]
        try:
            ok=self.k.Module32FirstW(h,C.byref(entry))
            while ok:
                if entry.szModule.lower()==name.lower():found.append(Module(int(entry.modBaseAddr),int(entry.modBaseSize),entry.szExePath))
                ok=self.k.Module32NextW(h,C.byref(entry))
        finally:self.k.CloseHandle(h)
        if len(found)!=1:raise ValueError('client.dll не найден однозначно; дождись загрузки игры')
        return found[0]
    def end_pressed(self):return bool(self.u.GetAsyncKeyState(35)&0x8000)


class Memory:
    def __init__(self,api,pid,readonly=False):
        self.api=api;self.pid=pid;self.handle=None;self.readonly=readonly
        # VM_READ | VM_WRITE | VM_OPERATION | QUERY_INFORMATION | SYNCHRONIZE
        access=0x0010|0x0400|0x100000
        if not readonly:access|=0x0020|0x0008
        h=api.k.OpenProcess(access,False,pid)
        if not h:raise api.error('Нет доступа к процессу Dota')
        self.handle=h
        if api.k.GetProcessId(h)!=pid:self.close();raise ValueError('PID изменился при подключении')
    def alive(self):return bool(self.handle and self.api.k.WaitForSingleObject(self.handle,0)==258)
    def read(self,address,size):
        if not self.alive() or not 0x10000<=address<0x0000800000000000 or not 0<size<=1024*1024:
            raise ValueError('Некорректное чтение / процесс завершён')
        data=C.create_string_buffer(size);count=UPTR()
        ok=self.api.k.ReadProcessMemory(self.handle,HANDLE(address),data,size,C.byref(count))
        if not ok or count.value!=size:raise self.api.error('Неполное чтение памяти')
        return data.raw
    def writable(self,address,size):
        if not self.alive():return False
        info=MBI()
        if self.api.k.VirtualQueryEx(self.handle,HANDLE(address),C.byref(info),C.sizeof(info))!=C.sizeof(info):return False
        # Heap/data only: no executable page, guard or automatic VirtualProtect.
        return bool(info.State==0x1000 and info.Protect in (0x04,0x08) and info.BaseAddress<=address and address+size<=info.BaseAddress+info.RegionSize)
    def write(self,address,data):
        if self.readonly:raise ValueError('Диагностическое подключение не разрешает запись')
        if len(data)!=4 or not self.writable(address,4):raise ValueError('Разрешена только запись одного float в RW-данные')
        count=UPTR();buf=C.create_string_buffer(data)
        ok=self.api.k.WriteProcessMemory(self.handle,HANDLE(address),buf,len(data),C.byref(count))
        if not ok or count.value!=len(data):raise self.api.error('Неполная запись камеры; автоматический повтор запрещён')
    def close(self):
        if self.handle:self.api.k.CloseHandle(self.handle);self.handle=None


def instance_lock():
    """Prevent two copies of this helper from owning the same camera value."""
    if os.name!='nt':raise OSError('Zoom предназначен для Windows')
    k=C.WinDLL('kernel32',use_last_error=True)
    k.CreateMutexW.argtypes=[HANDLE,C.c_int,C.c_wchar_p];k.CreateMutexW.restype=HANDLE
    k.CloseHandle.argtypes=[HANDLE];k.CloseHandle.restype=C.c_int
    h=k.CreateMutexW(None,False,'Local\\HACHBOB_Standalone_Zoom')
    error=C.get_last_error()
    if not h:raise OSError('Не удалось создать блокировку экземпляра Zoom')
    if error==183:
        k.CloseHandle(h);raise OSError('Окно HACHBOB Zoom уже запущено')
    return lambda:k.CloseHandle(h)
