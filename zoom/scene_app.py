"""Optional FarZ/haze UI. The accepted Zoom UI/control files remain unchanged.
MPL-2.0; see LICENSE-MPL-2.0.txt.
"""
import ctypes as C
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from .win32 import API,Memory
from .verified import locate_verified
from .scene import Context,Group,RestoreError,KINDS
from .farz import DirectFarZ

ROOT=Path(__file__).resolve().parent


class Worker(threading.Thread):
    def __init__(self):
        super().__init__(name='Scene controls',daemon=False)
        self.commands=queue.Queue();self.events=queue.Queue();self.quit=threading.Event();self.done=threading.Event()
        self.api=None;self.mem=None;self.context=None;self.groups={}
    def emit(self,text,kind='state'):
        self.events.put({'kind':kind,'text':text,'ready':self.context is not None,
                         'farz':bool(self.groups.get('farz') and self.groups['farz'].active),
                         'haze':bool(self.groups.get('haze') and self.groups['haze'].active)})
    def off(self,kind):
        g=self.groups.get(kind)
        if g:
            try:
                text=g.stop();logging.info('SCENE OFF %s %s',kind,text)
            except Exception as exc:
                logging.exception('SCENE RESTORE '+kind);self.emit('Возврат не подтверждён: '+str(exc),'restore_error')
        self.groups.pop(kind,None)
    def cleanup(self):
        for kind in list(self.groups):self.off(kind)
        if self.mem:self.mem.close()
        self.mem=None;self.context=None
    def connect(self):
        self.cleanup();self.emit('Проверяю сборку и камеру…','busy')
        pid=self.api.dota_pid();self.mem=Memory(self.api,pid);m=self.api.module(pid,'client.dll')
        camera=locate_verified(self.mem,m.base,m.size,self.cancel)
        self.context=Context(self.mem,camera)
        logging.info('SCENE CONNECT pid=%s PE=%s/%s',pid,camera.image.timestamp,camera.image.size)
        self.emit('Готово. Оба режима OFF. Рабочий зум управляется отдельно.')
    def cancel(self):return self.quit.is_set() or self.api.end_pressed()
    def run(self):
        try:
            self.api=API();self.commands.put('connect');last=0
            while not self.cancel():
                try:command=self.commands.get(timeout=.05)
                except queue.Empty:command=None
                if self.cancel():break
                try:
                    if command=='connect':self.connect()
                    elif command=='off':
                        for kind in list(self.groups):self.off(kind)
                        self.emit('Режимы выключены. Ошибки возврата, если были, записаны в журнал.')
                    elif command in ('farz','haze') and self.context:
                        if command in self.groups:
                            self.off(command);self.emit('Режим выключен.')
                        else:
                            self.emit('Проверяю FarZ камеры…' if command=='farz' else 'Проверяю контроллеры сцены…','busy')
                            group=DirectFarZ(self.context,self.cancel) if command=='farz' else Group(self.context,command,self.cancel)
                            self.groups[command]=group
                            try:count=group.start()
                            except Exception:
                                # start attempts conditional rollback; retain any failure in UI warning.
                                self.off(command);raise
                            logging.info('SCENE ON %s writes=%s',command,count)
                            if hasattr(group,'describe'):logging.info('CAMERA FARZ %s',group.describe())
                            else:
                                for e,old,target,wrote in group.records:
                                    logging.info('SCENE FIELD %s type=%s handle=%#x offset=%#x original=%s target=%s wrote=%s',command,e.typename,e.handle,KINDS[e.name][1],old.hex(),target.hex(),wrote)
                            self.emit(('FarZ: минимум 18000.' if command=='farz' else 'Поддерживаемая атмосферная дымка: параметры уменьшены до нуля.')+' Изменений: '+str(count))
                except InterruptedError:break
                except Exception as exc:
                    logging.exception('SCENE ACTION')
                    if command=='connect':self.cleanup()
                    self.emit(str(exc),'restore_error' if isinstance(exc,RestoreError) else 'error')
                if self.context and time.monotonic()-last>=.25:
                    last=time.monotonic()
                    for kind,g in list(self.groups.items()):
                        if not g.active:continue
                        try:g.poll()
                        except Exception as exc:
                            logging.exception('SCENE MONITOR');self.off(kind);self.emit(str(exc),'error')
        except Exception as exc:
            logging.exception('SCENE WORKER');self.emit(str(exc),'error')
        finally:
            self.cleanup();self.done.set();self.emit('Закрыто.','done')


class App:
    def __init__(self,root):
        self.root=root;self.worker=Worker();self.closing=False;self.restore_error=None
        bg='#11151c';fg='#edf0f5';muted='#a0adbe'
        root.title('HACHBOB · Scene exp6');root.geometry('470x295');root.configure(bg=bg);root.resizable(False,False)
        root.protocol('WM_DELETE_WINDOW',self.close);root.bind('<End>',lambda e:self.close())
        tk.Label(root,text='HACHBOB   /   СЦЕНА',font=('Segoe UI',17,'bold'),fg=fg,bg=bg).pack(anchor='w',padx=22,pady=(17,4))
        tk.Label(root,text='ZFar и атмосферная дымка · без тумана войны',fg=muted,bg=bg,font=('Segoe UI',9)).pack(anchor='w',padx=22)
        self.buttons={}
        row=tk.Frame(root,bg=bg);row.pack(fill='x',padx=22,pady=(18,12))
        for kind,text in [('farz','ZFar · OFF'),('haze','Дымка · как в игре')]:
            b=tk.Button(row,text=text,state='disabled',command=lambda k=kind:self.request(k),font=('Segoe UI',10,'bold'),bg='#273342',fg=fg,relief='flat',padx=12,pady=8)
            b.pack(side='left',padx=(0,10));self.buttons[kind]=b
        row=tk.Frame(root,bg=bg);row.pack(fill='x',padx=22)
        self.off=tk.Button(row,text='Вернуть оба',command=lambda:self.request('off'),bg='#273342',fg=fg,relief='flat',padx=10,pady=6);self.off.pack(side='left')
        self.retry=tk.Button(row,text='Найти Dota',command=lambda:self.request('connect'),bg='#273342',fg=fg,relief='flat',padx=10,pady=6);self.retry.pack(side='right')
        self.status=tk.Label(root,text='Подключение…',wraplength=425,justify='left',anchor='w',font=('Segoe UI',9),fg=muted,bg=bg)
        self.status.pack(fill='x',padx=22,pady=12)
        tk.Label(root,text='End / закрытие — условный возврат. Сам зум не меняется.',font=('Segoe UI',8),fg=muted,bg=bg).pack(side='bottom',pady=10)
        self.worker.start();root.after(50,self.events)
    def request(self,command):
        if self.closing:return
        for b in [*self.buttons.values(),self.off,self.retry]:b.config(state='disabled')
        self.worker.commands.put(command)
    def events(self):
        try:
            while True:
                e=self.worker.events.get_nowait()
                if e['kind']=='restore_error':self.restore_error=e['text']
                self.status.config(text=e['text'],fg='#f59b92' if e['kind'] in ('error','restore_error') else '#a0adbe')
                state='normal' if e['ready'] and e['kind']!='busy' and not self.closing else 'disabled'
                self.buttons['farz'].config(state=state,text='ZFar · ON' if e['farz'] else 'ZFar · OFF',bg='#27784d' if e['farz'] else '#273342')
                self.buttons['haze'].config(state=state,text='Без дымки · ON' if e['haze'] else 'Дымка · как в игре',bg='#27784d' if e['haze'] else '#273342')
                self.off.config(state=state);self.retry.config(state='normal' if not self.closing and e['kind']!='busy' else 'disabled')
                if e['kind']=='done':
                    if self.restore_error:
                        from tkinter import messagebox
                        messagebox.showwarning('Возврат сцены',self.restore_error+'\nПри оставшемся эффекте перезапусти Dota.',parent=self.root)
                    self.root.destroy();return
        except queue.Empty:pass
        self.root.after(50,self.events)
    def close(self):
        self.closing=True;self.worker.quit.set()
        for b in [*self.buttons.values(),self.off,self.retry]:b.config(state='disabled')
        self.status.config(text='Возвращаю исходные параметры…')


def instance_lock():
    k=C.WinDLL('kernel32',use_last_error=True)
    k.CreateMutexW.argtypes=[C.c_void_p,C.c_int,C.c_wchar_p];k.CreateMutexW.restype=C.c_void_p
    k.CloseHandle.argtypes=[C.c_void_p];k.CloseHandle.restype=C.c_int
    h=k.CreateMutexW(None,False,'Local\\HACHBOB_Scene_Controls');error=C.get_last_error()
    if not h:raise OSError('Не удалось создать блокировку окна Scene')
    if error==183:k.CloseHandle(h);raise OSError('Scene уже запущен')
    return lambda:k.CloseHandle(h)


def main():
    logging.basicConfig(level=logging.INFO,handlers=[RotatingFileHandler(ROOT/'scene.log',maxBytes=128*1024,backupCount=1,encoding='utf-8')],format='%(asctime)s %(levelname)s %(message)s')
    logging.info('SCENE exp6: direct camera FarZ data path; haze unchanged; no code patches')
    release=instance_lock();app=None
    try:
        root=tk.Tk();app=App(root);root.mainloop()
    finally:
        if app:app.worker.quit.set();app.worker.join()
        release()


if __name__=='__main__':main()
