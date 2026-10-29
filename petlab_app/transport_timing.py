"""Per-request monotonic diagnostics. Never model input or scheduling time."""
from contextlib import contextmanager
from contextvars import ContextVar
import http.client
import time
import urllib.request

_current=ContextVar('petlab_http_diagnostics',default=None)

class Capture:
    def __init__(self):
        self.started=time.perf_counter()
        self.events={}
    def record(self):
        return {'version':'petlab-http-phases-v1','clock':'monotonic-elapsed-ms',
                'elapsed_ms':dict(sorted(self.events.items(),key=lambda item:item[1])),
                'complete':'decoded' in self.events}

@contextmanager
def capture():
    value=Capture();token=_current.set(value)
    try:yield value
    finally:_current.reset(token)

def mark(name):
    value=_current.get()
    if value is not None:
        value.events[name]=round((time.perf_counter()-value.started)*1000,4)

class _ConnectionTiming:
    def __init__(self,*args,**kwargs):
        mark('connection_setup_started')
        super().__init__(*args,**kwargs)
        mark('connection_setup_finished')
        create=self._create_connection
        def measured_socket(*args,**kwargs):
            mark('dns_tcp_started')
            result=create(*args,**kwargs)
            mark('dns_tcp_finished')
            return result
        self._create_connection=measured_socket
    def connect(self):
        mark('connect_started')
        result=super().connect()
        mark('connect_finished')
        return result
    def _tunnel(self):
        mark('proxy_tunnel_started')
        result=super()._tunnel()
        mark('proxy_tunnel_finished')
        return result
    def request(self,*args,**kwargs):
        mark('request_started')
        result=super().request(*args,**kwargs)
        mark('request_sent')
        return result
    def getresponse(self):
        mark('response_headers_wait_started')
        result=super().getresponse()
        mark('response_headers_received')
        return result

class HTTPConnection(_ConnectionTiming,http.client.HTTPConnection):pass
class HTTPSConnection(_ConnectionTiming,http.client.HTTPSConnection):pass

class HTTPHandler(urllib.request.HTTPHandler):
    def http_open(self,req):return self.do_open(HTTPConnection,req)

class HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self,req):
        return self.do_open(HTTPSConnection,req,context=self._context)

def handlers():
    # Keep the existing standard handlers when there is no diagnostic consumer.
    return () if _current.get() is None else (HTTPHandler(),HTTPSHandler())
