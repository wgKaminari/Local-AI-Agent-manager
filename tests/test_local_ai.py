"""Local AI protocol, privacy boundary and output validation without live network."""

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_job_agent import local_ai
from norway_job_agent.profile import LIST_FIELDS, TEXT_FIELDS


LOCAL_DETAILS = {"details": {"format": "gguf", "family": "qwen3"}, "capabilities": ["completion"]}


def response(body, status=200):
    result = MagicMock()
    result.status = status
    data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    result.read.side_effect = io.BytesIO(data).read
    return result


def chat_result(content):
    return {"done": True, "done_reason": "stop", "message": {"role": "assistant", "content": json.dumps(content)}}


class LocalAITests(unittest.TestCase):
    def setUp(self):
        self.patch = patch.object(local_ai, "HTTPConnection")
        self.http = self.patch.start()
        self.addCleanup(self.patch.stop)
        self.connection = self.http.return_value
        self.profile = {"summary": "Built Python services.", "evidence": ["Built Python services."], "skills": ["Python"], "languages": {"Norwegian": "A1-A2"}, "target_roles": ["Norwegian B1 jobs"], "search_languages": {"Norwegian": "B1"}}
        self.job = {"title": "Developer", "company": "Example AS", "description": "Develop Python services. Norwegian B1 preferred.", "status": "saved"}
        self.draft = {"cover_letter": "Dear hiring team, I built Python services and would like to contribute.", "review_notes": [], "used_evidence": ["Built Python services."]}

    def replies(self, *bodies):
        self.connection.getresponse.side_effect = [response(body) for body in bodies]

    def requests(self):
        return [(call.args[0], call.args[1], json.loads(call.kwargs["body"]) if call.kwargs["body"] else None) for call in self.connection.request.call_args_list]

    def test_generation_uses_only_loopback_verified_model_and_factual_fields(self):
        self.replies(LOCAL_DETAILS, chat_result(self.draft))
        original = json.loads(json.dumps(self.profile))
        result = local_ai.generate_cover_letter(self.job, self.profile)
        self.assertEqual(result["cover_letter"], self.draft["cover_letter"])
        self.assertIn("Draft only", result["review_notes"][-1])
        self.assertEqual(self.profile, original)
        self.assertEqual(self.job["status"], "saved")
        for call in self.http.call_args_list:
            self.assertEqual(call.args, ("127.0.0.1", 11434))
        requests = self.requests()
        self.assertEqual([row[1] for row in requests], ["/api/show", "/api/chat"])
        self.assertNotIn("candidate", json.dumps(requests[0][2]))
        payload = requests[1][2]
        self.assertEqual(payload["model"], "qwen3:4b")
        self.assertFalse(payload["stream"])
        self.assertFalse(payload["think"])
        self.assertEqual(payload["options"]["temperature"], 0.2)
        self.assertLessEqual(payload["options"]["num_predict"], 2000)
        self.assertEqual(self.http.call_args.kwargs["timeout"], 240)
        facts = json.loads(payload["messages"][1]["content"])["candidate_facts"]
        self.assertNotIn("target_roles", facts)
        self.assertNotIn("search_languages", facts)
        self.assertEqual(facts["languages"], {"Norwegian": "A1-A2"})
        self.assertIn("UNTRUSTED DATA", payload["messages"][0]["content"])
        self.assertIn("authoritative over conflicting raw", payload["messages"][0]["content"])
        self.assertEqual(local_ai.validate_model_name("qwen3:4b"), "qwen3:4b")

    def test_cloud_and_url_model_names_are_rejected_before_http(self):
        for model in ("gpt-oss:120b-cloud", "model:cloud", "alias-CLOUD:latest", "https://remote.example/model", "model; command"):
            with self.subTest(model=model), self.assertRaises(ValueError):
                local_ai.generate_cover_letter(self.job, self.profile, model=model)
        self.http.assert_not_called()

    def test_remote_alias_metadata_blocks_personal_data_request(self):
        self.replies({**LOCAL_DETAILS, "remote_host": "https://ollama.com", "remote_model": "qwen3:4b"})
        with self.assertRaisesRegex(local_ai.LocalAIError, "remote host"):
            local_ai.generate_cover_letter(self.job, self.profile, model="innocent-alias")
        self.assertEqual([row[1] for row in self.requests()], ["/api/show"])
        self.assertNotIn("Built Python", str(self.connection.request.call_args))

    def test_models_list_filters_cloud_names_and_remote_aliases(self):
        self.replies({"models": [{"name": "qwen3:4b"}, {"name": "gpt-oss:120b-cloud"}, {"name": "alias"}, {"name": "remote-metadata", "remote_model": "remote"}]},
                     LOCAL_DETAILS, {**LOCAL_DETAILS, "remote_model": "remote"})
        self.assertEqual(local_ai.list_local_models(), ["qwen3:4b"])
        self.assertEqual([row[1] for row in self.requests()], ["/api/tags", "/api/show", "/api/show"])

    def test_missing_evidence_description_and_oversized_input_fail_before_http(self):
        for job, profile in (({}, self.profile), (self.job, {"skills": ["Python"]}), (self.job, {"cv_text": "x" * (local_ai.MAX_CV_CHARS + 1)})):
            with self.subTest(job=bool(job)), self.assertRaises(ValueError):
                local_ai.generate_cover_letter(job, profile)
        self.http.assert_not_called()

    def test_connection_error_is_actionable_and_does_not_leak_cv(self):
        self.connection.request.side_effect = OSError("SECRET CV CONTENT")
        with self.assertRaises(local_ai.LocalAIError) as caught:
            local_ai.generate_cover_letter(self.job, self.profile)
        self.assertIn("ollama pull qwen3:4b", str(caught.exception))
        self.assertIn("Install Ollama", str(caught.exception))
        self.assertNotIn("SECRET", str(caught.exception))
        self.connection.close.assert_called()

    def test_redirect_is_never_followed(self):
        self.connection.getresponse.return_value = response(b"SECRET", status=307)
        with self.assertRaisesRegex(local_ai.LocalAIError, "never follow redirects"):
            local_ai.list_local_models()
        self.assertEqual(self.connection.request.call_count, 1)
        self.assertEqual(self.http.call_count, 1)

    def test_malformed_output_and_fabricated_evidence_are_rejected(self):
        invalid_outputs = [
            {**self.draft, "used_evidence": ["Has a PhD and ten years of experience."]},
            {**self.draft, "review_notes": "not a list"},
            {**self.draft, "extra": "not permitted"},
        ]
        for invalid in invalid_outputs:
            with self.subTest(invalid=list(invalid)):
                self.replies(LOCAL_DETAILS, chat_result(invalid))
                with self.assertRaises(local_ai.LocalAIError):
                    local_ai.generate_cover_letter(self.job, self.profile)

    def test_language_upgrade_is_rejected(self):
        self.replies(LOCAL_DETAILS, chat_result({**self.draft, "cover_letter": "I speak Norwegian at B1 level."}))
        with self.assertRaisesRegex(local_ai.LocalAIError, "overstate"):
            local_ai.generate_cover_letter(self.job, self.profile)

    def test_truncated_response_is_rejected(self):
        self.replies(LOCAL_DETAILS, {**chat_result(self.draft), "done_reason": "length"})
        with self.assertRaisesRegex(local_ai.LocalAIError, "did not finish"):
            local_ai.generate_cover_letter(self.job, self.profile)

    def empty_suggestion(self):
        return {**{key: [] for key in LIST_FIELDS}, **{key: "" for key in TEXT_FIELDS}, "languages": {}}

    def test_profile_extraction_preserves_literal_levels_and_original_cv(self):
        cv = "  Alex Example\nBuilt Python services.\nNorwegian: A1-A2\nNationality: Ukrainian\n"
        suggestion = {**self.empty_suggestion(), "name": "Alex Example", "summary": "Built Python services.", "skills": ["Python"], "evidence": ["Built Python services."], "languages": {"Norwegian": "A1-A2"}}
        self.replies(LOCAL_DETAILS, chat_result(suggestion))
        result = local_ai.extract_profile(cv)
        self.assertEqual(result["languages"], {"Norwegian": "A1-A2"})
        self.assertEqual(result["work_authorization"], "")
        self.assertEqual(result["target_roles"], [])
        self.assertEqual(result["cv_text"], cv)
        self.assertEqual(set(result), {*LIST_FIELDS, *TEXT_FIELDS, "languages"})

    def test_profile_extraction_rejects_invented_permission_or_language_level(self):
        for additions in ({"work_authorization": "Authorized to work in Norway"}, {"languages": {"Norwegian": "B1"}}):
            self.replies(LOCAL_DETAILS, chat_result({**self.empty_suggestion(), **additions}))
            with self.subTest(additions=additions), self.assertRaises(local_ai.LocalAIError):
                local_ai.extract_profile("Nationality: Ukrainian. Norwegian: A1-A2.")

    def test_profile_keeps_verified_excerpts_and_reports_omitted_paraphrases(self):
        suggestion = {**self.empty_suggestion(), "summary": "Expert in artificial intelligence.",
                      "skills": ["Python", "Kubernetes"], "evidence": ["Built Python services."]}
        self.replies(LOCAL_DETAILS, chat_result(suggestion))
        result = local_ai.extract_profile("Built Python services.")
        self.assertEqual(result["summary"], "")
        self.assertEqual(result["skills"], ["Python"])
        self.assertEqual(result["evidence"], ["Built Python services."])
        self.assertIn("summary", result["_review_notes"][0])
        self.assertIn("skills", result["_review_notes"][0])

    def test_error_json_and_timeout_never_echo_server_content(self):
        self.replies({"error": "SECRET CV CONTENT"})
        with self.assertRaises(local_ai.LocalAIError) as caught:
            local_ai.list_local_models()
        self.assertNotIn("SECRET", str(caught.exception))
        self.connection.request.side_effect = TimeoutError("SECRET CV CONTENT")
        with self.assertRaises(local_ai.LocalAIError) as caught:
            local_ai.list_local_models()
        self.assertIn("timed out", str(caught.exception))
        self.assertNotIn("SECRET", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
