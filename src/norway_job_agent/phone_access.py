"""Phone access: the same interface on a phone, served by this PC over home Wi-Fi.

Off until switched on under Settings & activity → Phone access. The PC keeps
doing the work (collection, local AI, Gmail, the database); the phone is a
second screen. A second listener on the PC's private network address serves
the same application, and requests through it follow stricter rules (see
web_api.guard):

* only devices on a private network (home Wi-Fi) are answered;
* the page never contains the desktop's session token;
* every API call needs a device token, issued once in exchange for a short
  pairing code shown on the PC (10 minutes, five wrong attempts end it);
* device tokens are stored only as SHA-256 hashes and can be removed;
* actions that happen on the PC itself (Gmail sign-in, the application
  browser, updates, shortcuts, managing phones) are refused.

Traffic on the home network is plain HTTP: use it on a network you trust.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

CONFIG_FILE = "phone-access.json"
PORT = 8765
PORT_TRIES = 10
CODE_SECONDS = 600
MAX_ATTEMPTS = 5
MAX_DEVICES = 10
ENVIRON_KEY = "norway_job_agent.phone"
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I/L


# Home and local networks only. Python's is_private also counts reserved
# ranges such as documentation addresses, so the list is explicit.
_HOME_NETWORKS = tuple(ipaddress.ip_network(net) for net in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16",
    "::1/128", "fc00::/7", "fe80::/10"))


def is_private(address: str) -> bool:
    """Home-network and loopback addresses; never the public internet."""
    try:
        ip = ipaddress.ip_address(str(address).split("%", 1)[0])
    except ValueError:
        return False
    ip = getattr(ip, "ipv4_mapped", None) or ip
    return any(ip.version == net.version and ip in net for net in _HOME_NETWORKS)


def lan_address() -> str | None:
    """This PC's address on its local network, or None without one.

    Connecting a UDP socket sends nothing; it only asks the system which
    interface would reach the internet.
    """
    for probe in ("192.0.2.1", "10.255.255.255"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect((probe, 80))
                address = sock.getsockname()[0]
        except OSError:
            continue
        if address and is_private(address) and not ipaddress.ip_address(address).is_loopback:
            return address
    return None


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _PhoneMarker:
    """WSGI wrapper for the phone listener: marks requests that came through it.

    Clients cannot set this environ key, unlike the Host header.
    """

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        environ[ENVIRON_KEY] = True
        return self.app(environ, start_response)


class PhoneAccess:
    def __init__(self, data_dir: Path, app, *, port: int = PORT, address_finder=lan_address):
        self.data_dir = Path(data_dir)
        self.app = app
        self.default_port = port
        self._find_address = address_finder
        self._lock = threading.RLock()
        self._server = None
        self._thread = None
        self._url = ""
        self._error = ""
        self._code = ""
        self._code_expires = 0.0
        self._attempts = 0
        self._touched: dict[str, float] = {}

    # --- saved state -----------------------------------------------------------
    def _path(self) -> Path:
        return self.data_dir / CONFIG_FILE

    def _read(self) -> dict:
        try:
            value = json.loads(self._path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            value = {}
        if not isinstance(value, dict):
            value = {}
        devices = [item for item in value.get("devices", []) if isinstance(item, dict)
                   and isinstance(item.get("id"), str) and isinstance(item.get("token_sha256"), str)]
        port = value.get("port")
        return {"enabled": value.get("enabled") is True, "devices": devices,
                "port": port if isinstance(port, int) and not isinstance(port, bool) and 1024 <= port <= 65535 else None}

    def _write(self, value: dict) -> None:
        path = self._path()
        partial = path.with_suffix(".tmp")
        partial.write_text(json.dumps(value, indent=2), encoding="utf-8")
        partial.replace(path)

    # --- listener --------------------------------------------------------------
    def start_saved(self) -> None:
        """Reopen phone access after a restart when it was on."""
        if self._read()["enabled"]:
            try:
                self.enable(new_code=False)
            except ValueError as exc:
                self._error = str(exc)

    def enable(self, *, new_code: bool = True) -> dict:
        with self._lock:
            if self._server is None:
                address = self._find_address()
                if not address:
                    raise ValueError("This PC is not connected to a home network, so a phone cannot reach it. "
                                     "Connect to your Wi-Fi or network cable and try again.")
                config = self._read()
                self._start(address, config["port"] or self.default_port)
                config.update(enabled=True, port=self._port)
                self._write(config)
            if new_code or not self._code_valid():
                self._new_code()
            return self.status()

    def _start(self, address: str, port: int) -> None:
        from waitress import create_server
        last = None
        for candidate in [port, *range(port + 1, port + PORT_TRIES)] if port else [0]:
            try:
                server = create_server(_PhoneMarker(self.app), host=address, port=candidate, threads=4,
                                       clear_untrusted_proxy_headers=True, max_request_body_size=16 * 1024 * 1024)
                break
            except OSError as exc:
                last = exc
        else:
            raise ValueError(f"Phone access could not open a network port ({last}). Close other apps using ports "
                             f"{port}–{port + PORT_TRIES - 1} and try again.")
        self._server = server
        self._port = int(server.effective_port)  # waitress reports it as text
        self._url = f"http://{address}:{self._port}"
        self._error = ""
        self.app.config["PHONE_ORIGIN"] = self._url
        self._thread = threading.Thread(target=server.run, name="phone-access", daemon=True)
        self._thread.start()

    def disable(self) -> dict:
        with self._lock:
            self._stop()
            config = self._read()
            config["enabled"] = False
            self._write(config)
            self._code = ""
            return self.status()

    def _stop(self) -> None:
        server, self._server = self._server, None
        self.app.config.pop("PHONE_ORIGIN", None)
        self._url = ""
        if server is not None:
            try:
                server.close()
            finally:
                if self._thread is not None:
                    self._thread.join(timeout=2)
                self._thread = None

    def close(self) -> None:
        with self._lock:
            self._stop()

    # --- pairing ---------------------------------------------------------------
    def _code_valid(self) -> bool:
        return bool(self._code) and time.monotonic() < self._code_expires and self._attempts < MAX_ATTEMPTS

    def _new_code(self) -> None:
        raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
        self._code = raw[:4] + "-" + raw[4:]
        self._code_expires = time.monotonic() + CODE_SECONDS
        self._attempts = 0

    def new_code(self) -> dict:
        with self._lock:
            if self._server is None:
                raise ValueError("Switch phone access on first.")
            self._new_code()
            return self.status()

    def pair(self, code: str, name: str) -> str:
        """Exchange the code shown on the PC for this device's token."""
        with self._lock:
            if not self._code_valid():
                raise PermissionError("There is no valid pairing code. On your PC open Settings & activity → "
                                      "Phone access and choose New code.")
            supplied = "".join(ch for ch in str(code or "").upper() if ch.isalnum())
            if not hmac.compare_digest(supplied, self._code.replace("-", "")):
                self._attempts += 1
                left = MAX_ATTEMPTS - self._attempts
                if left <= 0:
                    raise PermissionError("Too many wrong codes. On your PC choose New code, then try again.")
                raise PermissionError(f"That pairing code is not right. {left} attempt{'s' if left != 1 else ''} left.")
            config = self._read()
            if len(config["devices"]) >= MAX_DEVICES:
                raise PermissionError("Ten phones are already paired. Remove one on your PC first.")
            token = secrets.token_urlsafe(32)
            label = " ".join(str(name or "").split())[:60] or "Phone"
            config["devices"].append({"id": secrets.token_hex(6), "name": label, "token_sha256": _hash(token),
                                      "created_at": _now(), "last_seen": _now()})
            self._write(config)
            return token

    def device(self, token: str) -> dict | None:
        """The paired device a token belongs to, or None."""
        if not token or len(token) > 200:
            return None
        digest = _hash(token)
        with self._lock:
            config = self._read()
            found = next((item for item in config["devices"] if hmac.compare_digest(item["token_sha256"], digest)), None)
            if found is not None and time.monotonic() - self._touched.get(found["id"], 0) > 300:
                self._touched[found["id"]] = time.monotonic()
                found["last_seen"] = _now()
                self._write(config)
            return found

    def remove(self, device_id: str) -> dict:
        with self._lock:
            config = self._read()
            config["devices"] = [item for item in config["devices"] if item["id"] != device_id]
            self._write(config)
            return self.status()

    # --- what the PC's Settings page shows --------------------------------------
    def status(self) -> dict:
        with self._lock:
            config = self._read()
            running = self._server is not None
            code = self._code if running and self._code_valid() else ""
            link = f"{self._url}/?pair={code}" if code else self._url
            return {
                "enabled": config["enabled"], "running": running, "url": self._url, "error": self._error,
                "code": code, "code_seconds": max(0, int(self._code_expires - time.monotonic())) if code else 0,
                "qr_png": _qr_png(link) if running else "",
                "devices": [{key: item.get(key, "") for key in ("id", "name", "created_at", "last_seen")}
                            for item in config["devices"]],
            }


def _qr_png(text: str) -> str:
    """A QR code as a PNG data URI, or "" when segno is missing (the address still shows).

    A PNG, not inline SVG: the page's icon styles would restyle SVG strokes.
    """
    try:
        import segno
    except ImportError:
        return ""
    return segno.make(text, error="m").png_data_uri(scale=6, border=3, dark="#111111", light="#ffffff")
