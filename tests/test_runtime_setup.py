import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from norway_job_agent import runtime_setup, service


class RuntimeSetupTests(unittest.TestCase):
    def test_installer_uses_current_python_without_shell(self):
        with patch.object(runtime_setup.subprocess, "run", return_value=MagicMock(returncode=0)) as run, patch.object(runtime_setup, "pdf_available", return_value=True):
            self.assertIn("ready", runtime_setup.install_pdf_support())
        self.assertEqual(run.call_args.args[0][0], sys.executable)
        self.assertEqual(run.call_args.args[0][1:4], ["-m", "pip", "install"])
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_download_progress_then_verifies_local_model(self):
        body = io.BytesIO(b'{"status":"pulling","total":100,"completed":50}\n{"status":"success"}\n')
        response = MagicMock(status=200)
        response.readline.side_effect = body.readline
        connection = MagicMock()
        connection.getresponse.return_value = response
        messages = []
        with patch.object(runtime_setup, "HTTPConnection", return_value=connection) as http, patch.object(runtime_setup, "list_local_models", return_value=["qwen3:4b"]):
            self.assertEqual(runtime_setup.download_model("qwen3:4b", messages.append), ["qwen3:4b"])
        self.assertEqual(http.call_args.args, ("127.0.0.1", 11434))
        self.assertEqual(connection.request.call_args.args[:2], ("POST", "/api/pull"))
        self.assertIn("50%", messages[0])

    def test_incomplete_model_download_is_not_reported_as_ready(self):
        response = MagicMock(status=200)
        response.readline.return_value = b""
        with patch.object(runtime_setup, "HTTPConnection") as http:
            http.return_value.getresponse.return_value = response
            with self.assertRaisesRegex(RuntimeError, "did not finish"):
                runtime_setup.download_model("qwen3:4b")

    def test_remote_model_name_is_rejected_before_download(self):
        with patch.object(runtime_setup, "HTTPConnection") as http:
            with self.assertRaises(ValueError):
                runtime_setup.download_model("something:cloud")
            http.assert_not_called()

    def test_real_text_pdf_import(self):
        try:
            from pypdf import PdfWriter
            from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
        except ImportError:
            self.skipTest("pypdf is not installed")
        writer = PdfWriter()
        page = writer.add_blank_page(width=600, height=800)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})})})
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 50 750 Td (Sample Candidate - Python AI Engineer) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample-cv.pdf"
            writer.write(path)
            self.assertIn("Sample Candidate - Python AI Engineer", service.import_cv_text(path))


class EmailPersistenceTests(unittest.TestCase):
    def test_alert_dedup_preserves_full_posting_and_retains_provenance(self):
        from norway_job_agent.storage import JobStore
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            service.initialize(data)
            job_id, _ = service.import_vacancy(data, {"title": "AI Engineer", "company": "Actual Company", "description": "Complete employer description", "source": "company", "source_id": "role-123", "source_url": "https://example.com/jobs/123"})
            service.update_workflow(data, job_id, "saved", "My notes")
            alert = {"title": "Short alert title", "company": "Unknown in alert", "description": "Short email excerpt", "source": "gmail", "source_id": "msg123:role123", "source_url": "https://example.com/jobs/123?utm_source=email", "raw_json": {"email": {"message_id": "msg123", "subject": "Your job alert"}}}
            report = service.import_email_candidates(data, [alert])
            self.assertEqual(report["existing"], 1)
            service.import_email_candidates(data, [alert])
            job = service.get_vacancy(data, job_id)
            self.assertEqual(job["description"], "Complete employer description")
            self.assertEqual(job["company"], "Actual Company")
            self.assertEqual(job["status"], "saved")
            self.assertEqual(job["notes"], "My notes")
            self.assertEqual(len(job["provenance"]), 1)
            with JobStore(data / "vacancies.db") as store:
                store.withdraw_source_job("company", "role-123")
            service.import_email_candidates(data, [alert])
            self.assertEqual(service.get_vacancy(data, job_id)["is_active"], 0)



class LocalAIStartupTests(unittest.TestCase):
    def test_installed_but_stopped_ollama_is_started_before_drafting(self):
        updates = []
        with patch.object(runtime_setup, "_running", return_value=False),                 patch.object(runtime_setup, "ollama_executable", return_value="ollama.exe"),                 patch.object(runtime_setup, "start_ollama") as start:
            runtime_setup.ensure_ollama(updates.append)
        start.assert_called_once_with()
        self.assertEqual(updates, ["Starting Ollama on this computer…"])

    def test_running_or_missing_ollama_is_left_alone(self):
        for running, executable in ((True, "ollama.exe"), (False, None)):
            with self.subTest(running=running), patch.object(runtime_setup, "_running", return_value=running),                     patch.object(runtime_setup, "ollama_executable", return_value=executable),                     patch.object(runtime_setup, "start_ollama") as start:
                runtime_setup.ensure_ollama()
            start.assert_not_called()

    def test_slow_start_is_awaited_for_up_to_a_minute(self):
        clock = iter(range(0, 200, 5))
        checks = iter([False] + [False] * 8 + [True])
        with patch.object(runtime_setup, "_running", side_effect=lambda: next(checks)),                 patch.object(runtime_setup, "ollama_executable", return_value="ollama.exe"),                 patch.object(runtime_setup.subprocess, "Popen"), patch.object(runtime_setup.time, "sleep"),                 patch.object(runtime_setup.time, "monotonic", side_effect=lambda: next(clock)):
            self.assertIn("running", runtime_setup.start_ollama())

    def test_speed_advice_names_low_memory(self):
        self.assertIsNone(runtime_setup.speed_advice(None))
        self.assertIsNone(runtime_setup.speed_advice(12.0, 0.5))
        self.assertIn("swapping", runtime_setup.speed_advice(1.4, 0.3))
        self.assertIn("qwen3:1.7b", runtime_setup.speed_advice(2.0, 8.0))

    def test_drafting_starts_ollama_and_reports_slow_generation(self):
        from norway_job_agent import service
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            service.initialize(data)
            profile = service.read_profile(data)
            profile.update(summary="Built Python services.", evidence=["Built Python services."])
            service.save_profile(data, profile)
            job_id, _ = service.import_vacancy(data, {"title": "Developer", "company": "Fixture", "source": "manual",
                                                      "source_url": "https://jobs.example.test/1", "description": "Python work."})
            order = []
            with patch.object(runtime_setup, "ensure_ollama", side_effect=lambda progress=None: order.append("start")),                     patch.object(runtime_setup, "memory_available_gb", return_value=0.4),                     patch("norway_job_agent.local_ai.generate_cover_letter",
                          side_effect=lambda *a, **k: order.append("draft") or {"cover_letter": "Draft", "review_notes": [], "used_evidence": []}),                     patch("norway_job_agent.local_ai.last_tokens_per_second", return_value=1.4):
                result = service.write_letter(data, job_id, persist=False)
        self.assertEqual(order, ["start", "draft"])
        self.assertIn("1.4 tokens per second", result["performance_note"])

if __name__ == "__main__":
    unittest.main()
