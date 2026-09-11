import io
import json
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


if __name__ == "__main__":
    unittest.main()
