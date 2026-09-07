"""Offline integration tests for the actual relay (no Bright Data credentials)."""
from __future__ import annotations

import base64
import contextlib
import datetime
import http.server
import ipaddress
import json
import select
import socket
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from relay import ConnectRelay, ProxyConfig, parse_authority

USER = "test+user@example.test"
PASSWORD = "p:@ss/%?&+"


def make_certs(path: Path) -> tuple[Path, Path, Path]:
    """Generate short-lived test-only certs in a temp directory (no openssl.exe)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    root, cert, key = path / "root.pem", path / "server.pem", path / "server.key"
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Offline Test Root")])
    now = datetime.datetime.now(datetime.timezone.utc)
    ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - datetime.timedelta(minutes=5))
          .not_valid_after(now + datetime.timedelta(days=2))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
          .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
          .sign(ca_key, hashes.SHA256()))
    server = (x509.CertificateBuilder()
              .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "mock.test")]))
              .issuer_name(name).public_key(server_key.public_key())
              .serial_number(x509.random_serial_number())
              .not_valid_before(now - datetime.timedelta(minutes=5))
              .not_valid_after(now + datetime.timedelta(days=2))
              .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
              .add_extension(x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()), critical=False)
              .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
              .add_extension(x509.SubjectAlternativeName([
                  x509.DNSName("mock.test"), x509.DNSName("localhost"),
                  x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
              .sign(ca_key, hashes.SHA256()))
    root.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert.write_bytes(server.public_bytes(serialization.Encoding.PEM))
    key.write_bytes(server_key.private_bytes(serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return root, cert, key


class ThreadedServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass  # Tests intentionally abort connections and TLS handshakes.


class Origin(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen_proxy_auth: list[str | None] = []

    def do_GET(self):
        type(self).seen_proxy_auth.append(self.headers.get("Proxy-Authorization"))
        body = (b'<!doctype html><html><title>Relay integration test</title>'
                b'<body><h1 id="ready">RELAY_TEST_OK</h1>'
                b'<script>document.body.dataset.js="executed"</script></body></html>')
        status = 403 if self.path == "/forbidden" else 200
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


class MockUpstream(socketserver.BaseRequestHandler):
    expected = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
    seen_auth: list[str] = []
    force_status = 200
    origin_port = 0

    def handle(self):
        request = self.request
        request.settimeout(10)
        data = b""
        while b"\r\n\r\n" not in data:
            part = request.recv(4096)
            if not part:
                return
            data += part
            if len(data) > 65536:
                return
        header, leftover = data.split(b"\r\n\r\n", 1)
        lines = header.decode("ascii").split("\r\n")
        fields = dict(line.split(":", 1) for line in lines[1:] if ":" in line)
        auth = next((v.strip() for k, v in fields.items() if k.lower() == "proxy-authorization"), "")
        type(self).seen_auth.append(auth)
        status = self.force_status if auth == self.expected else 407
        if status != 200:
            request.sendall(
                f'HTTP/1.1 {status} Rejected\r\nProxy-Authenticate: Basic realm="mock"\r\n'
                'x-brd-error: client_10000\r\nContent-Length: 0\r\nConnection: close\r\n\r\n'.encode()
            )
            return
        if not (lines[0].startswith("CONNECT mock.test:443 ") or lines[0].startswith("CONNECT localhost:443 ")):
            request.sendall(b"HTTP/1.1 403 Test destination not allowed\r\nContent-Length: 0\r\n\r\n")
            return
        with socket.create_connection(("127.0.0.1", self.origin_port), timeout=10) as origin:
            request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            if leftover:
                origin.sendall(leftover)
            while True:
                ready, _, _ = select.select([request, origin], [], [], 10)
                if not ready:
                    return
                for source in ready:
                    part = source.recv(65536)
                    if not part:
                        return
                    (origin if source is request else request).sendall(part)


class Fixture:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.root, self.cert, self.key = make_certs(self.path)
        self.origin = ThreadedServer(("127.0.0.1", 0), Origin)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(self.cert, self.key)
        self.origin.socket = tls.wrap_socket(self.origin.socket, server_side=True)
        MockUpstream.origin_port = self.origin.server_address[1]
        MockUpstream.force_status = 200
        MockUpstream.seen_auth = []
        Origin.seen_proxy_auth = []
        self.upstream = ThreadedServer(("127.0.0.1", 0), MockUpstream)
        self.tls_upstream = ThreadedServer(("127.0.0.1", 0), MockUpstream)
        self.tls_upstream.socket = tls.wrap_socket(self.tls_upstream.socket, server_side=True)
        self.servers = [self.origin, self.upstream, self.tls_upstream]
        for server in self.servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()

    def config(self, **overrides):
        values = dict(host="127.0.0.1", port=self.upstream.server_address[1],
                      username=USER, password=PASSWORD, connect_timeout=3, idle_timeout=5)
        values.update(overrides)
        return ProxyConfig(**values)

    def close(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()
        self.temp.cleanup()


def connect_header(port: int, request: bytes | None = None):
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    sock.sendall(request or b"CONNECT mock.test:443 HTTP/1.1\r\nHost: mock.test:443\r\nProxy-Authorization: Basic DO_NOT_FORWARD\r\n\r\n")
    header = b""
    while not header.endswith(b"\r\n\r\n"):
        part = sock.recv(1)
        if not part:
            break
        header += part
    return sock, header


class RelayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = Fixture()

    @classmethod
    def tearDownClass(cls):
        cls.fixture.close()

    def setUp(self):
        MockUpstream.force_status = 200
        self.log = self.fixture.path / (self.id().split(".")[-1] + ".jsonl")

    def fetch(self, relay):
        sock, header = connect_header(relay.port)
        if b" 200 " not in header:
            sock.close()
        self.assertIn(b" 200 ", header)
        tls = ssl.create_default_context(cafile=str(self.fixture.root))
        with tls.wrap_socket(sock, server_hostname="mock.test") as encrypted:
            encrypted.sendall(b"GET / HTTP/1.1\r\nHost: mock.test\r\nConnection: close\r\n\r\n")
            body = b""
            while True:
                block = encrypted.recv(65536)
                if not block:
                    break
                body += block
        self.assertIn(b"RELAY_TEST_OK", body)

    def test_valid_auth_special_characters_and_tls_tunnel(self):
        with ConnectRelay(self.fixture.config(), self.log) as relay:
            self.fetch(relay)
        self.assertEqual(MockUpstream.seen_auth[-1], MockUpstream.expected)
        self.assertIsNone(Origin.seen_proxy_auth[-1])
        self.assertNotIn(PASSWORD, self.log.read_text())
        self.assertNotIn(USER, self.log.read_text())
        self.assertNotIn("mock.test", self.log.read_text())

    def test_shutdown_cancels_an_open_tunnel(self):
        relay = ConnectRelay(self.fixture.config(idle_timeout=60), self.log)
        with relay:
            sock, header = connect_header(relay.port)
            self.assertIn(b" 200 ", header)
        try:
            self.assertFalse(relay._thread.is_alive())
        finally:
            sock.close()

    def test_bad_password_returns_502_without_auth_challenge(self):
        with ConnectRelay(self.fixture.config(password="wrong"), self.log) as relay:
            sock, header = connect_header(relay.port)
            sock.close()
        self.assertIn(b" 502 ", header)
        self.assertNotIn(b"proxy-authenticate", header.lower())
        report = json.loads(self.log.read_text().splitlines()[0])
        self.assertEqual(report["status"], 407)
        self.assertIn("client_10000", report["error_codes"])

    def test_upstream_403_returns_502_not_fake_success(self):
        MockUpstream.force_status = 403
        with ConnectRelay(self.fixture.config(), self.log) as relay:
            sock, header = connect_header(relay.port)
            sock.close()
        self.assertIn(b" 502 ", header)
        self.assertNotIn(b"proxy-authenticate", header.lower())

    def test_https_upstream_with_explicit_ca(self):
        cfg = self.fixture.config(scheme="https", port=self.fixture.tls_upstream.server_address[1], extra_ca=str(self.fixture.root))
        with ConnectRelay(cfg, self.log) as relay:
            self.fetch(relay)

    def test_untrusted_https_upstream_is_rejected(self):
        cfg = self.fixture.config(scheme="https", port=self.fixture.tls_upstream.server_address[1])
        with ConnectRelay(cfg, self.log) as relay:
            sock, header = connect_header(relay.port)
            sock.close()
        self.assertIn(b" 502 ", header)
        self.assertIn("SSLCertVerificationError", self.log.read_text())

    def test_plain_http_is_explicitly_rejected(self):
        with ConnectRelay(self.fixture.config(), self.log) as relay:
            sock, header = connect_header(relay.port, b"GET http://mock.test/ HTTP/1.1\r\nHost: mock.test\r\n\r\n")
            sock.close()
        self.assertIn(b" 405 ", header)

    def test_missing_credentials_fail_before_listen(self):
        with self.assertRaises(ValueError):
            self.fixture.config(password="")

    def test_authority_validation(self):
        self.assertEqual(parse_authority("mock.test:443"), ("mock.test", 443))
        self.assertEqual(parse_authority("[::1]:443"), ("::1", 443))
        for value in ("a:0", "a:65536", "x@y:443", "a:443\r\nX:1", "https://a:443"):
            with self.assertRaises(ValueError):
                parse_authority(value)

    def test_unreachable_upstream_is_reported(self):
        with socket.socket() as unused:
            unused.bind(("127.0.0.1", 0))
            port = unused.getsockname()[1]
        with ConnectRelay(self.fixture.config(port=port), self.log) as relay:
            sock, header = connect_header(relay.port)
            sock.close()
        self.assertIn(b" 502 ", header)
        self.assertIn("ConnectionRefusedError", self.log.read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
