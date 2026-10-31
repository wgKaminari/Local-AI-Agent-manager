"""Optional cloud AI: Claude, ChatGPT, Gemini or DeepSeek with your own API key.

The local Ollama model stays the default and costs nothing. A cloud provider
is used only after you add its API key and choose it in Settings; requests
then go straight from this computer to that provider and are billed to your
account there. Chat subscriptions (ChatGPT Plus, Claude Pro, Gemini
Advanced) do not include API use.

Keys are saved in ai-keys.json in the data folder, encrypted for your Windows
account (DPAPI), never in sources.json or backups, and never sent back to the
page: it only learns whether a key is saved and its last characters. A monthly
token limit, counted in ai-usage.json, stops cloud requests once the month's
allowance is used.

API references: https://platform.claude.com/docs/en/api/messages,
https://developers.openai.com/api/docs/api-reference/responses,
https://ai.google.dev/api/generate-content and
https://api-docs.deepseek.com/api/create-chat-completion.
"""

from __future__ import annotations

import base64
import json
import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode

KEY_FILE = "ai-keys.json"
SETTINGS_FILE = "ai-settings.json"
USAGE_FILE = "ai-usage.json"
DEFAULT_MONTHLY_TOKENS = 1_000_000
MAX_RESPONSE_BYTES = 16_000_000
_MAX_LINE = 2_000_000
# Streams are timed by silence, so a long answer is never cut off; a
# reasoning model can think for a while before its first word.
_IDLE_TIMEOUT = 180
_TOTAL_TIMEOUT = 900
_RETRY_PAUSES = (2, 6)

PROVIDERS = {
    "anthropic": {
        "name": "Claude", "company": "Anthropic", "default_model": "claude-opus-5-5",
        "keys_url": "https://platform.claude.com/settings/keys",
        "billing_url": "https://platform.claude.com/settings/billing",
        "note": "Claude Opus 5.5 writes best; Claude Sonnet 5.5 and Claude Haiku 5.5 cost less. "
                "Paid per use from credit you add in the Claude Console.",
    },
    "openai": {
        "name": "ChatGPT", "company": "OpenAI", "default_model": "gpt-6.1-terra",
        "keys_url": "https://platform.openai.com/api-keys",
        "billing_url": "https://platform.openai.com/settings/organization/billing/overview",
        "note": "A ChatGPT Plus or Pro subscription does not include API use: the API is paid per use "
                "from credit you add on the OpenAI platform.",
    },
    "gemini": {
        "name": "Gemini", "company": "Google", "default_model": "gemini-3.8-flash",
        "keys_url": "https://aistudio.google.com/apikey",
        "billing_url": "https://aistudio.google.com/usage",
        "note": "Google AI Studio has a free tier with daily limits. Google may use free-tier requests "
                "to improve its products; requests on a paid plan are not used that way.",
    },
    "deepseek": {
        "name": "DeepSeek", "company": "DeepSeek", "default_model": "deepseek-flash",
        "keys_url": "https://platform.deepseek.com/api_keys",
        "billing_url": "https://platform.deepseek.com/usage",
        "note": "The lowest prices of the four; paid per use from credit you top up. "
                "DeepSeek stores requests on servers in China.",
    },
}
# Claude models that accept the server-side fallback for declined requests.
_FALLBACK_MODELS = frozenset({"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"})
_KEY = re.compile(r"[\x21-\x7e]{20,400}\Z")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SECRET = re.compile(r"(sk-[A-Za-z0-9_*.-]{4,}|AIza[A-Za-z0-9_*-]{8,})")
_lock = threading.Lock()


class CloudAIError(RuntimeError):
    """A user-readable error. It never includes an API key, CV or vacancy text."""


class _Retryable(CloudAIError):
    """The provider is busy or the connection dropped; trying again may work."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # A redirect would carry the API key to another address.
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _provider(provider: str) -> dict:
    if provider not in PROVIDERS:
        raise ValueError("Choose Claude, ChatGPT, Gemini or DeepSeek.")
    return PROVIDERS[provider]


def validate_model(model: str) -> str:
    if not isinstance(model, str) or not _MODEL.match(model.strip()):
        raise ValueError("Choose a model from the provider's list, such as claude-opus-5-5.")
    return model.strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read(path: Path) -> dict:
    try:
        if path.stat().st_size > 1_000_000:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            json.dump(value, output, indent=2, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


# ---------------------------------------------------------------- keys

def _protect(secret: str) -> dict:
    data = secret.encode("utf-8")
    if os.name != "nt":
        return {"protection": "none", "data": base64.b64encode(data).decode("ascii")}
    from .gmail import _windows_protect
    try:
        return {"protection": "windows-dpapi", "data": base64.b64encode(_windows_protect(data)).decode("ascii")}
    except Exception:
        raise CloudAIError("Windows could not encrypt the API key for your account, so it was not saved.") from None


def _unprotect(entry: dict) -> str:
    try:
        data = base64.b64decode(entry["data"], validate=True)
        if entry.get("protection") == "windows-dpapi":
            if os.name != "nt":
                raise CloudAIError("This API key was saved on Windows. Add it again on this computer.")
            from .gmail import _windows_protect
            data = _windows_protect(data, decrypt=True)
        elif entry.get("protection") != "none":
            raise ValueError
        return data.decode("utf-8")
    except CloudAIError:
        raise
    except Exception:
        raise CloudAIError("The saved API key cannot be unlocked for this Windows account. Remove it and add it again.") from None


def _keys(data_dir: Path) -> dict:
    keys = _read(Path(data_dir) / KEY_FILE).get("keys")
    return {name: entry for name, entry in keys.items() if name in PROVIDERS and isinstance(entry, dict)} if isinstance(keys, dict) else {}


def _key(data_dir: Path, provider: str) -> str:
    entry = _keys(data_dir).get(provider)
    if not entry:
        raise CloudAIError(f"Add your {PROVIDERS[provider]['name']} API key in Settings → AI assistant first.")
    return _unprotect(entry)


def save_key(data_dir: Path, provider: str, key: str) -> dict:
    """Check the key by listing the provider's models (free), then save it encrypted."""
    info = _provider(provider)
    key = key.strip() if isinstance(key, str) else ""
    if not _KEY.match(key):
        raise ValueError(f"Paste the whole {info['name']} API key: one line, no spaces.")
    models = list_models(data_dir, provider, key=key)
    with _lock:
        stored = _read(Path(data_dir) / KEY_FILE)
        keys = stored.get("keys") if isinstance(stored.get("keys"), dict) else {}
        keys[provider] = {**_protect(key), "hint": "…" + key[-4:], "saved_at": _now()}
        _write(Path(data_dir) / KEY_FILE, {"keys": keys})
    settings = read_settings(data_dir)
    if provider not in settings["models"]:
        ids = [model["id"] for model in models]
        settings["models"][provider] = info["default_model"] if info["default_model"] in ids or not ids else ids[0]
        _save_settings(data_dir, settings)
    return {**status(data_dir), "models": {provider: models}}


def delete_key(data_dir: Path, provider: str) -> dict:
    _provider(provider)
    with _lock:
        keys = _keys(data_dir)
        keys.pop(provider, None)
        _write(Path(data_dir) / KEY_FILE, {"keys": keys})
    settings = read_settings(data_dir)
    if settings["provider"] == provider:
        settings["provider"] = "local"
        _save_settings(data_dir, settings)
    return status(data_dir)


# ------------------------------------------------------------ settings

def read_settings(data_dir: Path) -> dict:
    raw = _read(Path(data_dir) / SETTINGS_FILE)
    models = raw.get("models") if isinstance(raw.get("models"), dict) else {}
    limit = raw.get("monthly_token_limit", DEFAULT_MONTHLY_TOKENS)
    consented = raw.get("consented") if isinstance(raw.get("consented"), list) else []
    result = {
        "provider": raw.get("provider") if raw.get("provider") in {"local", *PROVIDERS} else "local",
        "models": {name: model for name, model in models.items() if name in PROVIDERS and isinstance(model, str) and _MODEL.match(model)},
        "monthly_token_limit": limit if isinstance(limit, int) and not isinstance(limit, bool) and 0 <= limit <= 1_000_000_000 else DEFAULT_MONTHLY_TOKENS,
        "consented": [name for name in consented if name in PROVIDERS],
    }
    if result["provider"] != "local" and (result["provider"] not in _keys(data_dir) or result["provider"] not in result["consented"]):
        result["provider"] = "local"
    return result


def _save_settings(data_dir: Path, settings: dict) -> None:
    _write(Path(data_dir) / SETTINGS_FILE, settings)


def save_settings(data_dir: Path, value: dict) -> dict:
    """Choose the writer (local or a provider whose key is saved), models and the monthly limit."""
    if not isinstance(value, dict):
        raise ValueError("AI settings must be an object.")
    settings = read_settings(data_dir)
    if "models" in value:
        if not isinstance(value["models"], dict):
            raise ValueError("Models must map providers to model names.")
        for provider, model in value["models"].items():
            _provider(provider)
            settings["models"][provider] = validate_model(model)
    if "monthly_token_limit" in value:
        limit = value["monthly_token_limit"]
        if not isinstance(limit, int) or isinstance(limit, bool) or not 0 <= limit <= 1_000_000_000:
            raise ValueError("The monthly limit must be a whole number of tokens from 0 (no limit) to 1,000,000,000.")
        settings["monthly_token_limit"] = limit
    if "provider" in value:
        provider = value["provider"]
        if provider != "local":
            info = _provider(provider)
            if provider not in _keys(data_dir):
                raise ValueError(f"Add your {info['name']} API key before choosing {info['name']}.")
            if value.get("consent") is True and provider not in settings["consented"]:
                settings["consented"].append(provider)
            if provider not in settings["consented"]:
                raise ValueError(f"Confirm that your profile, CV, vacancies and chats may be sent to {info['company']} first.")
        settings["provider"] = provider
    _save_settings(data_dir, settings)
    return status(data_dir)


# --------------------------------------------------------------- usage

def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def usage(data_dir: Path) -> dict:
    raw = _read(Path(data_dir) / USAGE_FILE)
    providers = raw.get("providers") if raw.get("month") == _month() and isinstance(raw.get("providers"), dict) else {}
    clean = {}
    for name, counts in providers.items():
        if name in PROVIDERS and isinstance(counts, dict):
            clean[name] = {key: int(counts.get(key) or 0) for key in ("input", "output", "requests")}
    total = sum(counts["input"] + counts["output"] for counts in clean.values())
    return {"month": _month(), "providers": clean, "tokens": total}


def _record(data_dir: Path, provider: str, input_tokens: int, output_tokens: int) -> None:
    with _lock:
        current = usage(data_dir)
        counts = current["providers"].setdefault(provider, {"input": 0, "output": 0, "requests": 0})
        counts["input"] += max(0, int(input_tokens or 0))
        counts["output"] += max(0, int(output_tokens or 0))
        counts["requests"] += 1
        _write(Path(data_dir) / USAGE_FILE, {"month": current["month"], "providers": current["providers"]})


def _check_allowance(data_dir: Path) -> None:
    limit = read_settings(data_dir)["monthly_token_limit"]
    used = usage(data_dir)["tokens"]
    if limit and used >= limit:
        raise CloudAIError(f"This month's cloud AI limit of {limit:,} tokens is used up ({used:,}). "
                           "Raise it in Settings → AI assistant, or switch to your free local model.")


def status(data_dir: Path) -> dict:
    """What the page may show: never a key, only whether one is saved."""
    settings, keys = read_settings(data_dir), _keys(data_dir)
    providers = [{
        "id": name, **{key: info[key] for key in ("name", "company", "default_model", "keys_url", "billing_url", "note")},
        "configured": name in keys, "hint": keys.get(name, {}).get("hint", ""),
        "saved_at": keys.get(name, {}).get("saved_at", ""),
        "model": settings["models"].get(name, info["default_model"]),
        "consented": name in settings["consented"],
    } for name, info in PROVIDERS.items()]
    return {"provider": settings["provider"], "providers": providers, "usage": usage(data_dir),
            "monthly_token_limit": settings["monthly_token_limit"]}


# ----------------------------------------------------------------- HTTP

def _clean_message(text) -> str:
    text = " ".join(str(text or "").split())[:300]
    return _SECRET.sub("[key]", text)


def _error_message(body: bytes) -> str:
    try:
        value = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return ""
    if isinstance(value, list) and value:
        value = value[0]
    error = value.get("error") if isinstance(value, dict) else None
    if isinstance(error, dict):
        return _clean_message(error.get("message") or error.get("type") or "")
    return _clean_message(error) if isinstance(error, str) else ""


def _http_error(provider: str, status: int, body: bytes, model: str = "") -> CloudAIError:
    name, message = PROVIDERS[provider]["name"], _error_message(body)
    detail = f" ({message})" if message else ""
    lowered = message.casefold()
    if status in (401, 403):
        return CloudAIError(f"{name} did not accept the API key{detail}. Check it in Settings → AI assistant, or create a new key.")
    if status == 402 or any(word in lowered for word in ("credit balance", "insufficient balance", "insufficient_quota", "exceeded your current quota", "billing")):
        return CloudAIError(f"{name} reports that your account has no credit left{detail}. Add credit on {PROVIDERS[provider]['billing_url']}, or switch to your local model.")
    if status == 404:
        return CloudAIError(f"{name} does not offer the model “{model}” to your key{detail}. Choose another model in Settings → AI assistant.")
    if status == 429:
        return _Retryable(f"{name} is limiting how fast you can send requests{detail}. Wait a minute and try again.")
    if status >= 500:
        return _Retryable(f"{name} is busy or unavailable right now{detail}. Try again in a minute.")
    return CloudAIError(f"{name} refused the request{detail}.")


def _open(provider: str, method: str, url: str, headers: dict, payload: dict | None = None, *, model: str = ""):
    from . import __version__
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/json", "Accept": "application/json",
        "User-Agent": f"PersonalJobAgent/{__version__}", **headers})
    try:
        return _OPENER.open(request, timeout=_IDLE_TIMEOUT)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(65536)
        except OSError:
            body = b""
        raise _http_error(provider, exc.code, body, model) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise _Retryable(f"Cannot reach {PROVIDERS[provider]['name']}. Check your internet connection and try again.") from None


def _json(provider: str, response) -> dict:
    try:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    except (TimeoutError, OSError):
        raise _Retryable(f"{PROVIDERS[provider]['name']} stopped responding. Try again.") from None
    finally:
        response.close()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise CloudAIError(f"{PROVIDERS[provider]['name']} returned an unreadable answer. Try again.") from None
    if len(raw) > MAX_RESPONSE_BYTES or not isinstance(value, dict):
        raise CloudAIError(f"{PROVIDERS[provider]['name']} returned an unexpected answer. Try again.")
    return value


def _events(provider: str, response):
    """Server-sent events: yields each event's data, read with a total time limit."""
    started, size, lines = time.monotonic(), 0, []
    try:
        while True:
            if time.monotonic() - started > _TOTAL_TIMEOUT:
                raise TimeoutError
            line = response.readline(_MAX_LINE + 1)
            if not line:
                break
            size += len(line)
            if len(line) > _MAX_LINE or size > MAX_RESPONSE_BYTES:
                raise CloudAIError(f"{PROVIDERS[provider]['name']} sent an oversized answer. Try a shorter request.")
            text = line.decode("utf-8", "replace").rstrip("\r\n")
            if not text:
                if lines:
                    yield "\n".join(lines)
                    lines = []
            elif text.startswith("data:"):
                lines.append(text[6:] if text.startswith("data: ") else text[5:])
        if lines:
            yield "\n".join(lines)
    except (TimeoutError, OSError):
        raise _Retryable(f"{PROVIDERS[provider]['name']} stopped responding while writing. Try again.") from None
    finally:
        response.close()


def _decode(provider: str, data: str):
    try:
        value = json.loads(data)
    except ValueError:
        raise CloudAIError(f"{PROVIDERS[provider]['name']} sent an unreadable answer. Try again.") from None
    return value if isinstance(value, dict) else {}


# --------------------------------------------------------------- models

def _headers(provider: str, key: str) -> dict:
    if provider == "anthropic":
        return {"x-api-key": key, "anthropic-version": "2023-06-01"}
    if provider == "gemini":
        return {"x-goog-api-key": key}
    return {"Authorization": f"Bearer {key}"}


_OPENAI_SKIP = re.compile(r"embedding|tts|whisper|transcri|audio|realtime|image|dall-e|moderation|search|computer-use|instruct|babbage|davinci|sora|codex", re.I)
_GEMINI_SKIP = re.compile(r"embedding|image|tts|live|audio|native|veo|imagen|robotics", re.I)


def list_models(data_dir: Path, provider: str, *, key: str | None = None) -> list[dict]:
    """The provider's chat models available to this key, newest first. Listing is free."""
    _provider(provider)
    key = key if key is not None else _key(data_dir, provider)
    headers, models = _headers(provider, key), []
    if provider == "anthropic":
        cursor = ""
        for _ in range(5):
            value = _json(provider, _open(provider, "GET", "https://api.anthropic.com/v1/models?" + urlencode({"limit": 100, **({"after_id": cursor} if cursor else {})}), headers))
            models += [{"id": item["id"], "name": item.get("display_name") or item["id"]} for item in value.get("data", [])
                       if isinstance(item, dict) and str(item.get("id", "")).startswith("claude-")]
            cursor = value.get("last_id") or ""
            if not value.get("has_more") or not cursor:
                break
    elif provider == "openai":
        value = _json(provider, _open(provider, "GET", "https://api.openai.com/v1/models", headers))
        items = [item for item in value.get("data", []) if isinstance(item, dict) and isinstance(item.get("id"), str)]
        items.sort(key=lambda item: -int(item.get("created") or 0))
        models = [{"id": item["id"], "name": item["id"]} for item in items
                  if re.match(r"(gpt-|o\d|chatgpt)", item["id"]) and not _OPENAI_SKIP.search(item["id"])]
    elif provider == "gemini":
        token = ""
        for _ in range(5):
            value = _json(provider, _open(provider, "GET", "https://generativelanguage.googleapis.com/v1beta/models?" + urlencode({"pageSize": 1000, **({"pageToken": token} if token else {})}), headers))
            for item in value.get("models", []):
                name = str(item.get("name", "")) if isinstance(item, dict) else ""
                if name.startswith("models/gemini") and "generateContent" in (item.get("supportedGenerationMethods") or []) and not _GEMINI_SKIP.search(name):
                    models.append({"id": name[len("models/"):], "name": item.get("displayName") or name[len("models/"):]})
            token = value.get("nextPageToken") or ""
            if not token:
                break
    else:
        value = _json(provider, _open(provider, "GET", "https://api.deepseek.com/models", headers))
        models = [{"id": item["id"], "name": item["id"]} for item in value.get("data", [])
                  if isinstance(item, dict) and isinstance(item.get("id"), str)]
    unique = {}
    for model in models:
        if _MODEL.match(model["id"]):
            unique.setdefault(model["id"], {"id": model["id"], "name": str(model["name"])[:100]})
    return list(unique.values())


# ------------------------------------------------------------ generation

def _anthropic(key: str, model: str, system: str, messages: list[dict], json_mode: bool, max_tokens: int, fallback: bool):
    headers = {**_headers("anthropic", key), "Accept": "text/event-stream"}
    body = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages, "stream": True}
    if fallback:
        # A declined request is answered by another Claude model instead.
        headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
        body["fallbacks"] = "default"
    response = _open("anthropic", "POST", "https://api.anthropic.com/v1/messages", headers, body, model=model)
    for data in _events("anthropic", response):
        event = _decode("anthropic", data)
        kind = event.get("type")
        if kind == "message_start":
            usage = (event.get("message") or {}).get("usage") or {}
            yield "usage_in", sum(int(usage.get(name) or 0) for name in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        elif kind == "content_block_delta":
            delta = event.get("delta") or {}
            if delta.get("type") == "text_delta":
                yield "text", str(delta.get("text") or "")
        elif kind == "message_delta":
            yield "usage_out", int((event.get("usage") or {}).get("output_tokens") or 0)
            reason = (event.get("delta") or {}).get("stop_reason")
            if reason:
                yield "finish", {"max_tokens": "length", "refusal": "refused"}.get(reason, "stop")
        elif kind == "error":
            error = event.get("error") or {}
            message = _clean_message(error.get("message"))
            if error.get("type") in {"overloaded_error", "api_error", "rate_limit_error"}:
                raise _Retryable(f"Claude is busy right now ({message}). Try again in a minute.")
            raise CloudAIError(f"Claude stopped with an error ({message}).")


def _openai(key: str, model: str, system: str, messages: list[dict], json_mode: bool, max_tokens: int, fallback: bool):
    body = {"model": model, "instructions": system, "input": messages, "max_output_tokens": max_tokens,
            "stream": True, "store": False}
    if json_mode:
        body["text"] = {"format": {"type": "json_object"}}
    response = _open("openai", "POST", "https://api.openai.com/v1/responses",
                     {**_headers("openai", key), "Accept": "text/event-stream"}, body, model=model)
    for data in _events("openai", response):
        event = _decode("openai", data)
        kind = event.get("type")
        if kind == "response.output_text.delta":
            yield "text", str(event.get("delta") or "")
        elif kind in {"response.completed", "response.incomplete"}:
            result = event.get("response") or {}
            usage = result.get("usage") or {}
            yield "usage_in", int(usage.get("input_tokens") or 0)
            yield "usage_out", int(usage.get("output_tokens") or 0)
            reason = ((result.get("incomplete_details") or {}).get("reason") or "") if kind == "response.incomplete" else ""
            yield "finish", "refused" if reason == "content_filter" else "length" if reason else "stop"
        elif kind in {"response.failed", "error"}:
            error = (event.get("response") or {}).get("error") or event
            message = _clean_message(error.get("message") or error.get("code"))
            if str(error.get("code") or "") in {"server_error", "rate_limit_exceeded"}:
                raise _Retryable(f"ChatGPT is busy right now ({message}). Try again in a minute.")
            raise CloudAIError(f"ChatGPT stopped with an error ({message}).")


def _gemini(key: str, model: str, system: str, messages: list[dict], json_mode: bool, max_tokens: int, fallback: bool):
    config = {"maxOutputTokens": max_tokens}
    if json_mode:
        config["responseMimeType"] = "application/json"
    body = {"systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "model" if message["role"] == "assistant" else "user", "parts": [{"text": message["content"]}]}
                         for message in messages],
            "generationConfig": config}
    response = _open("gemini", "POST", f"https://generativelanguage.googleapis.com/v1beta/models/{quote(model, safe='')}:streamGenerateContent?alt=sse",
                     {**_headers("gemini", key), "Accept": "text/event-stream"}, body, model=model)
    usage = {}
    for data in _events("gemini", response):
        chunk = _decode("gemini", data)
        if (chunk.get("promptFeedback") or {}).get("blockReason"):
            yield "finish", "refused"
            return
        usage = chunk.get("usageMetadata") or usage
        for candidate in (chunk.get("candidates") or [])[:1]:
            for part in ((candidate.get("content") or {}).get("parts") or []):
                if isinstance(part, dict) and part.get("text") and not part.get("thought"):
                    yield "text", str(part["text"])
            reason = candidate.get("finishReason")
            if reason and reason != "FINISH_REASON_UNSPECIFIED":
                yield "usage_in", int(usage.get("promptTokenCount") or 0)
                yield "usage_out", int(usage.get("candidatesTokenCount") or 0) + int(usage.get("thoughtsTokenCount") or 0)
                yield "finish", "stop" if reason == "STOP" else "length" if reason in {"MAX_TOKENS", "CONTINUATION"} else "refused"


def _deepseek(key: str, model: str, system: str, messages: list[dict], json_mode: bool, max_tokens: int, fallback: bool):
    body = {"model": model, "messages": [{"role": "system", "content": system}, *messages],
            "max_tokens": max_tokens, "stream": True, "stream_options": {"include_usage": True}}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    response = _open("deepseek", "POST", "https://api.deepseek.com/chat/completions",
                     {**_headers("deepseek", key), "Accept": "text/event-stream"}, body, model=model)
    for data in _events("deepseek", response):
        if data.strip() == "[DONE]":
            break
        chunk = _decode("deepseek", data)
        for choice in (chunk.get("choices") or [])[:1]:
            piece = (choice.get("delta") or {}).get("content")
            if piece:
                yield "text", str(piece)
            reason = choice.get("finish_reason")
            if reason == "insufficient_system_resource":
                raise _Retryable("DeepSeek is busy right now. Try again in a minute.")
            if reason:
                yield "finish", {"stop": "stop", "length": "length"}.get(reason, "refused")
        if isinstance(chunk.get("usage"), dict):
            yield "usage_in", int(chunk["usage"].get("prompt_tokens") or 0)
            yield "usage_out", int(chunk["usage"].get("completion_tokens") or 0)


_CLIENTS = {"anthropic": _anthropic, "openai": _openai, "gemini": _gemini, "deepseek": _deepseek}


def complete(data_dir: Path, provider: str, model: str, system: str, messages: list[dict], *,
             json_mode: bool = False, max_tokens: int = 16000, on_text=None, progress=None) -> dict:
    """Stream one answer; on_text receives the whole text so far as it grows.

    Busy or dropped connections are retried twice. An answer that stops early
    (output limit, refusal) raises CloudAIError: no partial text is returned.
    """
    info = _provider(provider)
    model = validate_model(model)
    if not messages or any(message.get("role") not in {"user", "assistant"} or not isinstance(message.get("content"), str) for message in messages):
        raise ValueError("A request needs user and assistant messages with text.")
    key = _key(data_dir, provider)
    fallback = provider == "anthropic" and model in _FALLBACK_MODELS
    for attempt in range(len(_RETRY_PAUSES) + 1):
        _check_allowance(data_dir)
        parts, finish, tokens_in, tokens_out = [], None, 0, 0
        if on_text is not None and attempt:
            on_text("")
        try:
            for kind, value in _CLIENTS[provider](key, model, system, messages, json_mode, max_tokens, fallback):
                if kind == "text" and value:
                    parts.append(value)
                    if on_text is not None:
                        on_text("".join(parts))
                elif kind == "usage_in":
                    tokens_in = max(tokens_in, value)
                elif kind == "usage_out":
                    tokens_out = max(tokens_out, value)
                elif kind == "finish":
                    finish = value
        except _Retryable as exc:
            if tokens_in or tokens_out:
                _record(data_dir, provider, tokens_in, tokens_out)
            if attempt == len(_RETRY_PAUSES):
                raise
            if progress is not None:
                progress(f"{exc} Retrying in {_RETRY_PAUSES[attempt]} s…")
            time.sleep(_RETRY_PAUSES[attempt])
            continue
        except CloudAIError as exc:
            if fallback and "fallback" in str(exc).casefold():
                fallback = False  # An account or model without fallbacks: ask plainly.
                continue
            raise
        _record(data_dir, provider, tokens_in, tokens_out)
        text = "".join(parts)
        if finish == "refused":
            raise CloudAIError(f"{info['name']} declined to answer this request. Rephrase it, or try another model.")
        if finish == "length":
            raise CloudAIError(f"{info['name']} reached its length limit before finishing. Ask for something shorter; no partial answer was kept.")
        if finish is None or not text.strip():
            raise CloudAIError(f"{info['name']} ended without an answer. Try again.")
        return {"text": text, "input_tokens": tokens_in, "output_tokens": tokens_out, "model": model, "provider": provider}
    raise CloudAIError(f"{info['name']} could not answer. Try again.")


class CloudEngine:
    """The provider chosen in Settings, passed where a local model name goes."""

    is_cloud = True

    def __init__(self, data_dir: Path, provider: str, model: str):
        self.data_dir, self.provider, self.model = Path(data_dir), provider, validate_model(model)

    @property
    def label(self) -> str:
        return f"{self.model} ({PROVIDERS[self.provider]['name']})"

    @property
    def name(self) -> str:
        return PROVIDERS[self.provider]["name"]

    def generate(self, system: str, messages: list[dict], *, json_mode: bool = False, max_tokens: int = 16000,
                 on_text=None, progress=None) -> dict:
        return complete(self.data_dir, self.provider, self.model, system, messages, json_mode=json_mode,
                        max_tokens=max_tokens, on_text=on_text, progress=progress)


def engine(data_dir: Path, provider: str | None = None, model: str | None = None) -> CloudEngine | None:
    """The chosen cloud engine, or None when the local model is chosen."""
    settings = read_settings(data_dir)
    provider = provider or settings["provider"]
    if provider == "local":
        return None
    info = _provider(provider)
    if provider not in _keys(data_dir):
        raise CloudAIError(f"Add your {info['name']} API key in Settings → AI assistant first.")
    if provider not in settings["consented"]:
        raise CloudAIError(f"Choose {info['name']} in Settings → AI assistant and confirm what is sent first.")
    return CloudEngine(data_dir, provider, model or settings["models"].get(provider, info["default_model"]))
