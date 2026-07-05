import unittest
from unittest.mock import MagicMock, patch

from job_pipeline.genai_client import is_retryable_capacity_error
from job_pipeline.openai_client import (
    OpenAIKeyMissingError,
    is_retryable_openai_error,
    is_temperature_parameter_error,
    model_fixed_temperature_family,
    openai_generate_json_with_retry,
    resolve_openai_temperature,
)


class RateLimitError(Exception):
    def __init__(self, message=""):
        super().__init__(message)
        self.status_code = 429


class TestOpenAIRetryable(unittest.TestCase):
    def test_rate_limit_is_retryable(self):
        self.assertTrue(is_retryable_openai_error(RateLimitError("rate limit")))

    def test_non_retryable(self):
        self.assertFalse(is_retryable_openai_error(Exception("401 unauthorized")))

    def test_capacity_helper_overlap(self):
        self.assertTrue(is_retryable_capacity_error(RateLimitError("429")))


class TestOpenAITemperature(unittest.TestCase):
    def test_fixed_temp_family_o_series(self):
        self.assertTrue(model_fixed_temperature_family("o3-mini"))
        self.assertTrue(model_fixed_temperature_family("gpt-5"))

    def test_gpt4_can_use_low_temp(self):
        self.assertEqual(resolve_openai_temperature("gpt-4.1", explicit=0.2), 0.2)

    def test_o_series_omits_temp_even_when_explicit(self):
        self.assertIsNone(resolve_openai_temperature("o3-mini", explicit=0.2))

    @patch("job_pipeline.openai_client.openai_api_key", return_value="test-key")
    def test_retries_without_temperature_after_400(self, _key):
        client = MagicMock()
        ok = MagicMock()
        ok.choices = [MagicMock(message=MagicMock(content='{"ok": true}'))]

        class BadRequestError(Exception):
            pass

        err = BadRequestError(
            "Error code: 400 - temperature does not support 0.2 with this model"
        )
        client.chat.completions.create.side_effect = [err, ok]

        out = openai_generate_json_with_retry(
            model="some-new-model",
            system="sys",
            user="user",
            client=client,
            temperature=0.2,
            max_retries=1,
            base_sleep=0.01,
        )
        self.assertEqual(out, {"ok": True})
        self.assertEqual(client.chat.completions.create.call_count, 2)
        first = client.chat.completions.create.call_args_list[0].kwargs
        second = client.chat.completions.create.call_args_list[1].kwargs
        self.assertIn("temperature", first)
        self.assertNotIn("temperature", second)
        self.assertTrue(is_temperature_parameter_error(err))


class TestOpenAIGenerateJsonWithRetry(unittest.TestCase):
    @patch("job_pipeline.openai_client.openai_api_key", return_value="test-key")
    def test_succeeds_on_first_try(self, _key):
        client = MagicMock()
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content='{"ok": true}'))]
        client.chat.completions.create.return_value = resp

        out = openai_generate_json_with_retry(
            model="gpt-4.1",
            system="sys",
            user="user",
            client=client,
            max_retries=3,
            base_sleep=0.01,
            label="test",
        )
        self.assertEqual(out, {"ok": True})
        client.chat.completions.create.assert_called_once()
        kwargs = client.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["response_format"], {"type": "json_object"})
        self.assertEqual(kwargs.get("temperature"), 0.2)

    @patch("job_pipeline.openai_client.openai_api_key", return_value="")
    def test_missing_key_raises(self, _key):
        with self.assertRaises(OpenAIKeyMissingError):
            openai_generate_json_with_retry(
                model="gpt-4.1",
                system="sys",
                user="user",
                max_retries=1,
                base_sleep=0.01,
            )

    @patch("job_pipeline.openai_client.openai_api_key", return_value="test-key")
    @patch("job_pipeline.openai_client.time.sleep")
    def test_retries_then_succeeds(self, sleep_mock, _key):
        client = MagicMock()
        ok = MagicMock()
        ok.choices = [MagicMock(message=MagicMock(content='{"done": 1}'))]
        client.chat.completions.create.side_effect = [RateLimitError("429"), ok]

        out = openai_generate_json_with_retry(
            model="gpt-4.1",
            system="sys",
            user="user",
            client=client,
            max_retries=3,
            base_sleep=0.01,
            label="test",
        )
        self.assertEqual(out, {"done": 1})
        self.assertEqual(client.chat.completions.create.call_count, 2)
        sleep_mock.assert_called_once()

    @patch("job_pipeline.openai_client.openai_api_key", return_value="test-key")
    @patch("job_pipeline.openai_client.time.sleep")
    def test_raises_after_retries_exhausted(self, _sleep, _key):
        client = MagicMock()
        client.chat.completions.create.side_effect = RateLimitError("503")

        with self.assertRaises(RateLimitError):
            openai_generate_json_with_retry(
                model="gpt-4.1",
                system="sys",
                user="user",
                client=client,
                max_retries=2,
                base_sleep=0.01,
            )
        self.assertEqual(client.chat.completions.create.call_count, 2)


if __name__ == "__main__":
    unittest.main()
