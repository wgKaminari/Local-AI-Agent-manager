"""Cloud AI providers with your own key: storage, choice, streaming, limits. No live network."""

import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_job_agent import cloud_ai, local_ai

KEY = "sk-ant-api03-FictionalTestKey-0123456789abcdef"


class FakeResponse(io.BytesIO):
    """What urllib returns: readable, line by line, closable."""


def sse(*events, done=False):
    lines = [f"data: {json.dumps(event)}\n\n" for event in events]
    if done:
        lines.append("data: [DONE]\n\n")
    return FakeResponse("".join(lines).encode("utf-8"))


def http_error(status, body):
    return urllib.error.HTTPError("https://api.example.test", status, "error", {}, io.BytesIO(json.dumps(body).encode()))


class CloudAITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data = Path(self.temporary.name)
        self.requests = []
        self.replies = []
        opener = patch.object(cloud_ai._OPENER, "open", side_effect=self.fake_open)
        opener.start()
        self.addCleanup(opener.stop)
        pause = patch.object(cloud_ai.time, "sleep")
        pause.start()
        self.addCleanup(pause.stop)

    def fake_open(self, request, timeout=None):
        body = json.loads(request.data.decode("utf-8")) if request.data else None
        self.requests.append({"url": request.full_url, "method": request.get_method(),
                              "headers": {key.lower(): value for key, value in request.header_items()}, "body": body})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def add_key(self, provider="anthropic", key=KEY, models=None):
        listing = {"anthropic": {"data": [{"id": "claude-opus-5-5", "display_name": "Claude Opus 5.5"},
                                          {"id": "claude-haiku-5-5", "display_name": "Claude Haiku 5.5"}], "has_more": False},
                   "openai": {"data": [{"id": "gpt-6.1-terra", "created": 2}, {"id": "text-embedding-3-large", "created": 3},
                                       {"id": "gpt-6-luna", "created": 1}]},
                   "gemini": {"models": [{"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]},
                                         {"name": "models/gemini-embedding-001", "supportedGenerationMethods": ["embedContent"]}]},
                   "deepseek": {"data": [{"id": "deepseek-flash"}, {"id": "deepseek-v4-pro"}]}}[provider]
        self.replies.append(FakeResponse(json.dumps(models or listing).encode()))
        return cloud_ai.save_key(self.data, provider, key)

    def choose(self, provider="anthropic"):
        return cloud_ai.save_settings(self.data, {"provider": provider, "consent": True})

    # ------------------------------------------------------------ keys

    def test_a_key_is_checked_saved_encrypted_and_never_shown_again(self):
        result = self.add_key()
        self.assertEqual(self.requests[0]["url"], "https://api.anthropic.com/v1/models?limit=100")
        self.assertEqual(self.requests[0]["headers"]["x-api-key"], KEY)
        self.assertEqual(self.requests[0]["headers"]["anthropic-version"], "2023-06-01")
        saved = (self.data / cloud_ai.KEY_FILE).read_text(encoding="utf-8")
        self.assertNotIn(KEY, saved)
        self.assertNotIn(KEY, json.dumps(result))
        claude = next(p for p in result["providers"] if p["id"] == "anthropic")
        self.assertTrue(claude["configured"])
        self.assertEqual(claude["hint"], "…" + KEY[-4:])
        self.assertEqual(claude["model"], "claude-opus-5-5")
        self.assertEqual([m["id"] for m in result["models"]["anthropic"]], ["claude-opus-5-5", "claude-haiku-5-5"])
        self.assertEqual(cloud_ai._key(self.data, "anthropic"), KEY)
        # Saving a key alone does not switch the app to a paid provider.
        self.assertEqual(result["provider"], "local")

    def test_a_rejected_key_is_not_saved_and_the_error_hides_it(self):
        self.replies.append(http_error(401, {"error": {"message": f"Incorrect API key provided: {KEY}"}}))
        with self.assertRaises(cloud_ai.CloudAIError) as caught:
            cloud_ai.save_key(self.data, "anthropic", KEY)
        self.assertIn("did not accept the API key", str(caught.exception))
        self.assertNotIn(KEY, str(caught.exception))
        self.assertFalse((self.data / cloud_ai.KEY_FILE).exists())
        with self.assertRaises(ValueError):
            cloud_ai.save_key(self.data, "anthropic", "short")
        with self.assertRaises(ValueError):
            cloud_ai.save_key(self.data, "copilot", KEY)

    def test_model_lists_keep_chat_models_only(self):
        self.assertEqual([m["id"] for m in self.add_key("openai", "sk-proj-FictionalOpenAIKey-0123456789")["models"]["openai"]],
                         ["gpt-6.1-terra", "gpt-6-luna"])
        self.assertEqual([m["id"] for m in self.add_key("gemini", "AIzaFictionalGeminiKey0123456789")["models"]["gemini"]],
                         ["gemini-3.8-flash"])
        self.assertEqual(self.requests[-1]["headers"]["x-goog-api-key"], "AIzaFictionalGeminiKey0123456789")
        self.assertNotIn("key=", self.requests[-1]["url"])  # The key never goes into the address.
        self.assertEqual([m["id"] for m in self.add_key("deepseek", "sk-FictionalDeepSeekKey0123456789")["models"]["deepseek"]],
                         ["deepseek-flash", "deepseek-v4-pro"])
        self.assertEqual(self.requests[-1]["headers"]["authorization"], "Bearer sk-FictionalDeepSeekKey0123456789")

    def test_choosing_a_provider_needs_its_key_and_your_confirmation(self):
        with self.assertRaisesRegex(ValueError, "Add your Claude API key"):
            cloud_ai.save_settings(self.data, {"provider": "anthropic", "consent": True})
        self.add_key()
        with self.assertRaisesRegex(ValueError, "Confirm that your profile"):
            cloud_ai.save_settings(self.data, {"provider": "anthropic"})
        self.assertIsNone(cloud_ai.engine(self.data))
        self.assertEqual(self.choose()["provider"], "anthropic")
        engine = cloud_ai.engine(self.data)
        self.assertEqual((engine.provider, engine.model, engine.label), ("anthropic", "claude-opus-5-5", "claude-opus-5-5 (Claude)"))
        cloud_ai.save_settings(self.data, {"models": {"anthropic": "claude-haiku-5-5"}})
        self.assertEqual(cloud_ai.engine(self.data).model, "claude-haiku-5-5")
        with self.assertRaises(ValueError):
            cloud_ai.save_settings(self.data, {"models": {"anthropic": "https://evil.example/model"}})
        # Removing the key goes back to the free local model.
        self.assertEqual(cloud_ai.delete_key(self.data, "anthropic")["provider"], "local")
        self.assertIsNone(cloud_ai.engine(self.data))

    # ------------------------------------------------------ generation

    def test_claude_streams_text_and_counts_every_token(self):
        self.add_key()
        self.choose()
        self.replies.append(sse(
            {"type": "message_start", "message": {"usage": {"input_tokens": 100, "cache_read_input_tokens": 20}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": ""}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Hello "}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "there"}},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 42}},
            {"type": "message_stop"}))
        seen = []
        result = cloud_ai.engine(self.data).generate("Be brief.", [{"role": "user", "content": "Hi"}], on_text=seen.append)
        self.assertEqual(result["text"], "Hello there")
        self.assertEqual(seen, ["Hello ", "Hello there"])
        request = self.requests[-1]
        self.assertEqual(request["url"], "https://api.anthropic.com/v1/messages")
        self.assertEqual(request["body"]["system"], "Be brief.")
        self.assertEqual(request["body"]["messages"], [{"role": "user", "content": "Hi"}])
        self.assertTrue(request["body"]["stream"])
        self.assertNotIn("temperature", request["body"])
        self.assertEqual(request["body"]["fallbacks"], "default")
        self.assertEqual(request["headers"]["anthropic-beta"], "server-side-fallback-2026-07-01")
        usage = cloud_ai.usage(self.data)
        self.assertEqual(usage["providers"]["anthropic"], {"input": 120, "output": 42, "requests": 1})
        self.assertEqual(usage["tokens"], 162)

    def test_chatgpt_uses_the_responses_api_without_storing_requests(self):
        self.add_key("openai", "sk-proj-FictionalOpenAIKey-0123456789")
        self.choose("openai")
        self.replies.append(sse(
            {"type": "response.created", "response": {}},
            {"type": "response.output_text.delta", "delta": "{\"a\": "},
            {"type": "response.output_text.delta", "delta": "1}"},
            {"type": "response.completed", "response": {"status": "completed", "usage": {"input_tokens": 50, "output_tokens": 9}}}))
        result = cloud_ai.engine(self.data).generate("Return JSON.", [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"},
                                                                       {"role": "user", "content": "z"}], json_mode=True, max_tokens=900)
        self.assertEqual(result["text"], "{\"a\": 1}")
        body = self.requests[-1]["body"]
        self.assertEqual(self.requests[-1]["url"], "https://api.openai.com/v1/responses")
        self.assertEqual(body["instructions"], "Return JSON.")
        self.assertEqual(body["input"][1], {"role": "assistant", "content": "y"})
        self.assertEqual(body["max_output_tokens"], 900)
        self.assertEqual(body["text"], {"format": {"type": "json_object"}})
        self.assertIs(body["store"], False)
        self.assertEqual(cloud_ai.usage(self.data)["providers"]["openai"]["output"], 9)

    def test_gemini_skips_thoughts_and_maps_roles(self):
        self.add_key("gemini", "AIzaFictionalGeminiKey0123456789")
        self.choose("gemini")
        self.replies.append(sse(
            {"candidates": [{"content": {"parts": [{"text": "thinking…", "thought": True}]}}]},
            {"candidates": [{"content": {"parts": [{"text": "Answer"}]}}]},
            {"candidates": [{"content": {"parts": [{"text": "."}]}, "finishReason": "STOP"}],
             "usageMetadata": {"promptTokenCount": 30, "candidatesTokenCount": 5, "thoughtsTokenCount": 7}}))
        result = cloud_ai.engine(self.data).generate("Sys", [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"},
                                                              {"role": "user", "content": "c"}])
        self.assertEqual(result["text"], "Answer.")
        request = self.requests[-1]
        self.assertEqual(request["url"], "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:streamGenerateContent?alt=sse")
        self.assertEqual([c["role"] for c in request["body"]["contents"]], ["user", "model", "user"])
        self.assertEqual(request["body"]["systemInstruction"], {"parts": [{"text": "Sys"}]})
        self.assertEqual(cloud_ai.usage(self.data)["providers"]["gemini"], {"input": 30, "output": 12, "requests": 1})

    def test_deepseek_reads_the_openai_style_stream(self):
        self.add_key("deepseek", "sk-FictionalDeepSeekKey0123456789")
        self.choose("deepseek")
        self.replies.append(sse(
            {"choices": [{"delta": {"reasoning_content": "hmm"}}]},
            {"choices": [{"delta": {"content": "Hej"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 3}}, done=True))
        result = cloud_ai.engine(self.data).generate("Sys", [{"role": "user", "content": "a"}], json_mode=True)
        self.assertEqual(result["text"], "Hej")
        body = self.requests[-1]["body"]
        self.assertEqual(body["messages"][0], {"role": "system", "content": "Sys"})
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["stream_options"], {"include_usage": True})
        self.assertEqual(cloud_ai.usage(self.data)["providers"]["deepseek"]["input"], 11)

    def test_an_unfinished_or_declined_answer_is_not_returned_but_is_counted(self):
        self.add_key()
        self.choose()
        self.replies.append(sse({"type": "message_start", "message": {"usage": {"input_tokens": 10}}},
                                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Partial"}},
                                {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}, "usage": {"output_tokens": 99}}))
        with self.assertRaisesRegex(cloud_ai.CloudAIError, "length limit"):
            cloud_ai.engine(self.data).generate("s", [{"role": "user", "content": "x"}])
        self.assertEqual(cloud_ai.usage(self.data)["tokens"], 109)
        self.replies.append(sse({"type": "message_delta", "delta": {"stop_reason": "refusal"}, "usage": {"output_tokens": 1}}))
        with self.assertRaisesRegex(cloud_ai.CloudAIError, "declined"):
            cloud_ai.engine(self.data).generate("s", [{"role": "user", "content": "x"}])

    def test_a_busy_provider_is_retried_and_the_partial_text_restarts(self):
        self.add_key()
        self.choose()
        self.replies.extend([
            http_error(529, {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}),
            sse({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Done"}},
                {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}})])
        seen, notes = [], []
        result = cloud_ai.engine(self.data).generate("s", [{"role": "user", "content": "x"}], on_text=seen.append, progress=notes.append)
        self.assertEqual(result["text"], "Done")
        self.assertEqual(seen, ["", "Done"])
        self.assertIn("busy", notes[0])
        # No credit left is reported plainly and not retried.
        self.replies.append(http_error(400, {"error": {"type": "invalid_request_error", "message": "Your credit balance is too low."}}))
        with self.assertRaisesRegex(cloud_ai.CloudAIError, "no credit left"):
            cloud_ai.engine(self.data).generate("s", [{"role": "user", "content": "x"}])
        self.assertEqual(self.replies, [])

    def test_the_monthly_limit_stops_requests_before_they_are_sent(self):
        self.add_key()
        self.choose()
        cloud_ai.save_settings(self.data, {"monthly_token_limit": 100})
        cloud_ai._record(self.data, "anthropic", 80, 30)
        sent = len(self.requests)
        with self.assertRaisesRegex(cloud_ai.CloudAIError, "limit of 100 tokens is used up"):
            cloud_ai.engine(self.data).generate("s", [{"role": "user", "content": "x"}])
        self.assertEqual(len(self.requests), sent)
        cloud_ai.save_settings(self.data, {"monthly_token_limit": 0})  # No limit.
        cloud_ai._check_allowance(self.data)
        with self.assertRaises(ValueError):
            cloud_ai.save_settings(self.data, {"monthly_token_limit": -1})

    def test_redirects_are_never_followed(self):
        self.assertIsNone(cloud_ai._NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://evil.example"))

    # ------------------------------------------- the drafting pipeline

    def test_cover_letters_from_a_cloud_model_get_the_same_checks(self):
        class Engine:
            is_cloud = True
            label = "fake-model (Fake)"

            def __init__(self, replies):
                self.replies, self.calls = list(replies), []

            def generate(self, system, messages, **options):
                self.calls.append((system, messages, options))
                return {"text": self.replies.pop(0)}

        profile = {"name": "Fictional Applicant", "summary": "Built Python services.", "evidence": ["Built Python services."],
                   "skills": ["Python"], "languages": {"Norwegian": "A1-A2"}}
        job = {"title": "Developer", "company": "Example AS", "description": "Develop Python services."}
        invented = json.dumps({"cover_letter": "Dear Hiring Team, I build Python services.", "review_notes": [],
                               "used_evidence": ["Ten years of Rust at a bank."]})
        good = "```json\n" + json.dumps({"cover_letter": "Dear Hiring Team,\nI built Python services.\nKind regards,\nFictional Applicant",
                                         "review_notes": ["Check the team."], "used_evidence": ["Built Python services."]}) + "\n```"
        engine = Engine([invented, good])
        result = local_ai.generate_cover_letter(job, profile, model=engine)
        self.assertIn("I built Python services.", result["cover_letter"])
        self.assertEqual(len(engine.calls), 2)  # The invented citation was rejected and retried.
        system, messages, options = engine.calls[1]
        self.assertIn("previous attempt was rejected", system)
        self.assertIn("JSON schema", system)
        self.assertTrue(options["json_mode"])
        self.assertIn("Develop Python services.", messages[0]["content"])


if __name__ == "__main__":
    unittest.main()
