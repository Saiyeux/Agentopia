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

    parsed = _parse_json_object(content)
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
    return message.get("content") or ""


def _ollama_message_text(response: dict[str, Any]) -> str:
    message = response.get("message", {})
    content = message.get("content") or response.get("response") or ""
    return content


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
        "reasoning_effort": "low",
        "enable_thinking": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }
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


def _parse_json_object(content: str) -> dict[str, Any]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```").strip()
        cleaned = cleaned.removesuffix("```").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end >= start:
        cleaned = cleaned[start : end + 1]
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        partial = _parse_partial_action(cleaned)
        if partial is not None:
            return partial
        raise ValueError(f"Model did not return valid JSON: {content}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Model JSON must be an object")
    return parsed


def _parse_partial_action(content: str) -> dict[str, Any] | None:
    if '"speech"' not in content and "'speech'" not in content:
        return None
    speech = _extract_string_field(content, "speech")
    if not speech:
        return None
    inner = _extract_string_field(content, "inner") or ""
    to = _extract_string_field(content, "to")
    return {"speech": speech, "inner": inner, "action": None, "to": to}


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
