from email.message import Message
import socket
from time import monotonic
from unittest import TestCase
from unittest.mock import patch

from tracegraph.public_excerpt import (
    _PublicHTTPSConnection,
    load_public_text,
    normalize_excerpt,
)


class PublicExcerptTests(TestCase):
    def test_text_normalization_retains_wording_and_case(self):
        self.assertEqual(normalize_excerpt(" A\u00a0ﬁnding\n citi\u00adzen! "), "A finding citizen!")

    def test_redirect_and_html_extraction(self):
        headers = Message()
        headers["Content-Type"] = "text/html; charset=utf-8"
        html = (
            b"<head><title>Hidden metadata</title></head>"
            b"<main><p>We will <strong>open</strong> the center.</p>"
            b"<script>Do not use this text.</script><p>By May&nbsp;2027.</p></main>"
        )
        with patch("tracegraph.public_excerpt._request", side_effect=[
            ("/promise", None, None), (None, headers, html),
        ]) as request:
            text = load_public_text("https://example.org/start")
        self.assertEqual(text, "We will open the center. By May 2027.")
        self.assertEqual([call.args[0] for call in request.call_args_list], [
            "https://example.org/start", "https://example.org/promise",
        ])
        self.assertEqual(request.call_args_list[0].args[1], request.call_args_list[1].args[1])

    def test_invalid_url_rejected_without_resolution(self):
        urls = (
            "http://example.org", "https://user@example.org", "https://example.org:8443",
            "https://example.org\\.test", "https://example.org/path\r\n", "https://localhost",
        )
        with patch("tracegraph.public_excerpt.socket.getaddrinfo") as resolve:
            for url in urls:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    load_public_text(url)
        resolve.assert_not_called()

    def test_nonpublic_resolution_blocks_connection(self):
        public = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443))
        private = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 443))
        with (
            patch("tracegraph.public_excerpt.socket.getaddrinfo", return_value=[public, private]),
            patch("tracegraph.public_excerpt._PublicHTTPSConnection") as connect,
            self.assertRaises(ValueError),
        ):
            load_public_text("https://example.org")
        connect.assert_not_called()

    def test_numeric_connection_keeps_original_tls_hostname(self):
        address = ("93.184.216.34", 443)
        with (
            patch("tracegraph.public_excerpt.ssl.create_default_context") as context,
            patch("tracegraph.public_excerpt.socket.socket") as sockets,
        ):
            connection = _PublicHTTPSConnection(
                "example.org", [(socket.AF_INET, socket.IPPROTO_TCP, address)], monotonic() + 2,
            )
            connection.connect()
        sockets.return_value.connect.assert_called_once_with(address)
        context.return_value.wrap_socket.assert_called_once_with(sockets.return_value, server_hostname="example.org")
