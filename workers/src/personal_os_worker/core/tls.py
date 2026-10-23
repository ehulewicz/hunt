"""One TLS context for every HTTP client the worker makes.

Each fresh context's first handshake leaves about half a megabyte the process
never gets back (measured on OpenSSL 3.6): the device agent, which built two
fresh clients every 30 seconds, grew about 170 MB an hour while idle. A
context is safe to share between clients and threads, so every `httpx` client
and one-off request here verifies with this one.
"""

from __future__ import annotations

import ssl
from functools import cache

import httpx


@cache
def tls() -> ssl.SSLContext:
    return httpx.create_ssl_context()
