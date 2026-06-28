from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .db import connect


DEFAULT_PROVIDER = os.getenv("AGENTOPIA_LLM_PROVIDER", "lmstudio")
DEFAULT_BASE_URL = os.getenv("LMSTUDIO_BASE_URL", "http://127.0.0.1:1234/v1")
DEFAULT_MODEL = os.getenv("LMSTUDIO_MODEL", "qwen-agentworld-35b-a3b")


@dataclass(frozen=True)
class LlmSettings:
    provider: str
    base_url: str
    model: str
    api_key: str = ""


ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "speech": {"type": "string"},
        "inner": {"type": "string"},
        "action": {
            "anyOf": [
                {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "target": {"type": ["string", "null"]},
                        "detail": {"type": "string"},
                    },
                    "required": ["type", "target", "detail"],
                    "additionalProperties": False,
                },
                {"type": "null"},
            ]
        },
        "to": {"type": ["string", "null"]},
        "topic": {"type": "string"},
    },
    "required": ["speech", "inner", "action", "to", "topic"],
    "additionalProperties": False,
}


def get_settings() -> LlmSettings:
    try:
        with connect() as conn:
            row = conn.execute("SELECT * FROM llm_settings WHERE id = 1").fetchone()
    except Exception:
        row = None
    if row is None:
        return LlmSettings(DEFAULT_PROVIDER, DEFAULT_BASE_URL, DEFAULT_MODEL, "")
    return LlmSettings(
        provider=row["provider"],
        base_url=row["base_url"].rstrip("/"),
        model=row["model"],
        api_key=row["api_key"] or "",
    )


def save_settings(settings: LlmSettings) -> LlmSettings:
    provider = settings.provider
    if provider not in {"lmstudio", "ollama", "api"}:
        raise ValueError("provider must be lmstudio, ollama, or api")
    base_url = settings.base_url.strip().rstrip("/")
    model = settings.model.strip()
    api_key = settings.api_key.strip()
    if not base_url:
        raise ValueError("base_url is required")
    if not model:
        raise ValueError("model is required")
    with connect() as conn:
        if not api_key:
            current = conn.execute("SELECT api_key FROM llm_settings WHERE id = 1").fetchone()
            api_key = (current["api_key"] if current else "") or ""
        conn.execute(
            """
            INSERT INTO llm_settings (id, provider, base_url, model, api_key, updated_at)
            VALUES (1, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
              provider = excluded.provider,
              base_url = excluded.base_url,
              model = excluded.model,
              api_key = excluded.api_key,
              updated_at = CURRENT_TIMESTAMP
            """,
            (provider, base_url, model, api_key),
        )
    return LlmSettings(provider, base_url, model, api_key)


def public_settings(settings: LlmSettings | None = None) -> dict[str, Any]:
    current = settings or get_settings()
    return {
        "provider": current.provider,
        "base_url": current.base_url,
        "model": current.model,
        "api_key_set": bool(current.api_key),
    }


def list_models(settings: LlmSettings | None = None) -> dict[str, Any]:
    current = settings or get_settings()
    if current.provider == "ollama":
        data = _request_json(current, "GET", "/api/tags")
        models = data.get("models", [])
        return {
            "data": [
                {
                    "id": item.get("name", ""),
                    "name": item.get("name", ""),
                    "modified_at": item.get("modified_at"),
                    "size": item.get("size"),
                }
                for item in models
                if item.get("name")
            ]
        }
    return _request_json(current, "GET", "/models")


def chat(
    *,
    system: str,
    user: str,
    temperature: float = 0.7,
    max_tokens: int = 512,
) -> dict[str, Any]:
    """
    Simple chat completion that returns plain text content.
    Returns: {"content": str, "usage": dict}
    """
    settings = get_settings()
    if settings.provider == "ollama":
        response = _ollama_chat_completion(
            settings=settings,
            system=system,
            user=user,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        content = _ollama_message_text(response)
    else:
        # OpenAI-compatible (LM Studio, API)
        response = _openai_chat_completion(
            settings=settings,
            schema_name="",
            schema={},
            system=system,
            user=user,
            temperature=temperature,
            max_tokens=max_tokens,
            structured=False,
        )
        content = _openai_message_text_plain(response)
    usage = response.get("usage", {})
    return {"content": content, "usage": usage}


def chat_json(
    *,
    system: str,
    user: str,
    temperature: float = 0.8,
    max_tokens: int = 512,
) -> dict[str, Any]:
    return chat_json_schema(
        schema_name="agentopia_action",
        schema=ACTION_SCHEMA,
        system=system,
        user=user,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def chat_json_schema(
    *,
    schema_name: str,
    schema: dict[str, Any],
    system: str,
    user: str,
    temperature: float = 0.5,
    max_tokens: int = 512,
) -> dict[str, Any]:
    settings = get_settings()
    if settings.provider == "ollama":
        response = _ollama_chat_completion(
            settings=settings,
            system=system,
            user=user,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        content = _ollama_message_text(response)
    else:
        use_structured = _supports_structured_output(settings)
        response = _openai_chat_completion(
            settings=settings,
            schema_name=schema_name,
            schema=schema,
            system=system,
            user=user,
            temperature=temperature,
            max_tokens=max_tokens,
            structured=use_structured,
        )
        content = _openai_message_text(response)
        if use_structured and not content.strip():
            response = _openai_chat_completion(
                settings=settings,
                schema_name=schema_name,
                schema=schema,
                system=system,
                user=user,
                temperature=temperature,
                max_tokens=max_tokens,
                structured=False,
            )
            content = _openai_message_text(response)

    parsed = _parse_json_object(content, schema_name=schema_name)
    return {
        "model": response.get("model", settings.model),
        "content": content,
        "parsed": parsed,
        "usage": response.get("usage", {}),
    }


def _supports_structured_output(settings: LlmSettings) -> bool:
    if settings.provider == "api":
        return True
    return not settings.model.startswith("qwen-agentworld")


def _openai_message_text(response: dict[str, Any]) -> str:
    message = response["choices"][0]["message"]
    content = _content_to_text(message.get("content"))
    if content:
        return content
    for key in ("text", "reasoning_content", "reasoning", "thinking"):
        fallback = _content_to_text(message.get(key))
        if fallback and _looks_like_contract_json(fallback):
            return fallback
    return ""


def _openai_message_text_plain(response: dict[str, Any]) -> str:
    message = response["choices"][0]["message"]
    for key in ("content", "text", "reasoning_content", "reasoning", "thinking"):
        content = _content_to_text(message.get(key))
        if content:
            return content
    choice_text = _content_to_text(response["choices"][0].get("text"))
    if choice_text:
        return choice_text
    return ""


def _ollama_message_text(response: dict[str, Any]) -> str:
    message = response.get("message", {})
    return _content_to_text(message.get("content")) or _content_to_text(response.get("response"))


def _content_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(part for part in parts if part).strip()
    return str(value)


def _looks_like_contract_json(text: str) -> bool:
    return "{" in text and (
        '"speech"' in text
        or "'speech'" in text
        or '"success"' in text
        or "'success'" in text
        or '"deltas"' in text
        or "'deltas'" in text
        or '"opening"' in text
        or "'opening'" in text
        or '"narration"' in text
        or "'narration'" in text
    )


def _openai_chat_completion(
    *,
    settings: LlmSettings,
    schema_name: str,
    schema: dict[str, Any],
    system: str,
    user: str,
    temperature: float,
    max_tokens: int,
    structured: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": settings.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    # Note: Removed reasoning_effort, enable_thinking, and chat_template_kwargs
    # as they were causing the model to generate reasoning tokens but empty content
    if structured:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": True,
                "schema": schema,
            },
        }
    return _request_json(settings, "POST", "/chat/completions", payload)


def _ollama_chat_completion(
    *,
    settings: LlmSettings,
    system: str,
    user: str,
    temperature: float,
    max_tokens: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": settings.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }
    return _request_json(settings, "POST", "/api/chat", payload)


def _request_json(
    settings: LlmSettings,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"
    request = urllib.request.Request(
        f"{settings.base_url}{path}",
        data=body,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{settings.provider} HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{settings.provider} request failed: {exc}") from exc


def _parse_json_object(content: str, schema_name: str = "") -> dict[str, Any]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```").strip()
        cleaned = cleaned.removesuffix("```").strip()
    candidates = _json_object_candidates(cleaned)
    if candidates:
        scored = sorted(
            candidates,
            key=lambda item: _candidate_score(item, schema_name),
            reverse=True,
        )
        for candidate in scored:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    start = cleaned.find("{")
    if start >= 0:
        cleaned = cleaned[start:]
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        partial = _parse_partial_action(cleaned) if schema_name == "agentopia_action" else None
        if partial is not None:
            return partial
        raise ValueError(f"Model did not return valid JSON: {content}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Model JSON must be an object")
    return parsed


def _parse_partial_action(content: str) -> dict[str, Any] | None:
    speech = _extract_string_field(content, "speech") or _extract_unclosed_string_field(content, "speech")
    if not speech:
        speech = _first_natural_sentence(content)
    if not speech:
        return None
    inner = _extract_string_field(content, "inner") or ""
    to = _extract_string_field(content, "to")
    topic = _extract_string_field(content, "topic") or ""
    return {"speech": speech, "inner": inner, "action": None, "to": to, "topic": topic}


def _json_object_candidates(content: str) -> list[str]:
    candidates: list[str] = []
    depth = 0
    start: int | None = None
    in_string = False
    escape = False
    quote = ""
    for index, char in enumerate(content):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                in_string = False
            continue
        if char in {'"', "'"}:
            in_string = True
            quote = char
            continue
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(content[start : index + 1])
                start = None
    return candidates


def _candidate_score(candidate: str, schema_name: str) -> int:
    score = len(candidate)
    if schema_name == "agentopia_action":
        for field in ('"speech"', '"inner"', '"action"', '"to"', '"topic"'):
            if field in candidate:
                score += 1000
    elif schema_name == "agentopia_verdict":
        for field in ('"success"', '"narration"', '"deltas"'):
            if field in candidate:
                score += 1000
    elif schema_name == "agentopia_world_event":
        for field in ('"narration"',):
            if field in candidate:
                score += 1000
    elif schema_name == "agentopia_opening":
        for field in ('"opening"',):
            if field in candidate:
                score += 1000
    return score


def _extract_string_field(content: str, field: str) -> str | None:
    patterns = [
        rf'"{field}"\s*:\s*"((?:[^"\\]|\\.)*)"',
        rf"'{field}'\s*:\s*'((?:[^'\\]|\\.)*)'",
    ]
    for pattern in patterns:
        match = re.search(pattern, content)
        if match:
            try:
                return json.loads(f'"{match.group(1)}"')
            except json.JSONDecodeError:
                return match.group(1)
    return None


def _extract_unclosed_string_field(content: str, field: str) -> str | None:
    patterns = [
        rf'"{field}"\s*:\s*"((?:[^"\\]|\\.)*)$',
        rf"'{field}'\s*:\s*'((?:[^'\\]|\\.)*)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, content, flags=re.S)
        if not match:
            continue
        value = match.group(1)
        for marker in ('","inner"', '","action"', '","to"', '","topic"', "\n"):
            value = value.split(marker, 1)[0]
        return _decode_jsonish_string(value)
    return None


def _decode_jsonish_string(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except json.JSONDecodeError:
        return value


def _first_natural_sentence(content: str) -> str | None:
    text = re.sub(r"<[^>]+>", " ", content)
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"[{}\[\]\"]", " ", text)
    text = " ".join(text.split())
    if not text:
        return None
    for prefix in ("speech:", "台词：", "台词:", "回复：", "回复:"):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
    matches = re.findall(r"[\u4e00-\u9fff][^。！？!?]{1,120}[。！？!?]?", text)
    if not matches:
        return None
    return matches[0].strip(" ，,;；")
