import os
import unittest
from unittest.mock import patch

from job_pipeline.llm_provider import LLMWritingError, generate_json, writing_provider
from job_pipeline.openai_client import OpenAIKeyMissingError


class TestWritingProvider(unittest.TestCase):
    @patch.dict(os.environ, {"LLM_WRITING_PROVIDER": "gemini"}, clear=False)
    def test_writing_provider_gemini(self):
        self.assertEqual(writing_provider(), "gemini")

    @patch.dict(os.environ, {"LLM_WRITING_PROVIDER": "openai"}, clear=False)
    def test_writing_provider_openai_default(self):
        self.assertEqual(writing_provider(), "openai")

    @patch.dict(os.environ, {"LLM_WRITING_PROVIDER": "bogus"}, clear=False)
    def test_invalid_provider_defaults_openai(self):
        self.assertEqual(writing_provider(), "openai")

    def test_openai_model_for_role(self):
        from job_pipeline.genai_settings import openai_model_for

        with patch.dict(os.environ, {"OPENAI_RESUME_TAILOR_MODEL": "gpt-4o"}, clear=False):
            self.assertEqual(openai_model_for("tailor"), "gpt-4o")


class TestGenerateJsonFallback(unittest.TestCase):
    @patch("job_pipeline.llm_provider._gemini_generate_json")
    @patch("job_pipeline.llm_provider.openai_generate_json_with_retry")
    @patch.dict(os.environ, {"LLM_WRITING_PROVIDER": "openai"}, clear=False)
    def test_openai_primary_success(self, mock_openai, mock_gemini):
        mock_openai.return_value = {"summary": "ok"}
        out = generate_json("tailor", system="sys", user="prompt")
        self.assertEqual(out["summary"], "ok")
        mock_openai.assert_called_once()
        mock_gemini.assert_not_called()

    @patch("job_pipeline.llm_provider._gemini_generate_json")
    @patch("job_pipeline.llm_provider.openai_generate_json_with_retry")
    @patch.dict(os.environ, {"LLM_WRITING_PROVIDER": "openai"}, clear=False)
    def test_falls_back_to_gemini_when_openai_fails(self, mock_openai, mock_gemini):
        mock_openai.side_effect = OpenAIKeyMissingError("missing")
        mock_gemini.return_value = {"opening": "hi"}
        out = generate_json("cover_letter", system="sys", user="prompt")
        self.assertEqual(out["opening"], "hi")
        mock_openai.assert_called_once()
        mock_gemini.assert_called_once()

    @patch("job_pipeline.llm_provider._gemini_generate_json")
    @patch("job_pipeline.llm_provider.openai_generate_json_with_retry")
    @patch.dict(os.environ, {"LLM_WRITING_PROVIDER": "openai"}, clear=False)
    def test_raises_when_both_providers_fail(self, mock_openai, mock_gemini):
        mock_openai.side_effect = RuntimeError("openai down")
        mock_gemini.side_effect = RuntimeError("gemini down")
        with self.assertRaises(LLMWritingError):
            generate_json("tailor", system="sys", user="prompt")

    @patch("job_pipeline.llm_provider._gemini_generate_json")
    @patch("job_pipeline.llm_provider.openai_generate_json_with_retry")
    @patch.dict(os.environ, {"LLM_WRITING_PROVIDER": "gemini"}, clear=False)
    def test_gemini_primary_falls_back_to_openai(self, mock_openai, mock_gemini):
        mock_gemini.side_effect = RuntimeError("503")
        mock_openai.return_value = {"warnings": []}
        out = generate_json("package_check", system="sys", user="prompt")
        self.assertEqual(out["warnings"], [])
        mock_gemini.assert_called_once()
        mock_openai.assert_called_once()


if __name__ == "__main__":
    unittest.main()
