"""Phone access over home Wi-Fi: pairing, the phone's stricter rules, PC-only actions."""

import json
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_job_agent import phone_access, service
from norway_job_agent.phone_access import ENVIRON_KEY, PhoneAccess, is_private
from norway_job_agent.web_api import create_app

PHONE = "http://192.168.1.23:8765"
LOCAL = "http://127.0.0.1:5000"


class Fixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        service.initialize(self.data)
        self.app = create_app(self.data, token="desktop-secret-token")
        self.app.config.update(LOCAL_ORIGIN=LOCAL, PHONE_ORIGIN=PHONE)
        self.access = PhoneAccess(self.data, self.app, port=0)
        self.app.config["PHONE_ACCESS"] = self.access
        self.client = self.app.test_client()

    def phone(self, method, path, *, token="", address="192.168.1.50", host="192.168.1.23:8765", **options):
        headers = {"Host": host, **({"X-Session-Token": token} if token else {}), **options.pop("headers", {})}
        return self.client.open(path, method=method, base_url=f"http://{host}", headers=headers,
                                environ_base={"REMOTE_ADDR": address, ENVIRON_KEY: True}, **options)

    def local(self, method, path, **options):
        return self.client.open(path, method=method, base_url=LOCAL,
                                headers={"X-Session-Token": "desktop-secret-token"}, **options)

    def paired(self):
        self.access._new_code()
        response = self.phone("POST", "/api/phone/pair", json={"code": self.access._code.lower(), "name": "Test phone"})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()["token"]


class PhoneRulesTests(Fixture):
    def test_the_phone_page_never_carries_the_desktop_token(self):
        page = self.phone("GET", "/").get_data(as_text=True)
        self.assertNotIn("desktop-secret-token", page)
        self.assertIn('data-remote="1"', page)
        self.assertIn('content=""', page)
        # A spoofed Host header naming the PC's own address changes nothing.
        spoofed = self.phone("GET", "/", host="127.0.0.1:5000")
        self.assertEqual(spoofed.status_code, 403)
        self.assertNotIn("desktop-secret-token", spoofed.get_data(as_text=True))
        # The desktop still gets its token.
        self.assertIn("desktop-secret-token", self.client.get("/", base_url=LOCAL).get_data(as_text=True))

    def test_an_unpaired_phone_gets_no_data(self):
        for path in ("/api/bootstrap", "/api/jobs?view=discover", "/api/profile"):
            response = self.phone("GET", path)
            self.assertEqual(response.status_code, 403, path)
            self.assertTrue(response.get_json()["pair"])
        self.assertEqual(self.phone("GET", "/api/bootstrap", token="desktop-secret-token").status_code, 403)
        self.assertEqual(self.phone("GET", "/api/bootstrap", token="made-up").status_code, 403)

    def test_only_home_network_devices_are_answered(self):
        for address in ("8.8.8.8", "203.0.113.9", "2001:4860:4860::8888"):
            self.assertEqual(self.phone("GET", "/", address=address).status_code, 403, address)
        self.assertTrue(all(is_private(a) for a in ("192.168.1.5", "10.0.0.2", "172.20.1.1", "fd12::1", "127.0.0.1")))
        self.assertFalse(any(is_private(a) for a in ("8.8.8.8", "100.64.0.1", "not an address")))
        other_site = self.phone("GET", "/api/bootstrap", headers={"Origin": "http://evil.example"})
        self.assertEqual(other_site.status_code, 403)

    def test_pairing_gives_a_device_token_stored_only_as_a_hash(self):
        token = self.paired()
        boot = self.phone("GET", "/api/bootstrap", token=token)
        self.assertEqual(boot.status_code, 200)
        self.assertEqual(boot.get_json()["client"], {"phone": True, "device": "Test phone"})
        saved = (self.data / phone_access.CONFIG_FILE).read_text(encoding="utf-8")
        self.assertNotIn(token, saved)
        self.assertEqual(json.loads(saved)["devices"][0]["name"], "Test phone")
        # The PC lists the phone (never its token); removing it locks the phone out at once.
        listed = self.local("GET", "/api/phone").get_json()
        self.assertEqual([item["name"] for item in listed["devices"]], ["Test phone"])
        self.assertNotIn(token, json.dumps(listed))
        removed = self.local("DELETE", "/api/phone/devices/" + listed["devices"][0]["id"])
        self.assertEqual(removed.get_json()["devices"], [])
        self.assertEqual(self.phone("GET", "/api/bootstrap", token=token).status_code, 403)

    def test_wrong_codes_run_out_and_codes_expire(self):
        self.access._new_code()
        for attempt in range(phone_access.MAX_ATTEMPTS):
            response = self.phone("POST", "/api/phone/pair", json={"code": "WRONG-CODE"})
            self.assertEqual(response.status_code, 403)
        self.assertIn("Too many", response.get_json()["error"])
        right = self.phone("POST", "/api/phone/pair", json={"code": self.access._code})
        self.assertEqual(right.status_code, 403)  # the code is used up
        self.access._new_code()
        with patch.object(phone_access.time, "monotonic", return_value=self.access._code_expires + 1):
            late = self.phone("POST", "/api/phone/pair", json={"code": self.access._code})
        self.assertEqual(late.status_code, 403)

    def test_pc_only_actions_are_refused_from_the_phone(self):
        token = self.paired()
        refused = [
            ("POST", "/api/tasks", {"action": "gmail-connect"}),
            ("POST", "/api/tasks", {"action": "shortcuts"}),
            ("POST", "/api/updates/apply", {"tag": "v9.9.9"}),
            ("POST", "/api/delivery/preview", {"job_id": 1}),
            ("POST", "/api/adzuna-key", {"app_id": "abcd1234", "app_key": "0123456789abcdef"}),
            ("GET", "/api/phone", None),
            ("POST", "/api/phone/code", {}),
        ]
        for method, path, body in refused:
            response = self.phone(method, path, token=token, json=body)
            self.assertEqual(response.status_code, 400, path)
            self.assertIn("happens on your PC", response.get_json()["error"], path)
        # Everyday work is allowed.
        self.assertEqual(self.phone("GET", "/api/jobs?view=discover", token=token).status_code, 200)
        self.assertEqual(self.phone("GET", "/api/profile", token=token).status_code, 200)

    def test_the_phone_can_be_added_to_its_home_screen(self):
        manifest = self.phone("GET", "/manifest.webmanifest")
        self.assertEqual(manifest.status_code, 200)
        self.assertEqual(manifest.get_json()["display"], "standalone")
        self.assertEqual(self.phone("GET", "/assets/icon-192.png").status_code, 200)


class ListenerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        service.initialize(self.data)
        self.app = create_app(self.data, token="desktop-secret-token")
        self.app.config["LOCAL_ORIGIN"] = LOCAL

    def access(self):
        access = PhoneAccess(self.data, self.app, port=0, address_finder=lambda: "127.0.0.1")
        self.app.config["PHONE_ACCESS"] = access
        self.addCleanup(access.close)
        return access

    def test_switching_on_opens_a_real_listener_that_follows_phone_rules_and_survives_a_restart(self):
        access = self.access()
        status = access.enable()
        self.assertTrue(status["running"])
        self.assertTrue(status["qr_png"].startswith("data:image/png;base64,"))
        self.assertRegex(status["code"], r"^[A-Z2-9]{4}-[A-Z2-9]{4}$")
        page = urllib.request.urlopen(status["url"] + "/", timeout=5).read().decode("utf-8")
        self.assertIn('data-remote="1"', page)
        self.assertNotIn("desktop-secret-token", page)
        request = urllib.request.Request(status["url"] + "/api/phone/pair", method="POST",
                                         data=json.dumps({"code": status["code"], "name": "Pixel"}).encode(),
                                         headers={"Content-Type": "application/json"})
        token = json.loads(urllib.request.urlopen(request, timeout=5).read())["token"]
        boot = urllib.request.Request(status["url"] + "/api/bootstrap", headers={"X-Session-Token": token})
        self.assertEqual(json.loads(urllib.request.urlopen(boot, timeout=5).read())["client"]["phone"], True)
        port = int(status["url"].rsplit(":", 1)[1])
        access.close()
        # Reopening the app turns phone access back on, on the same port.
        again = self.access()
        again.start_saved()
        self.assertEqual(again.status()["url"], f"http://127.0.0.1:{port}")
        again.disable()
        self.assertFalse(again.status()["running"])
        third = self.access()
        third.start_saved()
        self.assertFalse(third.status()["running"])

    def test_without_a_home_network_it_explains_instead_of_opening(self):
        access = PhoneAccess(self.data, self.app, port=0, address_finder=lambda: None)
        with self.assertRaisesRegex(ValueError, "not connected to a home network"):
            access.enable()


if __name__ == "__main__":
    unittest.main()
