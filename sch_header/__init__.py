import logging
import http
from http import client as http_client

from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPSConnectionPool
from urllib3.poolmanager import ProxyManager
import requests
from requests.adapters import HTTPAdapter


logger = logging.getLogger(__name__)

# https://stackoverflow.com/questions/39068998/reading-connect-headers
# 关于如何获取 CONNECT headers 的方案

_sch_prefix = 'x-https-connect-header-'


class ProxyHeaderHTTPSConnection(HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._proxy_headers = []

    def _tunnel(self):
        logger.debug('Start https tunnel...')

        connect_str = "CONNECT %s:%d HTTP/1.0\r\n" % (self._tunnel_host, self._tunnel_port)
        connect_bytes = connect_str.encode("ascii")
        self.send(connect_bytes)
        for header, value in self._tunnel_headers.items():
            header_str = "%s: %s\r\n" % (header, value)
            header_bytes = header_str.encode("latin-1")
            self.send(header_bytes)
        self.send(b'\r\n')

        response = self.response_class(self.sock, method=self._method)
        (version, code, message) = response._read_status()

        if code != http.HTTPStatus.OK:
            self.close()
            raise OSError("Tunnel connection failed: %d %s" % (code, message.strip()))
        self._proxy_headers = {}

        while True:
            line = response.fp.readline(http_client._MAXLINE + 1)
            if len(line) > http_client._MAXLINE:
                raise http_client.LineTooLong("header line")
            if not line:
                # for sites which EOF without sending a trailer
                break
            if line in (b'\r\n', b'\n', b''):
                break
            # The line is a header, save it
            if b':' in line:
                hdr, val = line.decode().split(':', 1)
                self._proxy_headers[_sch_prefix + hdr] = val.strip()
            logger.debug('One line https connect header: {}.'.format(line.decode()))

    def getresponse(self):
        response = super().getresponse()
        for hdr, val in self._proxy_headers.items():
            response.headers[hdr] = val
        return response


class ProxyHeaderHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = ProxyHeaderHTTPSConnection


class ProxyHeaderProxyManager(ProxyManager):
    def _new_pool(self, scheme, host, port, request_context=None):
        assert scheme == 'https'
        if request_context is None:
            request_context = self.connection_pool_kw.copy()
        for key in ("scheme", "host", "port"):
            request_context.pop(key, None)
        return ProxyHeaderHTTPSConnectionPool(host, port, **request_context)


class ProxyHeaderHTTPAdapter(HTTPAdapter):
    def proxy_manager_for(self, proxy, **proxy_kwargs):
        if proxy in self.proxy_manager:
            manager = self.proxy_manager[proxy]
        else:
            proxy_headers = self.proxy_headers(proxy)
            manager = self.proxy_manager[proxy] = ProxyHeaderProxyManager(
                proxy_url=proxy,
                proxy_headers=proxy_headers,
                num_pools=self._pool_connections,
                maxsize=self._pool_maxsize,
                block=self._pool_block,
                **proxy_kwargs)
        return manager


def sch_get(url, params=None, **kwargs):
    kwargs.setdefault('allow_redirects', True)
    with requests.sessions.Session() as session:
        session.mount('https://', ProxyHeaderHTTPAdapter())
        return session.request(method='get', url=url, params=params, **kwargs)


def init_sch(sch_prefix: str = 'x-https-connect-header-'):
    if sch_prefix:
        global _sch_prefix
        _sch_prefix = sch_prefix
    requests.get = sch_get
