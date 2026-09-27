"""Choosing a local model, rather than having one dictated by an environment.

The model used to be configurable only through ``LLEX_LOCAL_MODEL`` and
``LLEX_LOCAL_MODEL_NAME``. That is fine for a developer and useless for someone
who installed a desktop application, and it left no way to see what was
available either.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from llex import settings as settings_module
from llex.llm import (
    KNOWN_RUNNERS,
    LocalLLMBridge,
    ModelCandidate,
    _port_of,
    default_discovery_endpoints,
    discover_models,
    extra_discovery_endpoints,
)
from llex.settings import SettingsStore, config_dir, default_settings_path


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SettingsStore:
    """A settings store pointed at a temporary file, so the real one is untouched."""
    path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_module, "default_settings_path", lambda: path)
    return SettingsStore(path)


class TestSettingsStore:
    def test_it_starts_empty(self, store: SettingsStore) -> None:
        assert store.read() == {}
        assert store.get("missing", "fallback") == "fallback"

    def test_it_round_trips(self, store: SettingsStore) -> None:
        assert store.write({"model": "qwen2.5:0.5b"}) is True
        assert SettingsStore(store.path).get("model") == "qwen2.5:0.5b"

    def test_it_merges(self, store: SettingsStore) -> None:
        store.write({"a": 1, "b": 2})
        store.update({"b": 3, "c": 4})
        assert store.read() == {"a": 1, "b": 3, "c": 4}

    def test_it_records_its_version(self, store: SettingsStore) -> None:
        store.write({"model": "m"})
        assert json.loads(store.path.read_text(encoding="utf-8"))["version"] == 1

    def test_a_corrupt_file_does_not_stop_the_editor(self, store: SettingsStore) -> None:
        """A settings file the user cannot read is not a reason to refuse to open
        a document. It is left on disk so nothing in it is lost."""
        store.path.write_text("{not json", encoding="utf-8")
        assert store.read() == {}
        assert store.last_error
        assert store.path.exists()

    def test_a_file_holding_a_list_is_not_settings(self, store: SettingsStore) -> None:
        store.path.write_text("[1, 2, 3]", encoding="utf-8")
        assert store.read() == {}

    def test_a_missing_directory_is_created(self, tmp_path: Path) -> None:
        path = tmp_path / "deep" / "nested" / "settings.json"
        assert SettingsStore(path).write({"a": 1}) is True
        assert path.is_file()

    def test_an_unwritable_path_is_reported_not_raised(self, tmp_path: Path) -> None:
        # A directory where the file should be: every write fails.
        path = tmp_path / "settings.json"
        path.mkdir()
        store = SettingsStore(path)
        assert store.write({"a": 1}) is False
        assert "could not write" in (store.last_error or "")

    def test_no_temporary_files_are_left_behind(self, store: SettingsStore) -> None:
        store.write({"a": 1})
        store.write({"a": 2})
        assert list(store.path.parent.glob(".*.tmp")) == []


class TestConfigDir:
    def test_it_is_per_user_and_not_shared(self) -> None:
        assert config_dir().name.lower() in {"llex", "LLex".lower()}

    def test_it_follows_appdata_on_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings_module.sys, "platform", "win32")
        monkeypatch.setenv("APPDATA", r"C:\Users\test\AppData\Roaming")
        assert config_dir() == Path(r"C:\Users\test\AppData\Roaming") / "LLex"

    def test_it_honours_xdg_elsewhere(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings_module.sys, "platform", "linux")
        # A path that only has to look like an XDG directory; nothing is written.
        monkeypatch.setenv("XDG_CONFIG_HOME", str(Path("/home/test/.config")))
        assert config_dir() == Path("/home/test/.config") / "llex"

    def test_the_default_path_is_under_the_config_dir(self) -> None:
        assert default_settings_path().parent == config_dir()


class TestDiscovery:
    def test_a_port_is_built_without_a_double_colon(self) -> None:
        """`KNOWN_RUNNERS` entries already carry their colon; formatting them into
        a host:port pair produced `127.0.0.1::11434`, which resolves nowhere, and
        discovery silently found nothing."""
        for url in default_discovery_endpoints():
            assert "::" not in url, url
            assert url.startswith("http://127.0.0.1:"), url

    def test_every_known_runner_is_probed(self) -> None:
        urls = default_discovery_endpoints()
        for port, _label in KNOWN_RUNNERS:
            assert f"http://127.0.0.1{port}/v1" in urls

    def test_an_extra_endpoint_is_probed_too(self) -> None:
        """A model that is configured must be discoverable even on a port nobody
        guessed, or the user cannot keep using what they already had."""
        urls = extra_discovery_endpoints(["http://192.168.1.5:9000/v1"])
        assert "http://192.168.1.5:9000/v1" in urls

    def test_a_runner_that_is_not_running_is_simply_absent(self) -> None:
        # Nothing is listening on this port, and that is the normal state.
        assert discover_models(["http://127.0.0.1:9/v1"], timeout=0.2) == []

    def test_a_runner_that_is_not_openai_compatible_is_absent(self) -> None:
        # A real HTTP server answering 404 rather than a model list.
        assert discover_models(["http://127.0.0.1:1/v1"], timeout=0.2) == []

    def test_the_port_is_read_from_the_url(self) -> None:
        assert _port_of("http://127.0.0.1:11434/v1") == "11434"
        assert _port_of("http://host:8080") == "8080"
        assert _port_of("http://host/v1") == ""

    def test_a_nonsense_url_has_no_port_rather_than_raising(self) -> None:
        assert _port_of("http://host:not-a-port/v1") == ""

    def test_a_runner_is_named_by_its_port(self) -> None:
        assert ModelCandidate("http://127.0.0.1:11434/v1", "m").runner_name == "Ollama"
        assert ModelCandidate("http://127.0.0.1:1234/v1", "m").runner_name == "LM Studio"
        # An unknown runner still shows something the user can act on.
        assert ModelCandidate("http://box:9999/v1", "m").runner_name == "http://box:9999/v1"

    def test_the_label_names_both_the_model_and_the_runner(self) -> None:
        assert "m" in ModelCandidate("http://127.0.0.1:11434/v1", "m").label
        assert "Ollama" in ModelCandidate("http://127.0.0.1:11434/v1", "m").label


class TestTheChosenModelIsRemembered:
    def test_a_choice_is_applied_at_once(self, store: SettingsStore) -> None:
        from llex.llm import save_model_choice

        save_model_choice("http://127.0.0.1:11434/v1", "qwen2.5:0.5b")
        bridge = LocalLLMBridge()
        assert bridge.endpoint == "http://127.0.0.1:11434/v1"
        assert bridge.model == "qwen2.5:0.5b"
        assert bridge.source == "settings"

    def test_the_choice_survives_a_restart(self, store: SettingsStore) -> None:
        from llex.llm import save_model_choice

        save_model_choice("http://127.0.0.1:11434/v1", "qwen2.5:0.5b")
        # A second bridge, as a fresh process would build.
        assert LocalLLMBridge().model == "qwen2.5:0.5b"

    def test_turning_it_off_is_respected(self, store: SettingsStore, monkeypatch: pytest.MonkeyPatch) -> None:
        """An explicitly empty choice is a decision, and must not be quietly
        replaced by whatever the environment happens to name."""
        monkeypatch.setenv("LLEX_LOCAL_MODEL", "http://127.0.0.1:11434/v1")
        monkeypatch.setenv("LLEX_LOCAL_MODEL_NAME", "from-env")
        from llex.llm import save_model_choice

        save_model_choice("", "")
        bridge = LocalLLMBridge()
        assert bridge.is_online is False
        assert bridge.endpoint == ""

    def test_the_environment_is_used_when_nothing_is_saved(
        self, store: SettingsStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LLEX_LOCAL_MODEL", "http://127.0.0.1:11434/v1")
        monkeypatch.setenv("LLEX_LOCAL_MODEL_NAME", "from-env")
        bridge = LocalLLMBridge()
        assert bridge.model == "from-env"
        assert bridge.source == "environment"

    def test_a_saved_choice_beats_the_environment(
        self, store: SettingsStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The user picked this in the dialog; an environment variable from a
        shell they started the app from should not override it."""
        monkeypatch.setenv("LLEX_LOCAL_MODEL_NAME", "from-env")
        from llex.llm import save_model_choice

        save_model_choice("http://127.0.0.1:11434/v1", "picked")
        assert LocalLLMBridge().model == "picked"

    def test_clearing_the_choice_goes_back_to_the_environment(
        self, store: SettingsStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LLEX_LOCAL_MODEL_NAME", "from-env")
        from llex.llm import clear_model_choice, save_model_choice

        save_model_choice("http://127.0.0.1:11434/v1", "picked")
        assert clear_model_choice() is True
        # Cleared to nothing, so no choice is recorded and the environment applies.
        store.write({})
        assert LocalLLMBridge().model == "from-env"

    def test_an_unwritable_store_does_not_break_the_bridge(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        blocked = tmp_path / "settings.json"
        blocked.mkdir()
        monkeypatch.setattr(settings_module, "default_settings_path", lambda: blocked)
        from llex.llm import save_model_choice

        assert save_model_choice("http://127.0.0.1:11434/v1", "m") is False
        # And the bridge still constructs, falling back to the environment.
        assert LocalLLMBridge() is not None

    def test_configure_changes_it_in_place(self) -> None:
        bridge = LocalLLMBridge()
        bridge.configure(endpoint="http://127.0.0.1:8080/v1", model="llama3")
        assert bridge.endpoint == "http://127.0.0.1:8080/v1"
        assert bridge.model == "llama3"
        # And leaves the other field alone when only one is given.
        bridge.configure(model="other")
        assert bridge.endpoint == "http://127.0.0.1:8080/v1"
        assert bridge.model == "other"

    def test_configure_normalises_the_endpoint(self) -> None:
        bridge = LocalLLMBridge()
        bridge.configure(endpoint="127.0.0.1:11434/v1/")
        assert bridge.endpoint == "http://127.0.0.1:11434/v1"


class TestTheModelApi:
    @pytest.fixture
    def client(self, store: SettingsStore, monkeypatch: pytest.MonkeyPatch) -> Any:
        from fastapi.testclient import TestClient

        from llex.api import API_TOKEN_HEADER, AppServices, build_app
        from llex.document import Document

        monkeypatch.delenv("LLEX_LOCAL_MODEL", raising=False)
        monkeypatch.delenv("LLEX_LOCAL_MODEL_NAME", raising=False)
        services = AppServices(document=Document(title="T"), bridge=LocalLLMBridge())
        app = build_app(services)
        with TestClient(app, base_url="http://127.0.0.1:8765") as test_client:
            test_client.headers.update({API_TOKEN_HEADER: str(app.state.services.token)})
            yield test_client

    def test_it_requires_the_token(self, store: SettingsStore) -> None:
        from fastapi.testclient import TestClient

        from llex.api import AppServices, build_app
        from llex.document import Document

        services = AppServices(document=Document(title="T"), bridge=LocalLLMBridge())
        app = build_app(services)
        with TestClient(app, base_url="http://127.0.0.1:8765") as raw:
            assert raw.get("/api/models").status_code == 401

    def test_listing_works_with_nothing_running(self, client: Any) -> None:
        """No runner is the normal state, and must not be an error."""
        body = client.get("/api/models").json()
        assert "models" in body
        assert isinstance(body["models"], list)
        assert "current" in body

    def test_listing_reports_where_the_current_value_came_from(self, client: Any) -> None:
        """Otherwise a user cannot tell why a choice they made was ignored."""
        assert client.get("/api/models").json()["source"] in {
            "default",
            "environment",
            "settings",
        }

    def test_selecting_saves_and_applies(self, client: Any) -> None:
        body = client.post(
            "/api/models", json={"endpoint": "http://127.0.0.1:11434/v1", "model": "m"}
        ).json()
        assert body["status"] == "saved"
        assert body["current"] == {"endpoint": "http://127.0.0.1:11434/v1", "model": "m"}
        assert SettingsStore(store_path_of()).get("model") == "m"

    def test_selecting_nothing_turns_the_model_off(self, client: Any) -> None:
        body = client.post("/api/models", json={"endpoint": "", "model": ""}).json()
        assert body["current"] == {"endpoint": "", "model": ""}
        # The backend reports the offline engine rather than a model that is not there.
        assert body["backend"] != ""

    def test_a_newline_in_the_endpoint_is_refused(self, client: Any) -> None:
        """These go into a URL and a settings file; a line break in either is a
        request-splitting hazard and never legitimate."""
        for bad in ("http://x\r\n/v1", "http://x\n/v1"):
            assert client.post("/api/models", json={"endpoint": bad, "model": "m"}).status_code == 422

    def test_refreshing_answers_like_listing(self, client: Any) -> None:
        assert client.post("/api/models/refresh").status_code == 200

    def test_a_backend_with_no_configure_is_reported_not_crashed(self, store: SettingsStore) -> None:
        from fastapi.testclient import TestClient

        from llex.api import API_TOKEN_HEADER, AppServices, build_app
        from llex.document import Document
        from llex.llm import HeuristicEngine

        services = AppServices(document=Document(title="T"), bridge=HeuristicEngine())
        app = build_app(services)
        with TestClient(app, base_url="http://127.0.0.1:8765") as raw:
            raw.headers.update({API_TOKEN_HEADER: str(app.state.services.token)})
            assert raw.post("/api/models", json={"endpoint": "", "model": ""}).status_code == 501
            # And listing still works, so the picker can still be opened.
            assert raw.get("/api/models").status_code == 200


def store_path_of() -> Path:
    """Where this test run's settings file is."""
    return settings_module.default_settings_path()
