"""Tests never call real Gemini, whatever .env says: tests that need an agent script a FakeLlm
and opt in by setting GOOGLE_API_KEY themselves."""
import pytest


@pytest.fixture(autouse=True)
def _no_real_gemini(monkeypatch):
    for var in ("GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION"):
        monkeypatch.delenv(var, raising=False)
