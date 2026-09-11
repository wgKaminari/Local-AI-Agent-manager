"""Regressions for setup discovery and review-only import boundaries."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from norway_job_agent import local_ai, runtime_setup, service
from norway_job_agent.profile import LIST_FIELDS, TEXT_FIELDS


class SetupDiscoveryTests(unittest.TestCase):
    def test_embedding_model_does_not_hide_installed_chat_model(self):
        responses = [
            {"models": [{"name": "nomic-embed-text:latest"}, {"name": "qwen3:4b"}]},
            {"details": {"format": "gguf"}, "capabilities": ["embedding"]},
            {"details": {"format": "gguf"}, "capabilities": ["completion"]},
        ]
        with patch.object(local_ai, "_request", side_effect=responses):
            self.assertEqual(local_ai.list_local_models(), ["qwen3:4b"])

    def test_direct_generation_still_rejects_embedding_models_before_sending_cv(self):
        with patch.object(local_ai, "_request", return_value={"details": {"format": "gguf"}, "capabilities": ["embedding"]}) as request:
            with self.assertRaisesRegex(local_ai.LocalAIError, "does not support text generation"):
                local_ai.generate_cover_letter({"title": "Developer", "description": "Build tools"}, {"summary": "Sensitive candidate fact"}, model="nomic-embed-text")
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[0], "/api/show")
        self.assertNotIn("Sensitive candidate fact", str(request.call_args))

    def test_missing_tag_is_recognized_after_successful_download(self):
        response = MagicMock(status=200)
        response.readline.return_value = b'{"status":"success"}\n'
        connection = MagicMock()
        connection.getresponse.return_value = response
        with patch.object(runtime_setup, "HTTPConnection", return_value=connection), patch.object(runtime_setup, "list_local_models", return_value=["qwen3:latest"]), patch.object(runtime_setup, "pdf_available", return_value=True):
            self.assertEqual(runtime_setup.download_model("qwen3"), ["qwen3:latest"])
            self.assertTrue(runtime_setup.setup_status("qwen3")["ready"])
            self.assertFalse(runtime_setup.setup_status("qwen3:4b")["ready"])


class EmailReviewPersistenceTests(unittest.TestCase):
    def test_invalid_metadata_is_rejected_before_any_candidate_is_saved(self):
        base = {"title": "AI Engineer", "company": "Example", "source": "gmail", "source_url": "https://example.com/jobs/1",
                "raw_json": {"email": {"message_id": "1"}}}
        invalid = ([1], "invalid JSON", "[]", {"email": None}, {"email": [1]}, {"email": {"value": {1, 2}}})
        for bad in invalid:
            with self.subTest(metadata_type=type(bad).__name__), tempfile.TemporaryDirectory() as directory:
                data = Path(directory)
                service.initialize(data)
                with self.assertRaisesRegex(ValueError, "metadata"):
                    service.import_email_candidates(data, [base, {**base, "source_url": "https://example.com/jobs/2", "raw_json": bad}])
                self.assertEqual(service.vacancies(data), [])

    def test_repeated_email_import_fills_gaps_preserves_drafts_and_distinct_messages(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            service.initialize(data)
            job_id, _ = service.import_vacancy(data, {"title": "AI Engineer", "company": "Employer", "description": "Full employer description",
                "source": "company", "source_id": "123", "source_url": "https://example.com/jobs/123"})
            service.update_workflow(data, job_id, "preparing", "Personal note")
            service.save_edited_letter(data, job_id, "My reviewed draft")
            alert = {"title": "Short AI title", "company": "Short company name", "location": "Oslo, Norway",
                     "description": "Partial alert excerpt", "source": "gmail", "source_id": "email-job-123",
                     "source_url": "https://example.com/jobs/123?utm_source=alert", "raw_json": {"email": {"message_id": "email-1"}}}
            for message_id in ("email-1", "email-1", "email-2"):
                report = service.import_email_candidates(data, [{**alert, "raw_json": json.dumps({"email": {"message_id": message_id}})}])
                self.assertEqual(report["existing"], 1)
            saved = service.get_vacancy(data, job_id)
            self.assertEqual(saved["title"], "AI Engineer")
            self.assertEqual(saved["company"], "Employer")
            self.assertEqual(saved["location"], "Oslo, Norway")
            self.assertEqual(saved["description"], "Full employer description")
            self.assertEqual(saved["status"], "preparing")
            self.assertEqual(saved["notes"], "Personal note")
            self.assertEqual(saved["source"], "company")
            self.assertEqual(saved["source_id"], "123")
            self.assertEqual(len(service.draft_history(data, job_id)), 1)
            self.assertEqual(service.draft_history(data, job_id)[0]["content"], "My reviewed draft")
            self.assertEqual({p["message_id"] for p in saved["provenance"]}, {"email-1", "email-2"})


class PartialProfileReviewTests(unittest.TestCase):
    def test_omitting_unsupported_prose_does_not_accept_invented_language_or_permission(self):
        empty = {**{key: [] for key in LIST_FIELDS}, **{key: "" for key in TEXT_FIELDS}, "languages": {}}
        for dangerous in ({"languages": {"Norwegian": "B1"}}, {"work_authorization": "Authorized to work in Norway"}):
            with self.subTest(fields=dangerous), patch.object(local_ai, "_chat", return_value={**empty, "summary": "AI expert", **dangerous}), self.assertRaises(local_ai.LocalAIError):
                local_ai.extract_profile("Built Python services. Norwegian: A1-A2. Nationality: Ukrainian.")


if __name__ == "__main__":
    unittest.main()
