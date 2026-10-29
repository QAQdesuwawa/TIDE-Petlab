"""Bounded unbuffered journal readers; never cache file contents by metadata.

Every read obtains actual bytes. File IDs only determine whether a handle still
names the current path. Windows sharing includes delete so atomic replacement
continues to work. No state, proof, clock or persistence decisions live here.
"""
from collections import OrderedDict
from contextlib import contextmanager
import io,os,stat,threading


def _open_shared(path):
    if os.name!='nt':return io.FileIO(path,'rb')
    import ctypes,msvcrt
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    create=kernel.CreateFileW
    create.argtypes=(wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,
        wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE)
    create.restype=wintypes.HANDLE
    handle=create(os.path.abspath(os.fspath(path)),0x80000000,7,None,3,0x80,None)
    if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    try:descriptor=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY)
    except BaseException:
        close=kernel.CloseHandle;close.argtypes=(wintypes.HANDLE,);close(handle);raise
    return io.FileIO(descriptor,'rb',closefd=True)


def _identity(info):return (info.st_dev,info.st_ino)


class JournalReads:
    def __init__(self,*,limit=32):
        self._handles=OrderedDict();self._limit=limit
        self._closed=False;self._lock=threading.RLock();self._depth=0

    @contextmanager
    def scope(self):
        with self._lock:
            if self._closed:raise RuntimeError('journal readers are closed')
            self._depth+=1
            try:yield
            finally:
                self._depth-=1
                if not self._depth:self.release()

    def release(self):
        with self._lock:
            while self._handles:self._handles.popitem()[1].close()

    def read(self,path):
        with self.scope():return self._read(path)

    def _read(self,path):
        path=os.path.abspath(os.fspath(path))
        with self._lock:
            if self._closed:raise RuntimeError('journal readers are closed')
            try:info=os.stat(path)
            except FileNotFoundError:
                old=self._handles.pop(path,None)
                if old is not None:old.close()
                return b''
            if not stat.S_ISREG(info.st_mode):raise OSError('journal path is not a regular file')
            # Filesystems without a meaningful file ID cannot safely retain it.
            if not info.st_ino:
                with _open_shared(path) as stream:return stream.read()
            stream=self._handles.get(path)
            if stream is not None and _identity(os.fstat(stream.fileno()))!=_identity(info):
                stream.close();del self._handles[path];stream=None
            if stream is None:
                stream=_open_shared(path)
                if _identity(os.fstat(stream.fileno()))!=_identity(info):
                    stream.close();raise OSError('journal path changed during open')
                self._handles[path]=stream
            self._handles.move_to_end(path)
            while len(self._handles)>self._limit:self._handles.popitem(last=False)[1].close()
            stream.seek(0);raw=stream.read()
            if _identity(os.stat(path))!=_identity(os.fstat(stream.fileno())):
                self._handles.pop(path).close()
                raise OSError('journal path changed during read')
            return raw

    def close(self):
        with self._lock:
            self._closed=True
            while self._handles:self._handles.popitem()[1].close()
