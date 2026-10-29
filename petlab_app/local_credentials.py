"""Opt-in current-Windows-user DPAPI storage; never part of a public export."""
import ctypes,hashlib,os
from pathlib import Path

class Blob(ctypes.Structure):
    _fields_=[('size',ctypes.c_ulong),('data',ctypes.POINTER(ctypes.c_ubyte))]

def _crypt(raw,protect):
    if os.name!='nt':raise OSError('Secure key storage requires Windows')
    buffer=ctypes.create_string_buffer(raw)
    source=Blob(len(raw),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)))
    target=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    # CRYPTPROTECT_UI_FORBIDDEN: current-user protection, no machine scope.
    if protect:
        ok=crypt.CryptProtectData(ctypes.byref(source),'TIDE PetLab API',None,None,None,1,ctypes.byref(target))
    else:
        ok=crypt.CryptUnprotectData(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target))
    if not ok:raise OSError('Windows could not access the locally protected key')
    try:return ctypes.string_at(target.data,target.size)
    finally:
        kernel.LocalFree.argtypes=[ctypes.c_void_p]
        kernel.LocalFree(ctypes.cast(target.data,ctypes.c_void_p))

def _path(name,directory=None):
    if not isinstance(name,str) or not name:raise ValueError('key environment name required')
    base=Path(directory) if directory is not None else Path(__file__).resolve().parents[1]/'output/user-settings/secrets'
    return base/(hashlib.sha256(name.encode()).hexdigest()+'.dpapi')

def store(name,value,*,directory=None):
    if not isinstance(value,str) or not value:raise ValueError('key must be nonempty')
    path=_path(name,directory);encrypted=_crypt(value.encode('utf8'),True)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp');temporary.write_bytes(encrypted);os.replace(temporary,path)

def load(name,*,directory=None):
    path=_path(name,directory)
    if not path.exists():return None
    return _crypt(path.read_bytes(),False).decode('utf8')
