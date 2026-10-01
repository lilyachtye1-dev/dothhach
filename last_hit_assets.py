"""Local-installation NPC timing, including split #base/#include KV1 files.
No downloads or foreign balance fallbacks. Bounded reads; VPK indexed once.
"""
from pathlib import Path, PurePosixPath
import posixpath
import re
import struct
import zlib

MAX_FILE = 16 * 1024 * 1024
_TOKEN = re.compile(r'(?P<space>\s+)|(?P<comment>//[^\r\n]*|/\*[\s\S]*?\*/)|(?P<quoted>"(?:\\.|[^"\\])*")|(?P<brace>[{}])|(?P<bare>[^\s{}"]+)')


def _kv_document(text):
    """Keep ordered, repeated directives OUTSIDE the value dictionary."""
    if len(text)>MAX_FILE:raise ValueError('oversized NPC KV')
    tokens=[];end=0
    for m in _TOKEN.finditer(text.lstrip('\ufeff')):
        if m.start()!=end:raise ValueError('invalid KV token / unterminated quote')
        end=m.end();kind=m.lastgroup
        if kind in ('space','comment'):continue
        value=m.group()
        if kind=='quoted':value=re.sub(r'\\(["\\])',r'\1',value[1:-1])
        if kind=='bare' and value.startswith('/*'):raise ValueError('unclosed KV comment')
        tokens.append((kind,value))
    if end!=len(text.lstrip('\ufeff')):raise ValueError('invalid KV trailing token')
    pos=0;directives=[]
    def parse(depth=0):
        nonlocal pos
        if depth>64:raise ValueError('KV nesting limit')
        result={}
        while pos<len(tokens):
            kind,k=tokens[pos];pos+=1
            if kind=='brace':
                if k=='}' and depth:return result
                raise ValueError('unexpected KV brace')
            if pos>=len(tokens):raise ValueError('invalid KV pair')
            vk,v=tokens[pos];pos+=1
            if kind=='bare' and k.startswith('#'):
                if depth or k.lower() not in ('#base','#include') or vk=='brace':
                    raise ValueError('unsupported NPC KV directive: '+k)
                directives.append((k.lower(),v));continue
            if kind=='bare' and k.startswith('['):raise ValueError('unsupported NPC KV condition')
            if vk=='brace' and v=='}':raise ValueError('missing KV value')
            result[k]=parse(depth+1) if vk=='brace' else v
        if depth:raise ValueError('unclosed KV block')
        return result
    return parse(),directives


def kv1(text):
    """Standalone KV parser; file dependency resolution belongs to AttackData."""
    return _kv_document(text)[0]


class VPK:
    """Index only NPC text paths once; exact preload/segment length and CRC32."""
    def __init__(self,directory):
        self.directory=Path(directory);self.entries={}
        with self.directory.open('rb') as f:
            h=f.read(12)
            if len(h)!=12:raise ValueError('short VPK header')
            magic,version,size=struct.unpack('<III',h)
            if magic!=0x55aa1234 or version not in (1,2) or not 0<size<=64*1024*1024:
                raise ValueError('unsupported VPK')
            self.header=12 if version==1 else 28;self.size=size
            f.seek(self.header);tree=f.read(size)
            if len(tree)!=size:raise ValueError('short VPK tree')
        pos=0
        def string():
            nonlocal pos
            end=tree.find(b'\0',pos)
            if end<0 or end-pos>512:raise ValueError('bad VPK string')
            value=tree[pos:end].decode('utf-8');pos=end+1
            return value
        while True:
            ext=string()
            if not ext:break
            while True:
                folder=string()
                if not folder:break
                while True:
                    name=string()
                    if not name:break
                    if pos+18>len(tree):raise ValueError('short VPK entry')
                    crc,preload,archive,offset,length,terminator=struct.unpack_from('<IHHIIH',tree,pos);pos+=18
                    if terminator!=0xffff or pos+preload>len(tree):raise ValueError('bad VPK entry')
                    pre=tree[pos:pos+preload];pos+=preload
                    path=(('' if folder==' ' else folder+'/')+name+('.'+ext if ext!=' ' else '')).replace('\\','/').lower()
                    if path.startswith('scripts/npc/') and ext.lower()=='txt':
                        if path in self.entries:raise ValueError('duplicate NPC VPK path: '+path)
                        self.entries[path]=(crc,pre,archive,offset,length)

    def read(self,wanted):
        item=self.entries.get(wanted.replace('\\','/').lower())
        if item is None:raise FileNotFoundError(wanted+' not found in VPK')
        crc,pre,archive,offset,length=item
        if length+len(pre)>MAX_FILE:raise ValueError('oversized NPC file')
        if archive==0x7fff:segment=self.directory;start=self.header+self.size+offset
        else:
            if not self.directory.name.endswith('_dir.vpk'):raise ValueError('bad VPK filename')
            segment=self.directory.with_name(self.directory.name[:-8]+'_%03d.vpk'%archive);start=offset
        with segment.open('rb') as f:f.seek(start);data=pre+f.read(length)
        if len(data)!=len(pre)+length or zlib.crc32(data)&0xffffffff!=crc:
            raise ValueError('NPC VPK length/CRC mismatch: '+wanted)
        return data.decode('utf-8-sig')


def vpk_read(directory,wanted):
    return VPK(directory).read(wanted)


def game_directory(dot):
    import ctypes as C
    from ctypes import wintypes as W
    lib=C.WinDLL('psapi',use_last_error=True)
    fun=lib.GetModuleFileNameExW;fun.argtypes=[W.HANDLE,W.HMODULE,W.LPWSTR,W.DWORD];fun.restype=W.DWORD
    buf=C.create_unicode_buffer(32768)
    if not fun(dot.pm.process_handle,W.HMODULE(dot.client),buf,len(buf)):raise OSError('client.dll path unavailable')
    path=Path(buf.value)
    for parent in path.parents:
        if parent.name.lower()=='dota' and ((parent/'pak01_dir.vpk').exists() or (parent/'scripts/npc/npc_heroes.txt').exists()):return parent
    raise FileNotFoundError('game/dota/pak01_dir.vpk not found beside loaded client.dll')


def _missing(local,base):
    """Recursively fill absent keys; explicit local fields always win."""
    out=dict(local)
    for key,value in base.items():
        if key not in out:out[key]=value
        elif isinstance(out[key],dict) and isinstance(value,dict):out[key]=_missing(out[key],value)
    return out


def _npc_path(path):
    path=path.replace('\\','/')
    if ':' in path or path.startswith('/'):raise ValueError('absolute NPC dependency path refused')
    path=posixpath.normpath(path)
    if not path.startswith('scripts/npc/') or not path.endswith('.txt'):
        raise ValueError('NPC dependency escapes scripts/npc: '+path)
    return path


class AttackData:
    def __init__(self,folder):
        self.folder=Path(folder).resolve();self.units={};self.unit_defaults={}
        self._vpk=None;self._cache={};self.sources={};self._bytes=0
        for file,root,base in (('npc_heroes','DOTAHeroes','npc_dota_hero_base'),
                               ('npc_units','DOTAUnits','npc_dota_units_base')):
            parsed=self._load('scripts/npc/'+file+'.txt')
            block=parsed.get(root)
            if not isinstance(block,dict):raise ValueError('invalid '+file+' root; expected '+root)
            rows={k:v for k,v in block.items() if isinstance(v,dict)}
            for name,row in rows.items():
                if name in self.units:raise ValueError('duplicate NPC definition: '+name)
                self.units[name]=row;self.unit_defaults[name]=base
        # The merged unit rows own references to their values. Drop redundant roots.
        self.file_count=len(self._cache);self._cache.clear()

    def _text(self,path):
        p=(self.folder/path).resolve()
        if not p.is_relative_to(self.folder/'scripts'/'npc'):
            raise ValueError('NPC dependency leaves installation: '+path)
        if p.exists():
            if p.stat().st_size>MAX_FILE:raise ValueError('oversized NPC file: '+path)
            with p.open('rb') as f:data=f.read(MAX_FILE+1)
            if len(data)>MAX_FILE:raise ValueError('oversized NPC file: '+path)
            raw=data.decode('utf-8-sig');self.sources[path]='loose'
        else:
            if self._vpk is None:self._vpk=VPK(self.folder/'pak01_dir.vpk')
            raw=self._vpk.read(path);self.sources[path]='VPK'
        self._bytes+=len(raw.encode('utf-8'))
        if self._bytes>64*1024*1024:raise ValueError('NPC dependency byte budget exceeded')
        return raw

    def _load(self,path,stack=()):
        path=_npc_path(path)
        if path in stack or len(stack)>=16:raise ValueError('NPC #base/#include cycle/depth: '+path)
        if path in self._cache:return self._cache[path]
        if len(self.sources)>=512:raise ValueError('NPC dependency file limit')
        try:doc,directives=_kv_document(self._text(path))
        except (OSError,ValueError) as exc:raise ValueError('NPC '+path+': '+str(exc)) from exc
        # Includes append definitions; bases supply missing fields. Repeated
        # directives retain their order; never collapse them to a dict key.
        for mode in ('#include','#base'):
            for kind,name in directives:
                if kind!=mode:continue
                if ':' in name or name.replace('\\','/').startswith('/'):
                    raise ValueError('absolute NPC dependency path refused')
                dep=posixpath.join(str(PurePosixPath(path).parent),name.replace('\\','/'))
                doc=_missing(doc,self._load(dep,stack+(path,)))
        self._cache[path]=doc
        return doc

    def entry(self,name,visited=()):
        if name in visited or len(visited)>8:raise ValueError('NPC inheritance cycle')
        row=self.units.get(name)
        if row is None:raise ValueError('Нет локальных NPC-параметров: '+name+'; записей='+str(len(self.units)))
        defaults={};base=self.unit_defaults.get(name)
        if base and base!=name and base in self.units:defaults=dict(self.units[base])
        parent=row.get('BaseClass')
        if parent in self.units:defaults.update(self.entry(parent,visited+(name,)))
        defaults.update(row)
        return defaults

    def timing(self,name,ranged,require_turn=True):
        row=self.entry(name)
        def value(key,low,high):
            if key not in row:raise ValueError('Нет '+key+' в локальных NPC-данных: '+name)
            n=float(row[key])
            if not low<=n<=high:raise ValueError('invalid NPC '+key)
            return n
        return (value('AttackAnimationPoint',0,2),
                value('MovementTurnRate',.01,10) if require_turn else 1.,
                value('ProjectileSpeed',100,10000) if ranged else 0.)
