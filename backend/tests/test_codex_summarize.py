"""Local ChatGPT/Codex adapter: credentials, subprocess failures, and output contract."""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from smoothie import config
from smoothie.codex_summarize import CodexProvider
from smoothie.summarize import ApiEndpointProvider, SummaryError, get_provider


def run(coroutine):
    return asyncio.run(coroutine)


def test_structured_summary_and_title_use_cli_managed_credentials(monkeypatch, tmp_path):
    provider = CodexProvider("/fake/codex", tmp_path, "test-model")
    calls = []
    async def execute(args, **kwargs):
        calls.append((args, kwargs))
        if args == ["login", "status"]:
            return 0, b"", b"Logged in using ChatGPT"
        schema = json.loads(Path(args[args.index("--output-schema") + 1]).read_text())
        data = {"title": "Team Launch Plan", "summary": "Launch on Friday."}
        Path(args[args.index("--output-last-message") + 1]).write_text(json.dumps({k: data[k] for k in schema["required"]}))
        return 0, b"", b""
    monkeypatch.setattr(provider, "_communicate", execute)
    result = run(provider.summarize("Launch on Friday."))
    assert result.title == "Team Launch Plan"
    assert result.summary == "Launch on Friday."
    assert run(provider.generate_title(result.summary)) == "Team Launch Plan"
    args, kwargs = calls[1]
    assert args[args.index("--model") + 1] == "test-model"
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert "--ephemeral" in args and "--ignore-user-config" in args
    assert "shell_tool" in args and "plugins" in args and "hooks" in args
    assert args[-1] == "-"
    assert "Launch on Friday." in kwargs["data"].decode()
    assert not Path(kwargs["cwd"]).exists()  # temporary outputs cleaned up
    assert not (tmp_path / "auth.json").exists()  # never created or copied by the adapter


@pytest.mark.parametrize("status", [b"Logged in using an API key", b"Not logged in"])
def test_subscription_provider_rejects_missing_or_api_key_login(monkeypatch, tmp_path, status):
    provider = CodexProvider("/fake/codex", tmp_path)
    communicate = AsyncMock(return_value=(0, status, b""))
    monkeypatch.setattr(provider, "_communicate", communicate)
    with pytest.raises(SummaryError, match="ChatGPT login is required"):
        run(provider.summarize("Meeting transcript"))
    communicate.assert_awaited_once()


def test_environment_preserves_login_home_without_api_billing_overrides(monkeypatch, tmp_path):
    for key in ["OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN"]:
        monkeypatch.setenv(key, "should-not-be-used")
    env = CodexProvider("codex", tmp_path)._environment()
    assert env["CODEX_HOME"] == str(tmp_path)
    assert not any(key in env for key in ["OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN"])


@pytest.mark.parametrize("diagnostic, expected", [
    (b"usage limit reached PRIVATE TRANSCRIPT", "usage limit"),
    (b"other failure PRIVATE TRANSCRIPT", "failed"),
])
def test_cli_errors_do_not_leak_transcript(monkeypatch, tmp_path, diagnostic, expected):
    provider = CodexProvider("codex", tmp_path)
    communicate = AsyncMock(side_effect=[(0, b"Logged in using ChatGPT", b""), (1, b"", diagnostic)])
    monkeypatch.setattr(provider, "_communicate", communicate)
    with pytest.raises(SummaryError, match=expected) as error:
        run(provider.summarize("PRIVATE TRANSCRIPT"))
    assert "PRIVATE TRANSCRIPT" not in str(error.value)


def test_invalid_or_empty_response_is_not_saved_as_summary(monkeypatch, tmp_path):
    provider = CodexProvider("codex", tmp_path)
    async def execute(args, **kwargs):
        if args == ["login", "status"]:
            return 0, b"Logged in using ChatGPT", b""
        Path(args[args.index("--output-last-message") + 1]).write_text('{"title":"Title", "summary":""}')
        return 0, b"", b""
    monkeypatch.setattr(provider, "_communicate", execute)
    with pytest.raises(SummaryError, match="requested title/summary JSON"):
        run(provider.summarize("Transcript"))


def test_timed_out_cli_is_killed_and_reaped(monkeypatch, tmp_path):
    class Process:
        returncode = None
        killed = False
        async def communicate(self, data=None):
            if not self.killed:
                await asyncio.sleep(5)
            return b"", b""
        def kill(self):
            self.killed = True
            self.returncode = -9
    process = Process()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    provider = CodexProvider("codex", tmp_path, timeout=0.01)
    with pytest.raises(SummaryError, match="timed out"):
        run(provider._communicate(["exec"]))
    assert process.killed


def test_provider_switch_is_persisted_without_exposing_paths(client):
    response = client.put("/api/settings", json={
        "summary_provider": "codex", "summary_codex_model": "test-model",
        "codex_home": "/untrusted", "codex_bin": "/untrusted",
    })
    assert response.status_code == 200
    assert response.json()["summary_provider"] == "codex"
    assert "codex_home" not in response.json() and "codex_bin" not in response.json()
    provider = get_provider()
    assert isinstance(provider, CodexProvider)
    assert provider.model == "test-model"
    assert config.load_config().summary_provider == "codex"
    response = client.put("/api/settings", json={"summary_provider": "api"})
    assert response.status_code == 200
    assert isinstance(get_provider(), ApiEndpointProvider)
    assert client.put("/api/settings", json={"summary_codex_timeout": 0}).status_code == 422
