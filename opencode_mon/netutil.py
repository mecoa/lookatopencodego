"""Shared network helpers for the official-usage and policy fetchers.

Many networks advertise IPv6 (AAAA) records but have an unroutable IPv6
path; Python's urllib follows getaddrinfo order and can stall on IPv6 where
curl silently falls back to IPv4. These helpers prefer IPv4 while keeping
the standard urllib API.
"""

import http.client
import socket
import urllib.request

DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def connect_tcp(host, port, timeout):
    """Connect a TCP socket to (host, port), preferring IPv4 addresses."""
    addrs = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    addrs.sort(key=lambda a: 0 if a[0] == socket.AF_INET else 1)
    err = None
    for af, socktype, proto, _canonname, sa in addrs:
        sock = socket.socket(af, socktype, proto)
        try:
            sock.settimeout(timeout)
            sock.connect(sa)
            return sock
        except OSError as exc:
            err = exc
            sock.close()
    if err is not None:
        raise err
    raise OSError("no addresses resolved for %s" % host)


class _V4HTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        self.sock = connect_tcp(self.host, self.port, self.timeout)
        if getattr(self, "_tunnel_host", None):
            getattr(self, "_tunnel")()
        host = getattr(self, "_tunnel_host", None) or self.host
        self.sock = getattr(self, "_context").wrap_socket(
            self.sock, server_hostname=host)


class _V4HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_V4HTTPSConnection, req)


def open_url(url, timeout, headers=None):
    """Open url over TLS with IPv4-first resolution; returns a response object."""
    merged = {"User-Agent": DEFAULT_UA}
    if headers:
        merged.update(headers)
    req = urllib.request.Request(url, headers=merged)
    opener = urllib.request.build_opener(_V4HTTPSHandler())
    return opener.open(req, timeout=timeout)