"""
Thin wrapper around the Google Gemini API.

This is the ONLY file that talks directly to the Gemini SDK.
The rest of the application communicates through GeminiClient.

Designed for Gemma 4 31B:
    gemma-4-31b-it
"""

import json
import logging
import mimetypes
import time

from config import Config

logger = logging.getLogger(__name__)

_genai = None
_import_error = None

try:
    import google.generativeai as genai

    _genai = genai
except ImportError as exc:
    _import_error = exc


class GeminiClientError(Exception):
    """Raised for unrecoverable Gemini API failures."""


class GeminiNotConfiguredError(GeminiClientError):
    """Raised when Gemini API is not configured correctly."""


class GeminiClient:
    """
    Wrapper around Gemini generate_content().

    Features:
    - server-side API key
    - JSON output
    - multimodal image input
    - longer timeout for Gemma 4 31B
    - one retry for transient failures
    """

    def __init__(self, api_key=None, model_name=None):
        self.api_key = api_key or Config.GEMINI_API_KEY
        self.model_name = model_name or Config.GEMINI_MODEL
        self._configured = False

    def _ensure_configured(self):
        if _genai is None:
            raise GeminiNotConfiguredError(
                "google-generativeai is not installed. "
                "Run: pip install -r requirements.txt"
            )

        if not self.api_key:
            raise GeminiNotConfiguredError(
                "GEMINI_API_KEY is not set. "
                "Add it to your .env file."
            )

        if not self._configured:
            _genai.configure(api_key=self.api_key)
            self._configured = True

    def _build_parts(self, prompt_text, images=None):
        """
        Build multimodal Gemini content parts.
        """

        parts = [prompt_text]

        for image in images or []:
            label = image.get("label")

            if label:
                parts.append(
                    f"[Reference image: {label}]"
                )

            parts.append(
                {
                    "mime_type": image["mime_type"],
                    "data": image["bytes"],
                }
            )

        return parts

    def generate_json(
        self,
        system_instruction,
        prompt_text,
        images=None,
        temperature=0.8,
        max_output_tokens=None,
    ):
        """
        Generate a JSON response from Gemini.

        Gemma 4 31B can sometimes take longer to respond, so the
        timeout is intentionally generous.

        Only one retry is performed for transient failures.
        """

        self._ensure_configured()

        # Keep the output reasonably small.
        # Thumbnail concepts do not need thousands of output tokens.
        output_tokens = (
            max_output_tokens
            if max_output_tokens is not None
            else min(
                Config.GEMINI_MAX_OUTPUT_TOKENS,
                2500,
            )
        )

        generation_config = {
            "temperature": temperature,
            "max_output_tokens": output_tokens,
            "response_mime_type": "application/json",
        }

        logger.info(
            "Calling Gemini model=%s output_tokens=%s",
            self.model_name,
            output_tokens,
        )

        model = _genai.GenerativeModel(
            model_name=self.model_name,
            system_instruction=system_instruction,
            generation_config=generation_config,
        )

        parts = self._build_parts(
            prompt_text,
            images,
        )

        max_retries = min(
            max(0, Config.GEMINI_MAX_RETRIES),
            1,
        )

        attempts = 1 + max_retries
        last_error = None

        for attempt in range(1, attempts + 1):

            try:
                logger.info(
                    "Gemini request attempt %s/%s",
                    attempt,
                    attempts,
                )

                response = model.generate_content(
                    parts,
                    request_options={
                        # 180 seconds instead of the old 45 seconds.
                        "timeout": 180,
                    },
                )

                raw_text = self._extract_text(response)

                logger.info(
                    "Gemini response received successfully."
                )

                return self._parse_json_safely(raw_text)

            except json.JSONDecodeError as exc:

                last_error = exc

                logger.warning(
                    "Gemini returned malformed JSON on attempt "
                    "%s/%s: %s",
                    attempt,
                    attempts,
                    exc,
                )

                if attempt < attempts:
                    time.sleep(0.5)

            except Exception as exc:

                last_error = exc
                message = str(exc).lower()

                # Rate limit / quota
                if (
                    "quota" in message
                    or "rate" in message
                    or "429" in message
                ):
                    raise GeminiClientError(
                        "Gemini API rate limit or quota was exceeded. "
                        "Please try again shortly."
                    ) from exc

                # Timeout / 504
                if (
                    "timeout" in message
                    or "deadline" in message
                    or "504" in message
                ):

                    logger.warning(
                        "Gemini request timed out on attempt "
                        "%s/%s",
                        attempt,
                        attempts,
                    )

                    if attempt < attempts:
                        time.sleep(1)

                    continue

                # Any other API error should not be retried.
                raise GeminiClientError(
                    f"Gemini API request failed: {exc}"
                ) from exc

        raise GeminiClientError(
            "Gemini did not return a valid response after "
            f"{attempts} attempt(s). Last error: {last_error}"
        )

    @staticmethod
    def _extract_text(response):
        """
        Extract text safely from a Gemini response.
        """

        text = getattr(response, "text", None)

        if text:
            return text

        candidates = (
            getattr(response, "candidates", None)
            or []
        )

        for candidate in candidates:

            content = getattr(
                candidate,
                "content",
                None,
            )

            if not content:
                continue

            for part in (
                getattr(content, "parts", [])
                or []
            ):

                part_text = getattr(
                    part,
                    "text",
                    None,
                )

                if part_text:
                    return part_text

        raise GeminiClientError(
            "Gemini returned an empty response. "
            "The request may have been blocked or interrupted."
        )

    @staticmethod
    def _parse_json_safely(raw_text):
        """
        Parse JSON with a few safe fallbacks.

        Handles:
        - normal JSON
        - markdown code fences
        - JSON surrounded by extra text
        """

        # Normal JSON
        try:
            return json.loads(raw_text)

        except json.JSONDecodeError:
            pass

        cleaned = raw_text.strip()

        # Remove markdown fences
        if cleaned.startswith("```"):

            lines = cleaned.splitlines()

            if lines:
                lines = lines[1:]

            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]

            cleaned = "\n".join(lines).strip()

        # Try again
        try:
            return json.loads(cleaned)

        except json.JSONDecodeError:
            pass

        # Find first JSON object
        start = cleaned.find("{")
        end = cleaned.rfind("}")

        if start != -1 and end != -1 and end > start:

            candidate = cleaned[
                start:end + 1
            ]

            return json.loads(candidate)

        raise json.JSONDecodeError(
            "Could not locate a valid JSON object.",
            cleaned,
            0,
        )


def image_file_to_part(file_storage):
    """
    Convert Flask FileStorage into Gemini image-part format.
    """

    mime_type = (
        file_storage.mimetype
        or mimetypes.guess_type(
            file_storage.filename
        )[0]
    )

    return {
        "bytes": file_storage.read(),
        "mime_type": mime_type or "image/jpeg",
    }