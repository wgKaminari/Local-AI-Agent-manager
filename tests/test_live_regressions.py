import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_job_agent.nav import _get_token
from norway_job_agent.local_ai import _check_language_claims, LocalAIError
from norway_job_agent.sources import _plain


class LiveRegressions(unittest.TestCase):
    def test_job_titles_with_trailing_ampersand_text_are_preserved(self):
        self.assertEqual(_plain("Sr. Data Engineer - FP&A"), "Sr. Data Engineer - FP&A")
        self.assertEqual(_plain("Research & Development"), "Research & Development")

    def test_nav_token_endpoint_includes_explanatory_heading(self):
        public_response = b"Current public token for Nav Job Vacancy Feed:\nheader.payload.signature\n"
        with patch("norway_job_agent.nav._fetch_nav", return_value=(200, {}, public_response, "")):
            with patch.dict("os.environ", {}, clear=True):
                self.assertEqual(_get_token(None), ("header.payload.signature", True))

    def test_correct_mixed_language_sentence_is_not_rejected(self):
        _check_language_claims("My English is B2-C1 and my Norwegian is A1-A2.", {"English": "B2-C1", "Norwegian": "A1-A2"})
        _check_language_claims("I speak English fluently, while my Norwegian is A1-A2.", {"English": "B2-C1", "Norwegian": "A1-A2"})

    def test_norwegian_fluently_remains_an_overstatement(self):
        with self.assertRaises(LocalAIError):
            _check_language_claims("I speak Norwegian fluently.", {"Norwegian": "A1-A2"})


if __name__ == "__main__":
    unittest.main()
