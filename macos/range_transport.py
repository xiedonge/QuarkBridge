"""One reusable HTTP connection per download worker; same-origin, no redirects."""
import http.client
import ssl
from urllib.parse import urlsplit
from urllib.request import build_opener,getproxies,proxy_bypass
from urllib.error import HTTPError
from verify_direct import NoRedirect,ProbeError

class RangeResponse:
    def __init__(self,response,owner):
        self.response=response;self.owner=owner;self.status=response.status;self.headers=response.headers
    def __enter__(self):return self
    def read(self,n=-1):
        try:return self.response.read() if n is None or n<0 else self.response.read(n)
        except (OSError,http.client.HTTPException):self.owner.close();raise
    def __exit__(self,*args):self.close()
    def close(self):
        # Never reuse a connection with unread bytes (pause, range error, or short read).
        if self.response.length!=0:self.owner.close()
        self.response.close()

class RangeOpener:
    def __init__(self,route='system'):
        self.route=route;self.connection=None;self.origin=None;self.proxies=getproxies();self.fallback=None
        self.context=ssl.create_default_context()
    def close(self):
        if self.connection:self.connection.close()
        self.connection=None;self.origin=None
    def open(self,request,timeout=20):
        url=request.full_url;u=urlsplit(url)
        if u.scheme not in ('http','https') or not u.hostname or u.username or u.password:
            raise ProbeError('下载地址格式无效。')
        if self.route=='system' and self.proxies.get(u.scheme) and not proxy_bypass(u.hostname):
            self.close()
            if self.fallback is None:self.fallback=build_opener(NoRedirect())
            return self.fallback.open(request,timeout=timeout)
        origin=(u.scheme,u.hostname,u.port or (443 if u.scheme=='https' else 80))
        target=(u.path or '/')+('?' + u.query if u.query else '')
        headers={k:v for k,v in request.header_items() if k.lower() not in ('connection','host')}
        for attempt in range(2):
            reused=self.connection is not None and self.origin==origin
            if not reused:
                self.close()
                if u.scheme=='https':self.connection=http.client.HTTPSConnection(origin[1],origin[2],timeout=timeout,context=self.context)
                else:self.connection=http.client.HTTPConnection(origin[1],origin[2],timeout=timeout)
                self.origin=origin
            else:
                self.connection.timeout=timeout
                if self.connection.sock:self.connection.sock.settimeout(timeout)
            try:
                self.connection.request('GET',target,headers=headers)
                response=self.connection.getresponse()
                break
            except (OSError,http.client.HTTPException):
                self.close()
                if attempt or not reused:raise
        if 300<=response.status<400:
            response.close();self.close();raise ProbeError('下载发生跳转，已停止，未向跳转地址发送登录信息。')
        if response.status>=400:
            code=response.status;response_headers=response.headers;response.close();self.close()
            raise HTTPError(url,code,'download rejected',response_headers,None)
        return RangeResponse(response,self)
