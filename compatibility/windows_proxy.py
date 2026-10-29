"""Legacy Windows urllib proxy compatibility, following CPython 3.11.16.
Source: https://github.com/python/cpython/blob/3.11/Lib/urllib/request.py
Adaptation: local handler selection only; no stdlib or OS settings are patched.
Current CPython host-only ProxyOverride matching is backported to older runtimes.
Copyright (c) Python Software Foundation. All rights reserved.

PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2
--------------------------------------------

1. This LICENSE AGREEMENT is between the Python Software Foundation
("PSF"), and the Individual or Organization ("Licensee") accessing and
otherwise using this software ("Python") in source or binary form and
its associated documentation.

2. Subject to the terms and conditions of this License Agreement, PSF hereby
grants Licensee a nonexclusive, royalty-free, world-wide license to reproduce,
analyze, test, perform and/or display publicly, prepare derivative works,
distribute, and otherwise use Python alone or in any derivative version,
provided, however, that PSF's License Agreement and PSF's notice of copyright,
i.e., "Copyright (c) 2001, 2002, 2003, 2004, 2005, 2006, 2007, 2008, 2009, 2010,
2011, 2012, 2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023 Python Software Foundation;
All Rights Reserved" are retained in Python alone or in any derivative version
prepared by Licensee.

3. In the event Licensee prepares a derivative work that is based on
or incorporates Python or any part thereof, and wants to make
the derivative work available to others as provided herein, then
Licensee hereby agrees to include in any such work a brief summary of
the changes made to Python.

4. PSF is making Python available to Licensee on an "AS IS"
basis.  PSF MAKES NO REPRESENTATIONS OR WARRANTIES, EXPRESS OR
IMPLIED.  BY WAY OF EXAMPLE, BUT NOT LIMITATION, PSF MAKES NO AND
DISCLAIMS ANY REPRESENTATION OR WARRANTY OF MERCHANTABILITY OR FITNESS
FOR ANY PARTICULAR PURPOSE OR THAT THE USE OF PYTHON WILL NOT
INFRINGE ANY THIRD PARTY RIGHTS.

5. PSF SHALL NOT BE LIABLE TO LICENSEE OR ANY OTHER USERS OF PYTHON
FOR ANY INCIDENTAL, SPECIAL, OR CONSEQUENTIAL DAMAGES OR LOSS AS
A RESULT OF MODIFYING, DISTRIBUTING, OR OTHERWISE USING PYTHON,
OR ANY DERIVATIVE THEREOF, EVEN IF ADVISED OF THE POSSIBILITY THEREOF.

6. This License Agreement will automatically terminate upon a material
breach of its terms and conditions.

7. Nothing in this License Agreement shall be deemed to create any
relationship of agency, partnership, or joint venture between PSF and
Licensee.  This License Agreement does not grant permission to use PSF
trademarks or trade name in a trademark sense to endorse or promote
products or services of Licensee, or any third party.

8. By copying, installing or otherwise using Python, Licensee
agrees to be bound by the terms and conditions of this License
Agreement."""

import base64,sys
from fnmatch import fnmatch
import urllib.request,urllib.parse

VERSION='windows-proxy-hostname-compat-v1'


def match_override(host,override):
    host,_=urllib.parse._splitport(host)
    for pattern in override.split(';'):
        pattern=pattern.strip()
        if pattern=='<local>':
            if '.' not in host:return True
        elif fnmatch(host,pattern):return True
    return False


def bypass(host):
    proxies=urllib.request.getproxies_environment()
    if proxies:return urllib.request.proxy_bypass_environment(host,proxies)
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Internet Settings') as key:
            enabled=winreg.QueryValueEx(key,'ProxyEnable')[0]
            override=str(winreg.QueryValueEx(key,'ProxyOverride')[0])
    except (ImportError,OSError):return False
    return bool(enabled and override and match_override(host,override))


class HostnameProxyHandler(urllib.request.ProxyHandler):
    def proxy_open(self,req,proxy,request_type):
        original_type=req.type
        proxy_type,user,password,hostport=urllib.request._parse_proxy(proxy)
        proxy_type=proxy_type or original_type
        if req.host and bypass(req.host):return None
        if user and password:
            credentials='%s:%s'%(urllib.parse.unquote(user),urllib.parse.unquote(password))
            encoded=base64.b64encode(credentials.encode()).decode('ascii')
            req.add_header('Proxy-authorization','Basic '+encoded)
        req.set_proxy(urllib.parse.unquote(hostport),proxy_type)
        if original_type==proxy_type or original_type=='https':return None
        return self.parent.open(req,timeout=req.timeout)


def provider_proxy_handler():
    if sys.platform=='win32' and not hasattr(urllib.request,'_proxy_bypass_winreg_override'):
        return HostnameProxyHandler()
    return urllib.request.ProxyHandler()
