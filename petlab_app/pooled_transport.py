"""Process-local HTTP connections, with explicit proxy/TLS and zero retries.

Only sockets are reused. Requests, authorization headers and response bodies
remain per invocation; no cookies, model responses or conversation are cached.
"""
import atexit
from collections import OrderedDict
from contextlib import contextmanager,closing
import os
import ssl
import threading
import urllib.parse
import urllib.request

import httpx

from compatibility.windows_proxy import bypass
from .transport_timing import mark

_lock=threading.RLock()
_pools=OrderedDict()
_pid=os.getpid()


def proxy_for(endpoint):
    url=urllib.parse.urlsplit(endpoint)
    proxy=urllib.request.getproxies().get(url.scheme)
    if not proxy or bypass(url.netloc):return None
    return proxy if '://' in proxy else 'http://'+proxy


@contextmanager
def connection(endpoint):
    global _pid
    url=urllib.parse.urlsplit(endpoint)
    key=(url.scheme,url.netloc,proxy_for(endpoint),os.getenv('SSL_CERT_FILE'),os.getenv('SSL_CERT_DIR'))
    with _lock:
        if _pid!=os.getpid():
            _pools.clear();_pid=os.getpid()
        item=_pools.get(key)
        if item is None:
            mark('connection_setup_started')
            # Same platform trust roots used by the previous urllib transport.
            transport=httpx.HTTPTransport(verify=ssl.create_default_context(),proxy=key[2],
                trust_env=False,retries=0,http1=True,http2=False,
                limits=httpx.Limits(max_connections=4,max_keepalive_connections=2,keepalive_expiry=30))
            item=[transport,0];_pools[key]=item
            mark('connection_setup_finished')
        item[1]+=1;_pools.move_to_end(key)
    try:yield item[0]
    finally:
        with _lock:
            item[1]-=1
            # Route changes never reuse a connection to the old proxy/host.
            # Bound idle pool storage without closing an in-flight request.
            for old in list(_pools):
                if len(_pools)<=8:break
                value=_pools[old]
                if value[1]==0:
                    del _pools[old];value[0].close()


def post(endpoint,data,headers,timeout):
    secure=urllib.parse.urlsplit(endpoint).scheme=='https'
    def trace(name,info):
        # httpcore's trace payload may contain headers. Retain event names only.
        names={'connection.connect_tcp.started':'connect_started',
            'connection.connect_tcp.complete':'dns_tcp_finished',
            'connection.start_tls.complete':'connect_finished',
            'http11.send_request_headers.started':'request_started',
            'http11.send_request_body.complete':'request_sent',
            'http11.receive_response_headers.started':'response_headers_wait_started',
            'http11.receive_response_headers.complete':'response_headers_received'}
        if name in names:mark(names[name])
        if not secure and name=='connection.connect_tcp.complete':mark('connect_finished')
    with connection(endpoint) as transport:
        mark('opener_ready')
        request=httpx.Request('POST',endpoint,headers=headers,content=data,
            extensions={'timeout':{k:timeout for k in ('connect','read','write','pool')},'trace':trace})
        with closing(transport.handle_request(request)) as response:
            mark('response_opened')
            if not 200<=response.status_code<300:
                return response.status_code,None
            raw=response.read()
            mark('body_received')
            return response.status_code,raw


def close_idle():
    with _lock:
        for key,item in list(_pools.items()):
            if item[1]==0:
                del _pools[key];item[0].close()


atexit.register(close_idle)
