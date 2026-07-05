import unittest
from unittest.mock import MagicMock, patch

from job_pipeline.genai_client import (
    generate_content_with_retry,
    is_retryable_capacity_error,
)


class ServerError(Exception):
    def __init__(self, status_code, message=""):
        super().__init__(message)
        self.status_code = status_code


class TestIsRetryableCapacityError(unittest.TestCase):
    def test_status_code_503(self):
        self.assertTrue(is_retryable_capacity_error(ServerError(503, "unavailable")))

    def test_message_contains_unavailable(self):
        self.assertTrue(is_retryable_capacity_error(Exception("503 UNAVAILABLE")))

    def test_non_retryable(self):
        self.assertFalse(is_retryable_capacity_error(Exception("invalid api key")))


class TestGenerateContentWithRetry(unittest.TestCase):
    def test_succeeds_on_first_try(self):
        client = MagicMock()
        expected = MagicMock(text="ok")
        client.models.generate_content.return_value = expected

        out = generate_content_with_retry(
            client,
            model="models/gemini-2.5-pro",
            contents=["prompt"],
            max_retries=3,
            base_sleep=0.01,
            fallback_model="models/gemini-2.5-flash",
            label="test",
        )

        self.assertIs(out, expected)
        client.models.generate_content.assert_called_once()

    @patch("job_pipeline.genai_client.time.sleep")
    def test_retries_then_succeeds(self, sleep_mock):
        client = MagicMock()
        expected = MagicMock(text="ok")
        client.models.generate_content.side_effect = [
            ServerError(503, "high demand"),
            expected,
        ]

        out = generate_content_with_retry(
            client,
            model="models/gemini-2.5-pro",
            contents=["prompt"],
            max_retries=3,
            base_sleep=0.01,
            fallback_model="models/gemini-2.5-flash",
            label="test",
        )

        self.assertIs(out, expected)
        self.assertEqual(client.models.generate_content.call_count, 2)
        sleep_mock.assert_called_once()

    @patch("job_pipeline.genai_client.time.sleep")
    def test_falls_back_to_flash_after_primary_exhausted(self, sleep_mock):
        client = MagicMock()
        expected = MagicMock(text="flash ok")
        client.models.generate_content.side_effect = [
            ServerError(503, "high demand"),
            ServerError(503, "high demand"),
            expected,
        ]

        out = generate_content_with_retry(
            client,
            model="models/gemini-2.5-pro",
            contents=["prompt"],
            max_retries=2,
            base_sleep=0.01,
            fallback_model="models/gemini-2.5-flash",
            label="test",
        )

        self.assertIs(out, expected)
        self.assertEqual(client.models.generate_content.call_count, 3)
        self.assertEqual(
            client.models.generate_content.call_args_list[-1].kwargs["model"],
            "models/gemini-2.5-flash",
        )

    @patch("job_pipeline.genai_client.time.sleep")
    def test_raises_after_all_attempts_fail(self, sleep_mock):
        client = MagicMock()
        client.models.generate_content.side_effect = ServerError(503, "high demand")

        with self.assertRaises(ServerError):
            generate_content_with_retry(
                client,
                model="models/gemini-2.5-pro",
                contents=["prompt"],
                max_retries=2,
                base_sleep=0.01,
                fallback_model="models/gemini-2.5-flash",
                label="test",
            )

        self.assertEqual(client.models.generate_content.call_count, 4)

    def test_non_retryable_error_raises_immediately(self):
        client = MagicMock()
        client.models.generate_content.side_effect = Exception("401 unauthorized")

        with self.assertRaisesRegex(Exception, "401"):
            generate_content_with_retry(
                client,
                model="models/gemini-2.5-pro",
                contents=["prompt"],
                max_retries=3,
                base_sleep=0.01,
                label="test",
            )

        client.models.generate_content.assert_called_once()


if __name__ == "__main__":
    unittest.main()
