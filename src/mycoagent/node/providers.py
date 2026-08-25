from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import httpx

PROVIDER_ECHO = "echo"
PROVIDER_OMLX = "omlx"
PROVIDER_OLLAMA = "ollama"
PROVIDER_CUSTOM = "custom"
PROVIDERS = (PROVIDER_ECHO, PROVIDER_OMLX, PROVIDER_OLLAMA, PROVIDER_CUSTOM)

OMLX_PORT = 8000
OLLAMA_PORT = 11434
_SKIP_MODEL_FRAGMENTS = ("embed", "rerank", "bge-", "e5-", "gte-", "minilm")


def _in_container() -> bool:
    return Path("/.dockerenv").exists() or bool(os.environ.get("KUBERNETES_SERVICE_HOST"))


def _host() -> str:
    return "host.docker.internal" if _in_container() else "127.0.0.1"


def provider_base_url(provider: str) -> str:
    name = provider.strip().lower()
    if name == PROVIDER_OMLX:
        return f"http://{_host()}:{OMLX_PORT}/v1"
    if name == PROVIDER_OLLAMA:
        return f"http://{_host()}:{OLLAMA_PORT}/v1"
    return ""


def infer_provider(url: str) -> str:
    raw = url.strip().lower()
    if not raw:
        return PROVIDER_ECHO
    if f":{OMLX_PORT}" in raw:
        return PROVIDER_OMLX
    if f":{OLLAMA_PORT}" in raw:
        return PROVIDER_OLLAMA
    return PROVIDER_CUSTOM


def normalize_openai_base_url(raw: str) -> str:
    url = raw.strip().rstrip("/")
    if not url:
        return ""
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    path = parsed.path.rstrip("/")
    if not path:
        parsed = parsed._replace(path="/v1")
        return urlunparse(parsed).rstrip("/")
    return url


def _is_chat_model(model_id: str) -> bool:
    lowered = model_id.lower()
    return not any(fragment in lowered for fragment in _SKIP_MODEL_FRAGMENTS)


def list_llm_models(
    base_url: str,
    api_key: str = "",
    timeout: float = 3.0,
) -> tuple[list[str], str | None]:
    url = normalize_openai_base_url(base_url)
    if not url:
        return [], "empty LLM URL"
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(f"{url}/models", headers=headers)
    except Exception as exc:  # noqa: BLE001 — listing is advisory
        return [], f"GET /models failed: {exc}"
    if not response.is_success:
        return [], f"GET /models HTTP {response.status_code}"
    try:
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        return [], f"GET /models invalid JSON: {exc}"
    rows = payload.get("data") if isinstance(payload, dict) else None
    ids: list[str] = []
    for item in rows or []:
        if isinstance(item, dict) and item.get("id"):
            ids.append(str(item["id"]))
        elif isinstance(item, str) and item.strip():
            ids.append(item.strip())
    chat = [model_id for model_id in ids if _is_chat_model(model_id)]
    chosen = chat or ids
    if not chosen:
        return [], "GET /models returned no chat models"
    return chosen, None


def detect_preferred_provider(timeout: float = 0.8) -> str:
    for name in (PROVIDER_OMLX, PROVIDER_OLLAMA):
        models, error = list_llm_models(provider_base_url(name), timeout=timeout)
        if error is None and models:
            return name
    return PROVIDER_ECHO


def resolve_for_init(
    provider: str | None,
    llm_url: str | None,
    llm_model: str | None,
    llm_key: str = "",
) -> tuple[str, str, list[str]]:
    """Return (openai_base_url, model, notes). Empty URL means Echo."""
    notes: list[str] = []
    name = (provider or "").strip().lower()
    if name and name not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}; use {', '.join(PROVIDERS)}")
    url = (llm_url or "").strip()
    if name == PROVIDER_ECHO:
        return "", "", notes
    if name in {PROVIDER_OMLX, PROVIDER_OLLAMA} and not url:
        url = provider_base_url(name)
    url = normalize_openai_base_url(url)
    model = (llm_model or "").strip()
    if url and not model:
        models, error = list_llm_models(url, llm_key)
        if error:
            notes.append(error)
        elif models:
            model = models[0]
            extra = ", ".join(models[:8])
            if len(models) > 1:
                notes.append(f"using first chat model {model!r}; available: {extra}")
    return url, model, notes
