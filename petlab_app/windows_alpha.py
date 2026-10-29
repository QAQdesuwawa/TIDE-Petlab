"""Windows-only per-pixel presentation of a verified PNG atlas.

GDI+ decodes/resamples the original alpha to premultiplied BGRA. No color-key,
edge threshold, image rewrite, model access, or behavioral decisions occur here.
"""
from __future__ import annotations

import ctypes as c
from ctypes import wintypes as w
from dataclasses import dataclass
import sys


class _Startup(c.Structure):
    _fields_=[("version",w.UINT),("callback",c.c_void_p),("no_thread",w.BOOL),("no_codecs",w.BOOL)]


class _Rect(c.Structure):
    _fields_=[("x",c.c_int),("y",c.c_int),("width",c.c_int),("height",c.c_int)]


class _BitmapData(c.Structure):
    _fields_=[("width",w.UINT),("height",w.UINT),("stride",c.c_int),("format",c.c_int),
        ("scan0",c.c_void_p),("reserved",c.c_size_t)]


class _Header(c.Structure):
    _fields_=[("size",w.DWORD),("width",w.LONG),("height",w.LONG),("planes",w.WORD),
        ("bits",w.WORD),("compression",w.DWORD),("image_size",w.DWORD),
        ("x_resolution",w.LONG),("y_resolution",w.LONG),("colors",w.DWORD),("important",w.DWORD)]


class _Info(c.Structure):
    _fields_=[("header",_Header),("colors",w.DWORD*3)]


class _Blend(c.Structure):
    _fields_=[("operation",w.BYTE),("flags",w.BYTE),("constant",w.BYTE),("alpha",w.BYTE)]


@dataclass(frozen=True)
class AlphaFrame:
    width:int
    height:int
    bgra:bytes


def _function(dll,name,args,result):
    function=getattr(dll,name);function.argtypes=args;function.restype=result
    return function


def _check(status):
    if status:raise RuntimeError("GDI+ sprite operation failed: "+str(status))


def load_alpha_frames(image_path,grid,*,subsample=2,output_size=None):
    from .frame_cache import cached_alpha_decode
    def decode():return _decode_alpha_frames(image_path,grid,subsample=subsample,output_size=output_size)
    if sys.platform!='win32':return decode()
    return cached_alpha_decode(image_path,grid,subsample=subsample,output_size=output_size,produce=decode)


def _decode_alpha_frames(image_path,grid,*,subsample=2,output_size=None):
    """Decode cells directly from the same verified asset, without changing the PNG."""
    if sys.platform!="win32":raise OSError("per-pixel sprite rendering requires Windows")
    if any(type(grid.get(k))is not int or grid[k]<=0 for k in ('cell_width','cell_height','columns','rows')):
        raise ValueError('alpha grid dimensions must be positive integers')
    dll=c.WinDLL("gdiplus",use_last_error=True)
    pointer=c.c_void_p;out=c.POINTER(pointer)
    startup=_function(dll,"GdiplusStartup",[c.POINTER(c.c_size_t),c.POINTER(_Startup),pointer],c.c_int)
    shutdown=_function(dll,"GdiplusShutdown",[c.c_size_t],None)
    create=_function(dll,"GdipCreateBitmapFromFile",[w.LPCWSTR,out],c.c_int)
    dispose=_function(dll,"GdipDisposeImage",[pointer],c.c_int)
    get_width=_function(dll,"GdipGetImageWidth",[pointer,c.POINTER(w.UINT)],c.c_int)
    get_height=_function(dll,"GdipGetImageHeight",[pointer,c.POINTER(w.UINT)],c.c_int)
    bitmap=_function(dll,"GdipCreateBitmapFromScan0",[c.c_int,c.c_int,c.c_int,c.c_int,pointer,out],c.c_int)
    graphics=_function(dll,"GdipGetImageGraphicsContext",[pointer,out],c.c_int)
    delete_graphics=_function(dll,"GdipDeleteGraphics",[pointer],c.c_int)
    clear=_function(dll,"GdipGraphicsClear",[pointer,w.UINT],c.c_int)
    interpolation=_function(dll,"GdipSetInterpolationMode",[pointer,c.c_int],c.c_int)
    compositing=_function(dll,"GdipSetCompositingMode",[pointer,c.c_int],c.c_int)
    draw=_function(dll,"GdipDrawImageRectRectI",[pointer,pointer]+[c.c_int]*9+[pointer,pointer,pointer],c.c_int)
    lock=_function(dll,"GdipBitmapLockBits",[pointer,c.POINTER(_Rect),w.UINT,c.c_int,c.POINTER(_BitmapData)],c.c_int)
    unlock=_function(dll,"GdipBitmapUnlockBits",[pointer,c.POINTER(_BitmapData)],c.c_int)
    token=c.c_size_t();image=pointer();started=False
    cell_w,cell_h=grid["cell_width"],grid["cell_height"]
    width,height=(cell_w+subsample-1)//subsample,(cell_h+subsample-1)//subsample
    if output_size is not None:
        if (not isinstance(output_size,(tuple,list)) or len(output_size)!=2
                or any(isinstance(n,bool) or not isinstance(n,int) or n<=0 for n in output_size)):
            raise ValueError("alpha output dimensions must be positive integers")
        width,height=output_size
    pixel_format=0x000E200B # PixelFormat32bppPARGB: native premultiplied BGRA.
    frames=[]
    try:
        _check(startup(c.byref(token),c.byref(_Startup(1,None,False,False)),None));started=True
        _check(create(str(image_path),c.byref(image)))
        actual_w,actual_h=w.UINT(),w.UINT()
        _check(get_width(image,c.byref(actual_w)));_check(get_height(image,c.byref(actual_h)))
        regions=grid.get('regions')
        expected_size=(grid['columns']*cell_w,grid['rows']*cell_h) if regions is None else tuple(grid.get('source_size',()))
        if (actual_w.value,actual_h.value)!=expected_size:
            raise ValueError('alpha grid differs from actual PNG dimensions')
        if regions is not None:
            if not isinstance(regions,list) or len(regions)!=grid['columns']*grid['rows'] or any(not isinstance(r,list) or len(r)!=4 or any(type(n)is not int for n in r) or not 0<=r[0]<r[2]<=actual_w.value or not 0<=r[1]<r[3]<=actual_h.value for r in regions):raise ValueError('invalid explicit atlas regions')
        for row in range(grid["rows"]):
            for column in range(grid["columns"]):
                sx,sy,sw,sh=column*cell_w,row*cell_h,cell_w,cell_h
                if regions is not None:
                    x0,y0,x1,y1=regions[row*grid['columns']+column];sx,sy,sw,sh=x0,y0,x1-x0,y1-y0
                destination=pointer();context=pointer();data=_BitmapData();locked=False
                try:
                    _check(bitmap(width,height,0,pixel_format,None,c.byref(destination)))
                    _check(graphics(destination,c.byref(context)))
                    _check(clear(context,0));_check(compositing(context,1));_check(interpolation(context,7))
                    _check(draw(context,image,0,0,width,height,sx,sy,sw,sh,2,None,None,None))
                    _check(lock(destination,c.byref(_Rect(0,0,width,height)),1,pixel_format,c.byref(data)));locked=True
                    pixels=b"".join(c.string_at(data.scan0+y*data.stride,width*4) for y in range(height))
                    frames.append(AlphaFrame(width,height,pixels))
                finally:
                    if locked:unlock(destination,c.byref(data))
                    if context:delete_graphics(context)
                    if destination:dispose(destination)
        return tuple(frames)
    finally:
        if image:dispose(image)
        if started:shutdown(token)


class WindowsAlphaSurface:
    CACHE_KEY_VERSION="immutable-artwork-v2"
    """Present to the actual Tk top-level HWND while retaining its input bindings."""
    def __init__(self,root,frames):
        self.root=root;self.frames=tuple(frames);self.closed=False
        self.hwnd=None;self.dc=None;self.bitmap=None;self.previous=None;self.bits=c.c_void_p();self.size=None
        self._pixel_cache={}
        user=c.WinDLL("user32",use_last_error=True);gdi=c.WinDLL("gdi32",use_last_error=True)
        self.ancestor=_function(user,"GetAncestor",[w.HWND,w.UINT],w.HWND)
        self.get_style=_function(user,"GetWindowLongPtrW",[w.HWND,c.c_int],c.c_ssize_t)
        self.set_style=_function(user,"SetWindowLongPtrW",[w.HWND,c.c_int,c.c_ssize_t],c.c_ssize_t)
        self.update=_function(user,"UpdateLayeredWindow",[w.HWND,w.HDC,c.POINTER(w.POINT),
            c.POINTER(w.SIZE),w.HDC,c.POINTER(w.POINT),w.DWORD,c.POINTER(_Blend),w.DWORD],w.BOOL)
        self.create_dc=_function(gdi,"CreateCompatibleDC",[w.HDC],w.HDC)
        self.delete_dc=_function(gdi,"DeleteDC",[w.HDC],w.BOOL)
        self.create_bitmap=_function(gdi,"CreateDIBSection",[w.HDC,c.POINTER(_Info),w.UINT,
            c.POINTER(c.c_void_p),w.HANDLE,w.DWORD],w.HBITMAP)
        self.select=_function(gdi,"SelectObject",[w.HDC,w.HGDIOBJ],w.HGDIOBJ)
        self.delete=_function(gdi,"DeleteObject",[w.HGDIOBJ],w.BOOL)

    def _release_buffer(self):
        if self.dc and self.previous:self.select(self.dc,self.previous)
        if self.bitmap:self.delete(self.bitmap)
        if self.dc:self.delete_dc(self.dc)
        self.bitmap=None;self.dc=None;self.previous=None;self.size=None
        self._pixel_cache.clear()

    def present(self,index,*,x=0,y=0,cache=True):
        if self.closed or not self.root.winfo_ismapped():return
        frame=self.frames[index]
        width,height=self.root.winfo_width(),self.root.winfo_height()
        if x<0 or y<0 or x+frame.width>width or y+frame.height>height:return
        hwnd=self.ancestor(self.root.winfo_id(),2)
        if not hwnd:raise c.WinError(c.get_last_error())
        if hwnd!=self.hwnd:
            style=self.get_style(hwnd,-20)
            # Reset any prior SetLayeredWindowAttributes mode before using per-pixel alpha.
            self.set_style(hwnd,-20,style&~0x80000);self.set_style(hwnd,-20,style|0x80000)
            self.hwnd=hwnd
        if self.size!=(width,height):
            self._release_buffer()
            self.dc=self.create_dc(None)
            if not self.dc:raise c.WinError(c.get_last_error())
            info=_Info(_Header(c.sizeof(_Header),width,-height,1,32,0,width*height*4,0,0,0,0))
            self.bitmap=self.create_bitmap(self.dc,c.byref(info),0,c.byref(self.bits),None,0)
            if not self.bitmap:
                self._release_buffer();raise c.WinError(c.get_last_error())
            self.previous=self.select(self.dc,self.bitmap);self.size=(width,height)
        # The same index in a different immutable atlas is different artwork.
        key=(frame,x,y)
        pixels=self._pixel_cache.get(key) if cache else None
        if pixels is None:
            if (x,y,frame.width,frame.height)==(0,0,width,height):pixels=frame.bgra
            else:
                buffer=bytearray(width*height*4)
                for row in range(frame.height):
                    start=((y+row)*width+x)*4
                    buffer[start:start+frame.width*4]=frame.bgra[row*frame.width*4:(row+1)*frame.width*4]
                pixels=bytes(buffer)
            if cache:self._pixel_cache[key]=pixels
        c.memmove(self.bits,pixels,width*height*4)
        size=w.SIZE(width,height);origin=w.POINT(0,0);blend=_Blend(0,0,255,1)
        if not self.update(hwnd,None,None,c.byref(size),self.dc,c.byref(origin),0,c.byref(blend),2):
            raise c.WinError(c.get_last_error())

    def close(self):
        if self.closed:return
        self.closed=True;self._release_buffer()
