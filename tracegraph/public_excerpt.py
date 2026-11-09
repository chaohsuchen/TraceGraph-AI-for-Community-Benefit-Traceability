"""Read text from public HTTPS sources before accepting research excerpts."""

import ipaddress
from contextlib import closing
from html.parser import HTMLParser
from http.client import HTTPException, HTTPSConnection
from io import BytesIO
from queue import Empty, Queue
import socket
import ssl
from threading import BoundedSemaphore, Thread, Timer
from time import monotonic
import unicodedata
from urllib.parse import quote, urljoin, urlsplit


_MAX_BYTES = 5 * 1024 * 1024
_MAX_PDF_PAGES = 40
_MAX_REDIRECTS = 3
_REDIRECTS = {301, 302, 303, 307, 308}
_DNS_SLOTS = BoundedSemaphore(4)
_GLOBAL_IPV6 = ipaddress.ip_network("2000::/3")
_IPV6_TRANSITION = ipaddress.ip_network("2002::/16")
_BLOCK_TAGS = frozenset(
    "address article aside blockquote br dd div dl dt figcaption figure footer h1 h2 h3 h4 h5 h6 "
    "header hr li main nav ol p pre section table tbody td th thead tr ul".split()
)
_HIDDEN_TAGS = {"head", "script", "style", "svg", "template", "noscript"}


def normalize_excerpt(text):
    """Normalize spacing and Unicode typography while preserving wording and case."""
    return " ".join(unicodedata.normalize("NFKC", text).replace("\u00ad", "").split())


def _remaining(deadline):
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError("Public source read timed out")
    return remaining


def _public_address(value):
    address = ipaddress.ip_address(value)
    if address.version == 6:
        if address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        elif address not in _GLOBAL_IPV6 or address in _IPV6_TRANSITION:
            return False
    return address.is_global and not address.is_multicast and not address.is_reserved


def _resolve(host, deadline):
    # System DNS can block beyond a socket timeout. Limit pending resolutions
    # and let the caller leave at the deadline even if the OS resolver stalls.
    result = Queue(maxsize=1)
    if not _DNS_SLOTS.acquire(timeout=_remaining(deadline)):
        raise TimeoutError("Public source resolution timed out")

    def resolve():
        try:
            result.put(socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
        except OSError as error:
            result.put(error)
        finally:
            _DNS_SLOTS.release()

    worker = Thread(target=resolve, daemon=True)
    try:
        worker.start()
    except RuntimeError:
        _DNS_SLOTS.release()
        raise
    try:
        resolved = result.get(timeout=_remaining(deadline))
    except Empty:
        raise TimeoutError("Public source resolution timed out") from None
    if isinstance(resolved, OSError):
        raise resolved
    if not resolved or any(not _public_address(row[4][0]) for row in resolved):
        raise ValueError("Public source resolved to a non-public address")
    return list(dict.fromkeys((family, protocol, address) for family, _, protocol, _, address in resolved))


class _PublicHTTPSConnection(HTTPSConnection):
    def __init__(self, host, addresses, deadline):
        self._tls_context = ssl.create_default_context()
        super().__init__(host, timeout=_remaining(deadline), context=self._tls_context)
        self._addresses = addresses
        self._deadline = deadline
        self._active_socket = None

    def connect(self):
        # Connect to the numeric address we validated instead of resolving the
        # hostname again. TLS still verifies the original URL hostname.
        for family, protocol, address in self._addresses:
            self._active_socket = socket.socket(family, socket.SOCK_STREAM, protocol)
            try:
                self._active_socket.settimeout(min(2, _remaining(self._deadline)))
                self._active_socket.connect(address)
            except OSError:
                self._active_socket.close()
                continue
            try:
                self._active_socket.settimeout(_remaining(self._deadline))
                self.sock = self._tls_context.wrap_socket(self._active_socket, server_hostname=self.host)
                self._active_socket = self.sock
                return
            except OSError:
                self._active_socket.close()
                raise
        raise OSError("Could not connect to the public source")

    def interrupt(self):
        if self._active_socket is not None:
            try:
                self._active_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _request(url, deadline):
    if "\\" in url or any(ord(character) < 32 or ord(character) == 127 for character in url):
        raise ValueError("Invalid public source URL")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or "%" in parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.port not in (None, 443)):
        raise ValueError("Public sources must use HTTPS without credentials")
    host = parsed.hostname.encode("idna").decode("ascii").rstrip(".")
    if "." not in host and ":" not in host:
        raise ValueError("Public source hostname must be fully qualified")
    target = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
    if parsed.query:
        target += "?" + quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~")
    addresses = _resolve(host, deadline)
    connection = _PublicHTTPSConnection(host, addresses, deadline)
    timer = Timer(_remaining(deadline), connection.interrupt)
    timer.daemon = True
    timer.start()
    try:
        with closing(connection):
            connection.request("GET", target, headers={"Accept-Encoding": "identity", "Connection": "close"})
            with connection.getresponse() as response:
                if response.status in _REDIRECTS:
                    location = response.getheader("Location")
                    if not location:
                        raise ValueError("Public source redirect is missing its destination")
                    return location, None, None
                encoding = response.getheader("Content-Encoding", "identity").strip().lower()
                if response.status != 200 or encoding != "identity":
                    return None, None, None
                body = response.read(_MAX_BYTES + 1)
                if len(body) > _MAX_BYTES:
                    raise ValueError("Public source exceeds the body limit")
                return None, response.headers, body
    except HTTPException as error:
        raise OSError("Invalid public source response") from error
    finally:
        timer.cancel()


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._hidden = []

    def handle_starttag(self, tag, _attributes):
        if tag in _HIDDEN_TAGS:
            self._hidden.append(tag)
        elif not self._hidden and tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if self._hidden:
            if tag == self._hidden[-1]:
                self._hidden.pop()
        elif tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self._hidden:
            self.parts.append(data)


def _pdf_text(body, deadline):
    try:
        from pypdf import PdfReader
        from pypdf.errors import PyPdfError
    except ImportError:
        return ""
    try:
        reader = PdfReader(BytesIO(body))
        if reader.is_encrypted:
            return ""
        parts = []
        for page in reader.pages[:_MAX_PDF_PAGES]:
            _remaining(deadline)
            parts.append(page.extract_text())
    except PyPdfError as error:
        raise ValueError("Public source PDF could not be read") from error
    return " ".join(parts)


def _decode_document(headers, body, deadline):
    media_type = headers.get_content_type()
    if media_type == "application/pdf" or body.startswith(b"%PDF-"):
        return _pdf_text(body, deadline)
    if media_type not in {"text/html", "application/xhtml+xml", "text/plain"}:
        return ""
    try:
        text = body.decode(headers.get_content_charset() or "utf-8-sig", errors="replace")
    except LookupError:
        text = body.decode("utf-8-sig", errors="replace")
    if media_type == "text/plain":
        return text
    parser = _VisibleText()
    parser.feed(text)
    parser.close()
    return "".join(parser.parts)


def load_public_text(url, *, timeout=8):
    """Return normalized text for a bounded public HTTPS document read."""
    if not isinstance(url, str):
        raise ValueError("Public source URL must be a string")
    deadline = monotonic() + timeout
    for _ in range(_MAX_REDIRECTS + 1):
        redirect, headers, body = _request(url, deadline)
        if redirect is None:
            if body is None:
                return ""
            text = _decode_document(headers, body, deadline)
            _remaining(deadline)
            return normalize_excerpt(text)
        url = urljoin(url, redirect)
    raise ValueError("Public source exceeded the redirect limit")
