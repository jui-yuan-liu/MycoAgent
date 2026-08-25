from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from mycoagent.cli import app
from mycoagent.node.local_config import load_local_config
from mycoagent.node.providers import (
    infer_provider,
    list_llm_models,
    normalize_openai_base_url,
    provider_base_url,
    resolve_for_init,
)


def test_normalize_appends_v1():
    assert normalize_openai_base_url("http://127.0.0.1:8000") == "http://127.0.0.1:8000/v1"
    assert normalize_openai_base_url("http://127.0.0.1:8000/v1/") == "http://127.0.0.1:8000/v1"


def test_infer_and_provider_urls():
    assert infer_provider("") == "echo"
    assert infer_provider("http://127.0.0.1:8000/v1") == "omlx"
    assert infer_provider("http://host.docker.internal:11434/v1") == "ollama"
    assert infer_provider("http://example:9000/v1") == "custom"
    assert provider_base_url("omlx").endswith(":8000/v1")
    assert provider_base_url("ollama").endswith(":11434/v1")


def test_list_llm_models_skips_embeddings():
    response = MagicMock()
    response.is_success = True
    response.json.return_value = {
        "data": [
            {"id": "Qwen3-Coder-8bit"},
            {"id": "bge-m3"},
            {"id": "docs-rerank"},
        ]
    }
    client = MagicMock()
    client.__enter__.return_value.get.return_value = response
    client.__exit__.return_value = False
    with patch("mycoagent.node.providers.httpx.Client", return_value=client):
        models, error = list_llm_models("http://127.0.0.1:8000")
    assert error is None
    assert models == ["Qwen3-Coder-8bit"]


def test_resolve_for_init_omlx_picks_first_chat_model():
    with patch(
        "mycoagent.node.providers.list_llm_models",
        return_value=(["Qwen3-Coder-8bit", "llama-3-8b"], None),
    ):
        url, model, notes = resolve_for_init("omlx", None, None)
    assert url.endswith(":8000/v1")
    assert model == "Qwen3-Coder-8bit"
    assert notes


def test_init_yes_provider_omlx(tmp_path):
    cfg = tmp_path / "agents.yaml"
    with patch(
        "mycoagent.cli.resolve_for_init",
        return_value=("http://host.docker.internal:8000/v1", "Qwen3-Coder-8bit", []),
    ):
        result = CliRunner().invoke(
            app,
            [
                "init",
                "--yes",
                "--provider",
                "omlx",
                "--config",
                str(cfg),
                "--no-apply",
            ],
        )
    assert result.exit_code == 0, result.output
    loaded = load_local_config(cfg)
    assert loaded is not None
    assert loaded.agents[0].llm_url == "http://host.docker.internal:8000/v1"
    assert loaded.agents[0].llm_model == "Qwen3-Coder-8bit"
    assert loaded.agents[0].executor == "auto"
