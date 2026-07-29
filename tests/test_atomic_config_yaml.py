import threading

import pytest
import yaml

import utils


def test_atomic_updates_preserve_independent_sections(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("existing:\n  value: kept\n", encoding="utf-8")
    monkeypatch.setenv("OMBRE_CONFIG_PATH", str(config_path))
    barrier = threading.Barrier(2)

    def update(name):
        barrier.wait()

        def mutate(config):
            config[name] = {"enabled": True}

        utils.atomic_update_config_yaml(mutate)

    threads = [threading.Thread(target=update, args=(name,)) for name in ("journal", "health")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved == {
        "existing": {"value": "kept"},
        "journal": {"enabled": True},
        "health": {"enabled": True},
    }


def test_atomic_update_failure_preserves_previous_config(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    original = "existing:\n  value: kept\n"
    config_path.write_text(original, encoding="utf-8")
    monkeypatch.setenv("OMBRE_CONFIG_PATH", str(config_path))

    def fail_replace(_source, _target):
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(utils.os, "replace", fail_replace)
    with pytest.raises(OSError, match="synthetic replace failure"):
        utils.atomic_update_config_yaml(lambda config: config.update(new=True))

    assert config_path.read_text(encoding="utf-8") == original


def test_embedding_persistence_failure_is_not_hidden(monkeypatch):
    from web import embedding

    def fail_update(_mutate):
        raise OSError("read-only config")

    monkeypatch.setattr(utils, "atomic_update_config_yaml", fail_update)
    with pytest.raises(OSError, match="read-only config"):
        embedding._persist_embedding_yaml({"model": "test"})
