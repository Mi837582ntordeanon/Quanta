"""LLM adapters for Ollama and OpenAI-compatible APIs.

The rest of the app speaks a small internal message format:

    {"role": "user", "content": "..."}
    {"role": "assistant", "content": "...", "tool_calls": [
        {"id": "...", "name": "...", "arguments": {...}}
    ]}
    {"role": "tool", "tool_call_id": "...", "name": "...", "content": "..."}

The adapter converts this to the provider's wire format, streams text deltas
back to the agent and returns the final assistant message with normalised tool
calls.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Generator

REQUEST_TIMEOUT = 600


class LLMError(RuntimeError):
    pass


def _friendly_http_error(exc: urllib.error.HTTPError, provider: str) -> str:
    try:
        raw = exc.read().decode("utf-8", "replace")
        data = json.loads(raw)
        detail = data.get("error") or data.get("message") or raw
        if isinstance(detail, dict):
            detail = detail.get("message") or json.dumps(detail, ensure_ascii=False)
    except Exception:
        detail = ""
    if exc.code == 404 and provider == "ollama":
        return ("Модель не найдена в Ollama. Проверьте имя или загрузите её командой "
                "`ollama pull <модель>`.")
    if exc.code in (401, 403):
        return f"Доступ запрещён ({exc.code}). Проверьте API-ключ."
    return f"Ошибка {provider} ({exc.code}): {detail or exc.reason}"


def _url_join(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


def list_models(settings: dict[str, Any]) -> list[dict[str, Any]]:
    provider = settings.get("provider", "ollama")
    if provider == "ollama":
        url = _url_join(settings.get("ollama_url") or "http://127.0.0.1:11434", "/api/tags")
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.URLError as exc:
            raise LLMError(
                "Не удалось подключиться к Ollama. Убедитесь, что Ollama запущена "
                "(в трее или командой `ollama serve`)."
            ) from exc
        except urllib.error.HTTPError as exc:
            raise LLMError(_friendly_http_error(exc, "ollama")) from exc
        except ValueError as exc:
            raise LLMError("Ollama вернула некорректный ответ.") from exc

        models = []
        for item in data.get("models") or []:
            name = item.get("name") or item.get("model")
            if not name:
                continue
            models.append({
                "name": name,
                "size": item.get("size"),
                "capabilities": item.get("capabilities") or [],
            })
        models.sort(key=lambda m: m["name"].lower())
        return models

    # OpenAI-compatible providers (OpenRouter, LM Studio, DeepSeek, OpenAI …)
    url = _url_join(settings.get("api_url") or "https://api.openai.com/v1", "/models")
    headers = {"Accept": "application/json"}
    key = (settings.get("api_key") or "").strip()
    if key:
        headers["Authorization"] = "Bearer " + key
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raise LLMError(_friendly_http_error(exc, "API")) from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"Не удалось подключиться к API: {exc.reason}") from exc
    except ValueError as exc:
        raise LLMError("API вернул некорректный список моделей.") from exc
    models = []
    for item in data.get("data") or []:
        name = item.get("id") or item.get("name")
        if name:
            models.append({"name": name, "capabilities": []})
    models.sort(key=lambda m: m["name"].lower())
    return models


def _normalise_tool_calls(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, call in enumerate(calls):
        name = call.get("name") or ""
        args = call.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except ValueError:
                args = {"_raw": args}
        if not isinstance(args, dict):
            args = {}
        result.append({
            "id": call.get("id") or f"call_{index}",
            "name": name,
            "arguments": args,
        })
    return [c for c in result if c["name"]]


def _to_ollama_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        if role == "assistant":
            item: dict[str, Any] = {"role": "assistant", "content": msg.get("content") or ""}
            calls = msg.get("tool_calls") or []
            if calls:
                item["tool_calls"] = [
                    {"function": {"name": c.get("name", ""), "arguments": c.get("arguments") or {}}}
                    for c in calls
                ]
            out.append(item)
        elif role == "tool":
            out.append({
                "role": "tool",
                "content": msg.get("content") or "",
                "tool_name": msg.get("name") or "",
            })
        else:
            out.append({"role": role or "user", "content": msg.get("content") or ""})
    return out


def _to_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        if role == "assistant":
            item = {"role": "assistant", "content": msg.get("content") or ""}
            calls = msg.get("tool_calls") or []
            if calls:
                item["tool_calls"] = [
                    {
                        "id": c.get("id") or f"call_{i}",
                        "type": "function",
                        "function": {
                            "name": c.get("name", ""),
                            "arguments": json.dumps(c.get("arguments") or {}, ensure_ascii=False),
                        },
                    }
                    for i, c in enumerate(calls)
                ]
            out.append(item)
        elif role == "tool":
            out.append({
                "role": "tool",
                "tool_call_id": msg.get("tool_call_id") or "",
                "content": msg.get("content") or "",
            })
        else:
            out.append({"role": role or "user", "content": msg.get("content") or ""})
    return out


def stream_chat(
    settings: dict[str, Any],
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> Generator[dict[str, Any], None, None]:
    primary = iter(_stream_provider(settings, messages, tools))
    try:
        first = next(primary)
    except StopIteration:
        return
    except Exception as exc:
        fallbacks = _fallback_settings(settings)
        if not fallbacks or not _retryable_before_output(exc):
            raise
        last_error: Exception = exc
        for fallback in fallbacks:
            candidate = iter(_stream_provider(fallback, messages, tools))
            try:
                first_fallback_event = next(candidate)
            except StopIteration:
                continue
            except Exception as fallback_error:
                last_error = fallback_error
                if _retryable_before_output(fallback_error):
                    continue
                raise
            yield {
                "type": "provider_fallback",
                "provider": fallback["provider"],
                "model": fallback["model"],
                "reason": str(last_error)[:240],
            }
            yield first_fallback_event
            yield from candidate
            return
        raise last_error
    yield first
    # Once a token or tool-call stream has started, retrying another model
    # could duplicate or diverge from text already shown to the user.
    yield from primary


def _stream_provider(
    settings: dict[str, Any], messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None,
) -> Generator[dict[str, Any], None, None]:
    if settings.get("provider", "ollama") == "ollama":
        yield from _stream_ollama(settings, messages, tools)
    else:
        yield from _stream_openai(settings, messages, tools)


def _fallback_settings(settings: dict[str, Any]) -> list[dict[str, Any]]:
    if not settings.get("fallback_enabled"):
        return []
    routes: list[dict[str, Any]] = []
    legacy_model = str(settings.get("fallback_model") or "").strip()
    raw_routes = []
    if legacy_model:
        raw_routes.append({
            "provider": settings.get("fallback_provider") or "openai",
            "model": legacy_model,
            "api_url": settings.get("fallback_api_url") or settings.get("api_url"),
            "api_key": settings.get("fallback_api_key") or settings.get("api_key"),
        })
    configured = settings.get("fallback_routes") or []
    if isinstance(configured, list):
        raw_routes.extend(item for item in configured if isinstance(item, dict))
    primary_key = (str(settings.get("provider") or "ollama"),
                   str(settings.get("model") or ""),
                   str(settings.get("api_url") or settings.get("ollama_url") or ""))
    for raw in raw_routes[:5]:
        provider = str(raw.get("provider") or "openai")
        model = str(raw.get("model") or "").strip()
        if provider not in {"ollama", "openai"} or not model:
            continue
        url_key = str(raw.get("api_url") or raw.get("ollama_url") or
                      (settings.get("api_url") if provider == "openai" else settings.get("ollama_url")) or "")
        if (provider, model, url_key) == primary_key:
            continue
        route = dict(settings)
        route.update({"provider": provider, "model": model, "fallback_enabled": False,
                      "fallback_routes": []})
        if provider == "openai":
            route["api_url"] = raw.get("api_url") or settings.get("api_url")
            route["api_key"] = raw.get("api_key") or settings.get("fallback_api_key") or settings.get("api_key")
        else:
            route["ollama_url"] = raw.get("ollama_url") or settings.get("ollama_url")
        routes.append(route)
    return routes


def planner_route_settings(settings: dict[str, Any]) -> dict[str, Any] | None:
    """Use the first configured reserve for optional multi-step plan refinement."""
    if not settings.get("planner_enabled"):
        return None
    routes = _fallback_settings(settings)
    if not routes:
        return None
    primary = dict(routes[0])
    primary["fallback_enabled"] = len(routes) > 1
    primary["fallback_model"] = ""
    primary["fallback_routes"] = [
        {
            "provider": route["provider"], "model": route["model"],
            "api_url": route.get("api_url", ""), "ollama_url": route.get("ollama_url", ""),
            "api_key": route.get("api_key", ""),
        }
        for route in routes[1:]
    ]
    return primary


def _retryable_before_output(exc: Exception) -> bool:
    if isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError)):
        return True
    message = str(exc).casefold()
    if any(text in message for text in (
        "не удалось подключиться", "не ответила вовремя", "connection refused",
        "connection reset", "timed out", "temporary failure",
    )):
        return True
    if isinstance(exc, LLMError):
        return any(f"({code})" in message for code in (429, 500, 502, 503, 504))
    return False


def effective_runtime_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Return temporary model settings for the selected resource profile."""
    effective = dict(settings)
    if str(settings.get("resource_profile") or "work") == "game":
        effective.update({
            "model_context_size": 8192,
            "model_keep_alive": "2m",
            "model_warmup_on_start": False,
            "stt_device": "cpu",
        })
    return effective


def unload_model(settings: dict[str, Any]) -> dict[str, Any]:
    """Unload only Quanta's selected Ollama model, and only if it is resident."""
    if settings.get("provider", "ollama") != "ollama":
        return {"ok": True, "skipped": "provider is not Ollama"}
    base = _url_join(settings.get("ollama_url") or "http://127.0.0.1:11434", "")
    model = str(settings.get("model") or "").strip()
    if not model:
        return {"ok": False, "error": "Модель Ollama не задана"}
    try:
        req = urllib.request.Request(_url_join(base, "/api/ps"), headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=2) as response:
            loaded = json.loads(response.read().decode("utf-8", "replace")).get("models") or []
        resident = any(
            str(item.get("name") or item.get("model") or "") in (model, model + ":latest")
            for item in loaded if isinstance(item, dict)
        )
        if not resident:
            return {"ok": True, "unloaded": False, "message": "Модель уже выгружена"}
        payload = json.dumps({"model": model, "prompt": "", "stream": False, "keep_alive": 0}).encode("utf-8")
        unload_req = urllib.request.Request(
            _url_join(base, "/api/generate"), data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        with urllib.request.urlopen(unload_req, timeout=30) as response:
            response.read()
        return {"ok": True, "unloaded": True, "message": "Модель Ollama выгружена"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:220]}


def warmup_model(settings: dict[str, Any]) -> dict[str, Any]:
    """Load an Ollama model in the background without adding a chat turn."""
    if settings.get("provider", "ollama") != "ollama":
        return {"ok": True, "skipped": "provider is not Ollama"}
    started = time.perf_counter()
    probe_settings = {**settings, "_quanta_warmup": True}
    messages = [
        {"role": "system", "content": "Ты Кванта. Отвечай кратко и только по-русски."},
        {"role": "user", "content": "Откликнись одним словом."},
    ]
    for _event in _stream_ollama(probe_settings, messages, None):
        pass
    return {"ok": True, "seconds": round(time.perf_counter() - started, 2)}


def _stream_ollama(
    settings: dict[str, Any],
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
) -> Generator[dict[str, Any], None, None]:
    model = settings.get("model") or "qwen3:4b-instruct"
    options = {
        "temperature": float(settings.get("temperature", 0.75)),
        # 12K covers the system/tools prompt plus trimmed chat history,
        # while using less VRAM than 16K on an 8 GB GPU.
        "num_ctx": int(settings.get("model_context_size", 12288)),
    }
    if settings.get("_quanta_warmup"):
        options["num_predict"] = 1
    payload: dict[str, Any] = {
        "model": model,
        "messages": _to_ollama_messages(messages),
        "stream": True,
        "keep_alive": settings.get("model_keep_alive", "5m"),
        "options": options,
    }
    if tools:
        payload["tools"] = tools
    # Qwen3 thinking models spend a lot of time reasoning; Quanta should
    # answer quickly. Instruct-only variants do not accept the flag.
    model_lower = model.lower()
    if model_lower.startswith(("qwen3", "qwen-3")) and "instruct" not in model_lower:
        payload["think"] = False

    url = _url_join(settings.get("ollama_url") or "http://127.0.0.1:11434", "/api/chat")
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/x-ndjson"},
        method="POST",
    )
    content_parts: list[str] = []
    calls: dict[int, dict[str, Any]] = {}
    arg_buffers: dict[int, str] = {}

    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            for raw_line in resp:
                if not raw_line:
                    continue
                line = raw_line.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if data.get("error"):
                    raise LLMError(str(data["error"]))
                message = data.get("message") or {}
                delta = message.get("content") or ""
                if delta:
                    content_parts.append(delta)
                    yield {"type": "delta", "text": delta}

                for tc in message.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    index = fn.get("index")
                    if index is None:
                        index = len(calls)
                    index = int(index)
                    entry = calls.setdefault(index, {
                        "id": tc.get("id") or f"call_{index}",
                        "name": "",
                        "arguments": {},
                    })
                    if tc.get("id"):
                        entry["id"] = tc["id"]
                    if fn.get("name"):
                        entry["name"] = fn["name"]
                    args = fn.get("arguments")
                    if isinstance(args, dict):
                        entry["arguments"] = args
                    elif isinstance(args, str):
                        arg_buffers[index] = arg_buffers.get(index, "") + args
                        try:
                            entry["arguments"] = json.loads(arg_buffers[index])
                        except ValueError:
                            entry["arguments"] = {"_raw": arg_buffers[index]}
                if data.get("done"):
                    break
    except urllib.error.HTTPError as exc:
        raise LLMError(_friendly_http_error(exc, "Ollama")) from exc
    except urllib.error.URLError as exc:
        raise LLMError(
            "Не удалось подключиться к Ollama. Проверьте, что Ollama запущена "
            "и адрес в настройках верный."
        ) from exc
    except TimeoutError as exc:
        raise LLMError("Ollama не ответила вовремя. Попробуйте ещё раз.") from exc

    final_calls = _normalise_tool_calls([calls[i] for i in sorted(calls)])
    yield {
        "type": "final",
        "message": {
            "role": "assistant",
            "content": "".join(content_parts),
            "tool_calls": final_calls,
        },
    }


def _stream_openai(
    settings: dict[str, Any],
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
) -> Generator[dict[str, Any], None, None]:
    model = settings.get("model") or ""
    payload: dict[str, Any] = {
        "model": model,
        "messages": _to_openai_messages(messages),
        "stream": True,
        "temperature": float(settings.get("temperature", 0.75)),
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    url = _url_join(settings.get("api_url") or "https://api.openai.com/v1", "/chat/completions")
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    key = (settings.get("api_key") or "").strip()
    if key:
        headers["Authorization"] = "Bearer " + key

    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    content_parts: list[str] = []
    calls: dict[int, dict[str, Any]] = {}
    arg_buffers: dict[int, str] = {}

    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", "replace").strip()
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                data_text = line[5:].strip()
                if data_text == "[DONE]":
                    break
                try:
                    data = json.loads(data_text)
                except ValueError:
                    continue
                if data.get("error"):
                    err = data["error"]
                    if isinstance(err, dict):
                        err = err.get("message") or json.dumps(err, ensure_ascii=False)
                    raise LLMError(str(err))
                choices = data.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                text = delta.get("content")
                if text:
                    content_parts.append(text)
                    yield {"type": "delta", "text": text}
                for tc in delta.get("tool_calls") or []:
                    index = int(tc.get("index", 0))
                    entry = calls.setdefault(index, {
                        "id": tc.get("id") or f"call_{index}",
                        "name": "",
                        "arguments": {},
                    })
                    if tc.get("id"):
                        entry["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        entry["name"] += fn["name"]
                    if fn.get("arguments"):
                        arg_buffers[index] = arg_buffers.get(index, "") + fn["arguments"]
    except urllib.error.HTTPError as exc:
        raise LLMError(_friendly_http_error(exc, "API")) from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"Не удалось подключиться к API: {exc.reason}") from exc
    except TimeoutError as exc:
        raise LLMError("API не ответил вовремя. Попробуйте ещё раз.") from exc

    final_calls: list[dict[str, Any]] = []
    for index in sorted(calls):
        entry = calls[index]
        raw_args = arg_buffers.get(index, "")
        args = entry.get("arguments") or {}
        if raw_args:
            try:
                args = json.loads(raw_args)
            except ValueError:
                args = {"_raw": raw_args}
        final_calls.append({"id": entry.get("id"), "name": entry.get("name"), "arguments": args})
    final_calls = _normalise_tool_calls(final_calls)

    yield {
        "type": "final",
        "message": {
            "role": "assistant",
            "content": "".join(content_parts),
            "tool_calls": final_calls,
        },
    }
