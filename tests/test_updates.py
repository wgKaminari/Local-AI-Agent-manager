"""Update checks and the hand-over to install.ps1, without network or real processes."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_job_agent import updates
from norway_job_agent.sources import SourceError


def release(tag, body="Faster lists."):
    return 200, {}, json.dumps({"tag_name": tag, "body": body, "html_url": f"https://github.com/x/releases/{tag}"}).encode(), ""


class UpdateCheckTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.folder, ignore_errors=True))

    def installed(self):
        app = self.folder / "app"
        app.mkdir()
        (self.folder / "install.json").write_text("{}", encoding="utf-8")
        (app / "install.ps1").write_text("# installer", encoding="utf-8")
        return patch("norway_job_agent.updates.checkout", return_value=app)

    def test_versions_compare_numerically(self):
        self.assertEqual(updates.parse_version("v0.10.0"), (0, 10, 0))
        self.assertGreater(updates.parse_version("v0.10.0"), updates.parse_version("0.9.9"))
        self.assertIsNone(updates.parse_version("latest"))

    def test_a_newer_release_on_an_installed_copy_can_update(self):
        with self.installed(), patch("norway_job_agent.updates.__version__", "0.7.0"), \
                patch("norway_job_agent.sources._fetch", return_value=release("v0.7.1")), \
                patch("norway_job_agent.updates.sys.platform", "win32"):
            result = updates.check()
        self.assertEqual((result["latest"], result["newer"], result["can_update"], result["kind"]), ("0.7.1", True, True, "installed"))
        self.assertEqual(result["notes"], "Faster lists.")

    def test_same_version_missing_release_and_offline(self):
        with self.installed(), patch("norway_job_agent.updates.__version__", "0.7.1"):
            with patch("norway_job_agent.sources._fetch", return_value=release("v0.7.1")):
                self.assertFalse(updates.check()["newer"])
            with patch("norway_job_agent.sources._fetch", return_value=(404, {}, b'{"message": "Not Found"}', "")):
                self.assertIn("No release", updates.check()["reason"])
            with patch("norway_job_agent.sources._fetch", side_effect=SourceError("offline")):
                with self.assertRaisesRegex(ValueError, "Could not reach GitHub"):
                    updates.check()

    def test_a_plain_copy_is_told_to_install_once(self):
        with patch("norway_job_agent.updates.checkout", return_value=self.folder), \
                patch("norway_job_agent.updates.__version__", "0.7.0"), \
                patch("norway_job_agent.sources._fetch", return_value=release("v0.7.1")), \
                patch("norway_job_agent.updates.sys.platform", "win32"):
            result = updates.check()
        self.assertEqual(result["kind"], "source")
        self.assertFalse(result["can_update"])
        self.assertIn("irm https://raw.githubusercontent.com/", result["install_command"])

    def test_a_git_checkout_updates_only_without_local_changes(self):
        (self.folder / ".git").mkdir()
        done = lambda out="": subprocess.CompletedProcess([], 0, out, "")
        with patch("norway_job_agent.updates.checkout", return_value=self.folder), \
                patch("norway_job_agent.updates.shutil.which", return_value="git"):
            with patch("norway_job_agent.updates._git", side_effect=[done(" M src/app.py\n"), done("origin/main")]):
                self.assertIn("local changes", updates.git_blocker(str(self.folder)))
            with patch("norway_job_agent.updates._git", side_effect=[done(""), done("origin/main")]):
                self.assertEqual(updates.git_blocker(str(self.folder)), "")

    def test_installed_update_runs_a_copy_of_the_installer_for_that_release(self):
        with self.installed(), patch("norway_job_agent.updates.sys.platform", "win32"), \
                patch("norway_job_agent.updates.subprocess.Popen") as popen:
            result = updates.start_update("v0.7.1")
        command = popen.call_args.args[0]
        self.assertEqual(result["kind"], "installed")
        self.assertEqual(command[:6], ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", command[5]])
        self.assertNotEqual(Path(command[5]).parent, self.folder / "app")  # Runs from a temporary copy.
        self.assertEqual(Path(command[5]).read_text(encoding="utf-8"), "# installer")
        self.assertIn("-WaitPid", command)
        self.assertEqual(command[command.index("-InstallRoot") + 1], str(self.folder))
        self.assertEqual(command[command.index("-Tag") + 1], "v0.7.1")

    def test_git_update_pulls_with_the_console_python(self):
        (self.folder / ".git").mkdir()
        (self.folder / "install.ps1").write_text("# installer", encoding="utf-8")
        with patch("norway_job_agent.updates.checkout", return_value=self.folder), \
                patch("norway_job_agent.updates.sys.platform", "win32"), \
                patch("norway_job_agent.updates.git_blocker", return_value=""), \
                patch("norway_job_agent.updates.sys.executable", r"C:\Python312\pythonw.exe"), \
                patch("norway_job_agent.updates.subprocess.Popen") as popen:
            updates.start_update("v0.7.1")
        command = popen.call_args.args[0]
        self.assertEqual(command[command.index("-Checkout") + 1], str(self.folder))
        self.assertEqual(Path(command[command.index("-Python") + 1]).name, "python.exe")

    def test_updates_refuse_bad_versions_and_plain_copies(self):
        with patch("norway_job_agent.updates.sys.platform", "win32"):
            with self.assertRaisesRegex(ValueError, "release version"):
                updates.start_update("latest; rm")
            with patch("norway_job_agent.updates.checkout", return_value=self.folder):
                (self.folder / "install.ps1").write_text("#", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "not installed with the installer"):
                    updates.start_update("v0.7.1")


class UpdateApiTests(unittest.TestCase):
    def test_apply_starts_the_installer_then_closes_the_app(self):
        from norway_job_agent.web_api import close_resources, create_app
        folder = tempfile.mkdtemp()
        app = create_app(Path(folder), "token")
        app.config.update(TESTING=True, LOCAL_ORIGIN="http://127.0.0.1:7654")
        closed = []
        app.config["QUIT_APP"] = lambda: closed.append(True)
        client = app.test_client()
        headers = {"X-Session-Token": "token", "Origin": "http://127.0.0.1:7654"}
        try:
            boot = client.get("/api/bootstrap", headers=headers, base_url="http://127.0.0.1:7654").get_json()
            self.assertFalse(boot["update_check"])  # Tests and previews never contact GitHub.
            with patch("norway_job_agent.updates.start_update", return_value={"started": True}) as start, \
                    patch("norway_job_agent.web_api.threading.Timer") as timer:
                response = client.post("/api/updates/apply", json={"tag": "v0.7.1"}, headers=headers, base_url="http://127.0.0.1:7654")
                timer.return_value.start.assert_called_once()
                timer.call_args.args[1]()
            self.assertEqual(response.status_code, 200)
            start.assert_called_once_with("v0.7.1")
            self.assertEqual(closed, [True])
        finally:
            close_resources(app)
            app.extensions["local_state"].worker.shutdown(wait=True)


if __name__ == "__main__":
    unittest.main()
