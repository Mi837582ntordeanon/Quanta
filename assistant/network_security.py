"""Public HTTP access for model-triggered tools, with SSRF and redirect checks."""
from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
import struct
import urllib.parse
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator


_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_LOCAL_SUFFIXES = (".localhost", ".local", ".localdomain", ".internal", ".lan", ".home", ".home.arpa")
_NUMERIC_HOST = re.compile(r"[0-9a-fA-FxX.]+\Z")


@dataclass(frozen=True)
class PublicTarget:
    url: str
    scheme: str
    host: str
    port: int
    connect_ip: str
    request_target: str


def _legacy_ipv4(host: str) -> ipaddress.IPv4Address | None:
    """Parse inet_aton-style shorthand, octal and hexadecimal IPv4 forms."""
    if not _NUMERIC_HOST.fullmatch(host):
        return None
    parts = host.split(".")
    if not 1 <= len(parts) <= 4 or any(not part for part in parts):
        raise ValueError("Некорректная числовая форма IP-адреса")

    values: list[int] = []
    for part in parts:
        if part.lower().startswith("0x"):
            digits, base = part[2:], 16
            if not digits:
                raise ValueError("Некорректная числовая форма IP-адреса")
        elif len(part) > 1 and part.startswith("0"):
            digits, base = part[1:], 8
        else:
            digits, base = part, 10
        try:
            value = int(digits or "0", base)
        except ValueError as exc:
            raise ValueError("Некорректная числовая форма IP-адреса") from exc
        values.append(value)

    if len(values) == 1:
        if values[0] > 0xFFFFFFFF:
            raise ValueError("Числовой IP-адрес вне диапазона")
        packed = struct.pack("!I", values[0])
    else:
        if any(value > 0xFF for value in values[:-1]):
            raise ValueError("Числовой IP-адрес вне диапазона")
        final_bits = 8 * (5 - len(values))
        if values[-1] >= 1 << final_bits:
            raise ValueError("Числовой IP-адрес вне диапазона")
        result = values[-1]
        for index, value in enumerate(values[:-1]):
            result |= value << (24 - index * 8)
        packed = struct.pack("!I", result)
    return ipaddress.IPv4Address(packed)


def _public_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError("Имя хоста разрешилось в некорректный IP-адрес") from exc
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    if (not address.is_global or address.is_private or address.is_loopback or address.is_link_local
            or address.is_reserved or address.is_multicast or address.is_unspecified):
        raise ValueError("Запросы к локальным и специальным IP-адресам запрещены")
    return address


def resolve_public_url(url: str, *, allowed_hosts: set[str] | None = None) -> PublicTarget:
    """Validate an HTTP(S) URL and resolve it to a public IP for pinned use."""
    value = str(url or "").strip()
    if not value or any(ord(char) < 32 for char in value):
        raise ValueError("Некорректный URL")
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Некорректный URL") from exc
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError("Разрешены только URL с http:// или https://")
    if not parsed.netloc or parsed.hostname is None or "@" in parsed.netloc:
        raise ValueError("URL должен содержать имя хоста без учётных данных")

    raw_host = parsed.hostname.rstrip(".").lower()
    if not raw_host or "%" in raw_host:
        raise ValueError("Некорректное имя хоста")
    try:
        numeric = ipaddress.ip_address(raw_host)
    except ValueError:
        numeric = _legacy_ipv4(raw_host)

    if numeric is not None:
        address = _public_ip(str(numeric))
        host = str(address)
        addresses = [address]
    else:
        try:
            host = raw_host.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValueError("Некорректное имя хоста") from exc
        if ("." not in host or host == "localhost" or host.endswith(_LOCAL_SUFFIXES)
                or host in {"ip6-localhost", "ip6-loopback"}):
            raise ValueError("Локальные имена хостов запрещены")
        addresses = []
        try:
            records = socket.getaddrinfo(host, port or (443 if scheme == "https" else 80), type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ValueError(f"Не удалось безопасно разрешить имя хоста: {host}") from exc
        for record in records:
            address = _public_ip(record[4][0].split("%", 1)[0])
            if address not in addresses:
                addresses.append(address)
        if not addresses:
            raise ValueError("Имя хоста не имеет IP-адресов")

    if allowed_hosts is not None and host not in {item.rstrip(".").lower() for item in allowed_hosts}:
        raise ValueError("Имя хоста не входит в список разрешённых")

    path = parsed.path or "/"
    request_target = path + ("?" + parsed.query if parsed.query else "")
    normalized = urllib.parse.urlunsplit((scheme, parsed.netloc, path, parsed.query, ""))
    return PublicTarget(
        url=normalized,
        scheme=scheme,
        host=host,
        port=port or (443 if scheme == "https" else 80),
        connect_ip=str(addresses[0]),
        request_target=request_target,
    )


def _make_connection(target: PublicTarget, timeout: float) -> http.client.HTTPConnection:
    host_arg = f"[{target.host}]" if ":" in target.host else target.host
    if target.scheme == "https":
        connection = http.client.HTTPSConnection(
            host_arg, target.port, timeout=timeout, context=ssl.create_default_context()
        )

        def connect() -> None:
            raw = socket.create_connection((target.connect_ip, target.port), timeout=timeout)
            try:
                connection.sock = connection._context.wrap_socket(raw, server_hostname=target.host)
            except BaseException:
                raw.close()
                raise

        connection.connect = connect  # type: ignore[method-assign]
        return connection

    connection = http.client.HTTPConnection(host_arg, target.port, timeout=timeout)

    def connect() -> None:
        connection.sock = socket.create_connection((target.connect_ip, target.port), timeout=timeout)

    connection.connect = connect  # type: ignore[method-assign]
    return connection


@contextmanager
def safe_open(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
    max_redirects: int = 5,
    allowed_hosts: set[str] | None = None,
) -> Iterator[tuple[Any, str]]:
    """Open a GET request, pinning each hop to a checked public IP address."""
    current = str(url or "").strip()
    redirects = 0
    request_headers = {"User-Agent": "Quanta/0.1", "Accept": "*/*"}
    request_headers.update(headers or {})
    while True:
        target = resolve_public_url(current, allowed_hosts=allowed_hosts)
        connection = _make_connection(target, timeout)
        try:
            connection.request("GET", target.request_target, headers=request_headers)
            response = connection.getresponse()
        except BaseException:
            connection.close()
            raise

        location = response.headers.get("Location") if response.status in _REDIRECT_STATUSES else None
        if location:
            response.close()
            connection.close()
            if redirects >= max_redirects:
                raise ValueError("Превышен лимит перенаправлений")
            current = urllib.parse.urljoin(target.url, location)
            redirects += 1
            continue
        if response.status >= 400:
            connection.close()
            raise OSError(f"HTTP Error {response.status}: {response.reason}")
        try:
            yield response, target.url
        finally:
            response.close()
            connection.close()
        return


def safe_http_get(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
    max_bytes: int = 2_000_000,
    allowed_hosts: set[str] | None = None,
) -> tuple[bytes, str, str]:
    """Fetch a bounded response and return its bytes, content type and final URL."""
    with safe_open(url, headers=headers, timeout=timeout, allowed_hosts=allowed_hosts) as (response, final_url):
        length = response.headers.get("Content-Length")
        if length:
            try:
                declared_length = int(length)
            except ValueError as exc:
                raise ValueError("Некорректный размер HTTP-ответа") from exc
            if declared_length > max_bytes:
                raise ValueError("Ответ превышает допустимый размер")
        data = response.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("Ответ превышает допустимый размер")
        return data, response.headers.get("Content-Type", ""), final_url
