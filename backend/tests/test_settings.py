"""Shared settings read/edit endpoints and config persistence."""
import smoothie.config as config_mod


def test_get_settings_returns_current(client):
    res = client.get("/api/settings")
    assert res.status_code == 200
    body = res.json()
    assert "summary_model" in body
    assert "transcription_model" in body
    # Removed account settings and filesystem paths are not exposed.
    assert "auth_secret" not in body
    assert "data_dir" not in body
    assert "ffmpeg_bin" not in body


def test_put_updates_and_persists(client):
    res = client.put(
        "/api/settings", json={"summary_model": "gpt-4o-mini"}
    )
    assert res.status_code == 200
    assert res.json()["summary_model"] == "gpt-4o-mini"

    # Persisted to config.yaml.
    import yaml

    data = yaml.safe_load(config_mod.CONFIG_PATH.read_text())
    assert data["summary_model"] == "gpt-4o-mini"

    # And reflected on a subsequent GET (live in-memory apply).
    again = client.get("/api/settings").json()
    assert again["summary_model"] == "gpt-4o-mini"


def test_put_ignores_hidden_fields(client):
    res = client.put(
        "/api/settings",
        json={"auth_secret": "hacked", "data_dir": "/evil", "summary_model": "ok"},
    )
    assert res.status_code == 200
    assert res.json()["summary_model"] == "ok"
    # Hidden fields are never returned (and were not applied).
    assert "auth_secret" not in res.json()
    assert "data_dir" not in res.json()


def test_put_rejects_invalid_types(client):
    # transcription_gpu_device must be an int.
    res = client.put(
        "/api/settings", json={"transcription_gpu_device": "not-an-int"}
    )
    assert res.status_code == 422


def test_transcription_device_and_legacy_model_persist(client):
    res = client.put("/api/settings", json={
        "transcription_model": "mlx-community/whisper-small-mlx",
        "transcription_device": "cpu",
        "transcription_gpu_device": 1,
    })
    assert res.status_code == 200
    assert res.json()["transcription_model"] == "small"
    assert res.json()["transcription_device"] == "cpu"
    assert res.json()["transcription_gpu_device"] == 1
    config_mod.get_settings.cache_clear()
    reloaded = config_mod.get_settings()
    assert reloaded.transcription_model == "small"
    assert reloaded.transcription_device == "cpu"
    for invalid in [{"transcription_device": "unknown"}, {"transcription_gpu_device": -1}]:
        assert client.put("/api/settings", json=invalid).status_code == 422


def test_removed_account_settings_are_ignored(client):
    res = client.put("/api/settings", json={
        "auth_secret": "obsolete", "auth_min_password_len": 1,
        "admin_usernames": ["someone"], "summary_model": "local-model",
    })
    assert res.status_code == 200
    assert res.json()["summary_model"] == "local-model"
    assert not any(key.startswith("auth_") or key == "admin_usernames" for key in res.json())
    assert "admin_usernames" not in config_mod.CONFIG_PATH.read_text()
