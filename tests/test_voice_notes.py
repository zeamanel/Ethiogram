"""Voice notes: Gemini transcription + the webhook hook that turns a Telegram
voice message into ordinary text for the agent pipeline."""
import uuid
from datetime import datetime, timezone

import pytest

import app.api.webhooks as wh
import app.services.transcription_service as ts
from app.services.telegram_service import MessageEnvelope


# ── transcription service ────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.content = b""

    def json(self):
        return self._payload


def _mock_httpx(monkeypatch, resp):
    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, **k): return resp
        async def get(self, url, **k): return resp
    monkeypatch.setattr(ts.httpx, "AsyncClient", _Client)


@pytest.mark.asyncio
async def test_transcribe_via_gemini_api(monkeypatch):
    monkeypatch.setattr(ts.settings, "gemini_api_key", "test-key")
    _mock_httpx(monkeypatch, _Resp(200, {
        "candidates": [{"content": {"parts": [{"text": "ሰላም፣ ቀጠሮ መያዝ እፈልጋለሁ"}]}}]}))
    out = await ts.transcription_service.transcribe(b"OGGDATA", "audio/ogg")
    assert out == "ሰላም፣ ቀጠሮ መያዝ እፈልጋለሁ"


@pytest.mark.asyncio
async def test_transcribe_returns_none_on_api_error(monkeypatch):
    monkeypatch.setattr(ts.settings, "gemini_api_key", "test-key")
    _mock_httpx(monkeypatch, _Resp(500, text="boom"))

    async def _no_vertex(self, b, m): return None
    monkeypatch.setattr(ts.TranscriptionService, "_via_vertex", _no_vertex)
    assert await ts.transcription_service.transcribe(b"OGGDATA") is None


@pytest.mark.asyncio
async def test_transcribe_empty_audio_short_circuits():
    assert await ts.transcription_service.transcribe(b"") is None


# ── webhook hook ─────────────────────────────────────────────────────────────

class _FakeBot:
    id = uuid.uuid4()


def _voice_env(duration=10, text=None):
    return MessageEnvelope(
        platform="telegram", business_id="b", bot_id="bot", token_hash="t",
        customer_id="c1", customer_name="C", customer_username=None,
        text=text, media_type="voice", media_url=None, media_file_id="fid",
        message_id=1, timestamp=datetime.now(timezone.utc),
        raw={"message": {"voice": {"duration": duration, "mime_type": "audio/ogg"}}},
    )


def _patch_telegram(monkeypatch, sent):
    async def _send(token, chat_id, text, **k):
        sent.append(text)
    async def _url(token, file_id):
        return "https://files.test/voice.ogg"
    monkeypatch.setattr(wh.telegram_service, "send_message", _send)
    monkeypatch.setattr(wh.telegram_service, "get_file_download_url", _url)


def _patch_download(monkeypatch, content=b"OGG"):
    import httpx

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, **k):
            r = _Resp(200)
            r.content = content
            return r
    monkeypatch.setattr(httpx, "AsyncClient", _Client)


@pytest.mark.asyncio
async def test_voice_transcript_becomes_message_text(monkeypatch):
    sent = []
    _patch_telegram(monkeypatch, sent)
    _patch_download(monkeypatch)

    async def _transcribe(audio, mime="audio/ogg"):
        assert audio == b"OGG" and mime == "audio/ogg"
        return "ዋጋው ስንት ነው"
    monkeypatch.setattr(ts.transcription_service, "transcribe", _transcribe)

    env = _voice_env()
    ok = await wh._maybe_transcribe_voice(env, "tok", _FakeBot())
    assert ok is True
    assert env.text == "ዋጋው ስንት ነው"
    assert sent == []                      # no error reply on success


@pytest.mark.asyncio
async def test_voice_too_long_gets_polite_reply(monkeypatch):
    sent = []
    _patch_telegram(monkeypatch, sent)
    env = _voice_env(duration=999)
    ok = await wh._maybe_transcribe_voice(env, "tok", _FakeBot())
    assert ok is False
    assert len(sent) == 1 and "2" in sent[0]


@pytest.mark.asyncio
async def test_voice_transcription_failure_gets_polite_reply(monkeypatch):
    sent = []
    _patch_telegram(monkeypatch, sent)
    _patch_download(monkeypatch)

    async def _fail(audio, mime="audio/ogg"): return None
    monkeypatch.setattr(ts.transcription_service, "transcribe", _fail)

    env = _voice_env()
    ok = await wh._maybe_transcribe_voice(env, "tok", _FakeBot())
    assert ok is False
    assert len(sent) == 1 and "ይቅርታ" in sent[0]


@pytest.mark.asyncio
async def test_audio_caption_is_kept_with_transcript(monkeypatch):
    sent = []
    _patch_telegram(monkeypatch, sent)
    _patch_download(monkeypatch)

    async def _transcribe(audio, mime="audio/ogg"): return "the spoken part"
    monkeypatch.setattr(ts.transcription_service, "transcribe", _transcribe)

    env = _voice_env(text="caption here")
    assert await wh._maybe_transcribe_voice(env, "tok", _FakeBot()) is True
    assert env.text == "caption here\nthe spoken part"
