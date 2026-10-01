"""Small standalone zoom UI. Explicit experimental opt-in; KS/Last Hit untouched.
This Source Code Form is subject to MPL-2.0 in LICENSE-MPL-2.0.txt.
"""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from .win32 import API,Memory,instance_lock
from .locator import UnsupportedDistance
from .verified import locate_verified as locate
from .control import Control

ROOT=Path(__file__).resolve().parent


def current_text(value,active):
    text='авто игры' if value==-1 else 'задано %.0f'%value
    return '%s   ·   %s'%(text,'ON' if active else 'OFF')


class Worker(threading.Thread):
    def __init__(self):
        super().__init__(name='Zoom memory worker',daemon=False)
        self.commands=queue.Queue();self.events=queue.Queue();self.quit=threading.Event()
        self.done=threading.Event();self.api=None;self.mem=None;self.control=None
    def emit(self,kind,text='',**kw):self.events.put(dict(kind=kind,text=text,**kw))
    def restore(self):
        if self.control:
            try:
                text=self.control.stop();logging.info(text);self.emit('status',text)
            except Exception as exc:
                logging.exception('RESTORE NOT CONFIRMED');self.emit('restore_error','Возврат не подтверждён: '+str(exc))
        self.control=None
        if self.mem:self.mem.close();self.mem=None
    def connect(self):
        self.restore();self.emit('connecting','Ищу камеру… Только чтение')
        pid=self.api.dota_pid();self.mem=Memory(self.api,pid)
        module=self.api.module(pid,'client.dll')
        logging.info('CONNECT pid=%s client=%#x size=%s path=%s',pid,module.base,module.size,module.path)
        cancel=lambda:self.quit.is_set() or self.api.end_pressed()
        try:camera=locate(self.mem,module.base,module.size,cancel)
        except UnsupportedDistance as exc:
            from .probe import capture,save
            self.emit('connecting','Камера найдена; собираю короткий отчёт без записи в игру…')
            report=capture(self.mem,exc.camera,cancel)
            path=save(report,ROOT/'zoom_probe.json')
            logging.info('CAMERA_PROBE complete=%s path=%s old_2e4_hex=%s',report['complete'],path,exc.camera.initial.hex())
            print('[ZOOM exp4] Отчёт сохранён: '+str(path),flush=True)
            raise ValueError('Камера найдена, поле дистанции неизвестно. Пришли zoom/zoom_probe.json; зум не включался.') from exc
        self.control=Control(self.mem,camera,lambda:self.quit.is_set() or self.api.end_pressed())
        number=self.control.poll()
        logging.info('CANDIDATE PE=%s/%s signature=%#x object=%#x type=%s distance=%s profile=verified_1790705915_override_2e4 EXPERIMENTAL',camera.image.timestamp,camera.image.size,camera.signature_address,camera.obj,camera.typename,number)
        self.emit('ready','Камера найдена · зум выключен',current=number,active=False)
    def run(self):
        try:
            self.api=API();self.commands.put(('connect',None));last=0
            while not self.quit.is_set():
                if self.api.end_pressed():self.quit.set();break
                pending=[]
                while True:
                    try:pending.append(self.commands.get_nowait())
                    except queue.Empty:break
                # A request to switch off wins over slider requests in this batch.
                if any(k=='off' for k,_ in pending):pending=[('off',None)]
                for kind,value in pending:
                    if self.quit.is_set() or self.api.end_pressed():self.quit.set();break
                    try:
                        if kind=='connect':self.connect()
                        elif kind=='on' and self.control:
                            current=self.control.arm(value);logging.info('ARM target=%s',value)
                            self.emit('ready','Зум включён',active=True,current=current)
                        elif kind=='target' and self.control and self.control.active:
                            current=self.control.apply(value);logging.info('SET target=%s',value)
                            self.emit('ready','Зум включён',active=True,current=current)
                        elif kind=='off' and self.control:
                            text=self.control.stop();logging.info(text)
                            self.emit('ready',text,active=False,current=self.control.poll())
                    except InterruptedError:self.quit.set();break
                    except Exception as exc:
                        logging.exception('ZOOM OFF')
                        self.restore();self.emit('error',str(exc))
                if self.control and time.monotonic()-last>=.10:
                    last=time.monotonic()
                    try:self.emit('current',current=self.control.poll())
                    except Exception as exc:
                        logging.exception('MONITOR OFF');self.restore();self.emit('error',str(exc))
                self.quit.wait(.025)
        except Exception as exc:
            logging.exception('WORKER');self.emit('error',str(exc))
        finally:
            self.restore();self.done.set();self.emit('done')


class App:
    BG='#11151c';FG='#edf0f5';MUTED='#a0adbe';GREEN='#7edca5'
    def __init__(self,root):
        self.root=root;self.worker=Worker();self.active=False;self.ready=False;self.closing=False;self.debounce=None;self.restore_problem=None
        root.title('HACHBOB · Zoom exp4');root.configure(bg=self.BG);root.geometry('470x310');root.resizable(False,False)
        root.protocol('WM_DELETE_WINDOW',self.close);root.bind('<End>',lambda e:self.close())
        tk.Label(root,text='HACHBOB   /   ZOOM',font=('Segoe UI',17,'bold'),fg=self.FG,bg=self.BG).pack(anchor='w',padx=22,pady=(17,3))
        tk.Label(root,text='exp4 · исправлен режим автоматической дистанции',font=('Segoe UI',9),fg=self.MUTED,bg=self.BG).pack(anchor='w',padx=22)
        self.distance=tk.IntVar(value=1500);self.heading=tk.StringVar(value='Дистанция: 1500')
        tk.Label(root,textvariable=self.heading,font=('Segoe UI',12,'bold'),fg=self.FG,bg=self.BG).pack(anchor='w',padx=22,pady=(13,0))
        self.scale=tk.Scale(root,from_=1000,to=2200,resolution=25,orient='horizontal',variable=self.distance,showvalue=False,
                           command=self.slider,bg=self.BG,fg=self.MUTED,troughcolor='#273342',activebackground=self.GREEN,
                           highlightthickness=0,length=423,sliderlength=22)
        self.scale.pack(padx=20)
        self.current=tk.StringVar(value='Сейчас: —   ·   запуск всегда OFF')
        tk.Label(root,textvariable=self.current,font=('Segoe UI',9),fg=self.MUTED,bg=self.BG).pack(anchor='w',padx=22)
        row=tk.Frame(root,bg=self.BG);row.pack(fill='x',padx=22,pady=(13,8))
        self.toggle=tk.Button(row,text='Включить зум',command=self.toggle_zoom,state='disabled',font=('Segoe UI',11,'bold'),bg=self.GREEN,fg='#10231a',relief='flat',padx=14,pady=5)
        self.toggle.pack(side='left')
        self.retry=tk.Button(row,text='Найти Dota',command=lambda:self.request('connect'),font=('Segoe UI',10),bg='#273342',fg=self.FG,relief='flat',padx=12,pady=6)
        self.retry.pack(side='right')
        self.status=tk.Label(root,text='Подключение…',font=('Segoe UI',9),fg=self.MUTED,bg=self.BG,wraplength=425,justify='left',anchor='w')
        self.status.pack(fill='x',padx=22)
        tk.Label(root,text='End — вернуть исходное и закрыть. ZFar / туман не меняются.',font=('Segoe UI',8),fg=self.MUTED,bg=self.BG).pack(side='bottom',pady=10)
        self.worker.start();root.after(50,self.events)
    def request(self,kind,value=None):
        if self.closing:return
        if kind=='connect':
            self.ready=False;self.active=False;self.toggle.config(state='disabled',text='Включить зум');self.retry.config(state='disabled')
        self.worker.commands.put((kind,value))
    def toggle_zoom(self):
        if not self.ready:return
        self.toggle.config(state='disabled')
        self.request('off' if self.active else 'on',self.distance.get())
    def slider(self,value):
        self.heading.set('Дистанция: %d'%self.distance.get())
        if self.debounce:self.root.after_cancel(self.debounce)
        self.debounce=self.root.after(180,self.commit_slider)
    def commit_slider(self):
        self.debounce=None
        if self.active:self.request('target',self.distance.get())
    def events(self):
        try:
            while True:
                event=self.worker.events.get_nowait();kind=event['kind']
                if 'current' in event:self.current.set(current_text(event['current'],event.get('active',self.active)))
                if event['text']:self.status.config(text=event['text'],fg='#f59b92' if kind in ('error','restore_error') else self.MUTED)
                if kind=='connecting':self.retry.config(state='disabled')
                elif kind=='ready':
                    self.ready=True;self.active=event['active']
                    self.toggle.config(state='normal',text='Вернуть как было' if self.active else 'Включить зум');self.retry.config(state='normal')
                elif kind in ('error','restore_error'):
                    if kind=='restore_error':self.restore_problem=event['text']
                    self.ready=False;self.active=False;self.toggle.config(state='disabled',text='Включить зум');self.retry.config(state='normal');self.current.set('Запись выключена')
                elif kind=='done':
                    self.retry.config(state='disabled');self.toggle.config(state='disabled')
                    if self.closing or self.worker.quit.is_set():
                        if self.restore_problem:
                            from tkinter import messagebox
                            messagebox.showwarning('Zoom: возврат не подтверждён',self.restore_problem+'\nЕсли картинка не вернулась — перезапусти Dota.',parent=self.root)
                        self.root.destroy();return
        except queue.Empty:pass
        if self.closing and self.worker.done.is_set():self.root.destroy();return
        self.root.after(50,self.events)
    def close(self):
        if self.closing:return
        self.closing=True;self.ready=False;self.toggle.config(state='disabled');self.retry.config(state='disabled')
        self.status.config(text='Выключаю и проверяю возврат исходного значения…')
        self.worker.quit.set()


def main():
    handler=RotatingFileHandler(ROOT/'zoom.log',maxBytes=256*1024,backupCount=1,encoding='utf-8')
    logging.basicConfig(level=logging.INFO,handlers=[handler],format='%(asctime)s %(levelname)s %(message)s')
    logging.info('BUILD experimental4-sentinel-fix; pinned code profile; -1=automatic')
    release=instance_lock();app=None
    try:
        root=tk.Tk();app=App(root);root.mainloop()
    finally:
        if app:app.worker.quit.set();app.worker.join()
        release()


if __name__=='__main__':main()
