from pathlib import Path

from ombrebrain.app.profiles import build_default_legacy_profiles
from web.dashboard import STATIC_ASSETS, VERSIONED_ASSETS


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = (ROOT / "frontend" / "dashboard.html").read_text(encoding="utf-8")
HUMAN_SETTINGS = (ROOT / "frontend" / "human-settings.js").read_text(encoding="utf-8")
SELF_PANEL = (ROOT / "frontend" / "self-panel.js").read_text(encoding="utf-8")
BUCKET_MANAGER = (ROOT / "src" / "bucket_manager.py").read_text(encoding="utf-8")


def test_dashboard_loads_feature_scripts_from_static_assets() -> None:
    human_tag = '<script src="/static/human-settings.js"></script>'
    self_tag = '<script src="/static/self-panel.js"></script>'

    assert human_tag in DASHBOARD
    assert self_tag in DASHBOARD
    assert DASHBOARD.index(human_tag) < DASHBOARD.index("// SVG icon strings")
    assert DASHBOARD.index(self_tag) > DASHBOARD.index('id="self-fab"')
    assert "human-settings.js" in STATIC_ASSETS
    assert "self-panel.js" in STATIC_ASSETS
    assert "/static/human-settings.js" in VERSIONED_ASSETS
    assert "/static/self-panel.js" in VERSIONED_ASSETS


def test_extracted_features_keep_their_public_handlers() -> None:
    for function_name in ("loadHumanName", "saveHumanName", "syncExistingHuman"):
        assert f"function {function_name}(" in HUMAN_SETTINGS
        assert f"function {function_name}(" not in DASHBOARD

    for function_name in (
        "openSelfPanel",
        "closeSelfPanel",
        "setSelfFilter",
        "loadSelfEntries",
        "saveSelfEntry",
        "renderSelfEntries",
    ):
        assert f"function {function_name}(" in SELF_PANEL
        assert f"function {function_name}(" not in DASHBOARD


def test_dead_dashboard_and_bucket_manager_scaffolding_is_removed() -> None:
    assert "heartbeat-text" not in DASHBOARD
    assert "Wikilink injection — DISABLED" not in BUCKET_MANAGER
    assert "def _apply_wikilinks" not in BUCKET_MANAGER


def test_all_frontend_assets_share_the_dashboard_policy_boundary() -> None:
    registry = build_default_legacy_profiles()

    assert registry.profile_for_path("frontend/human-settings.js").module == "frontend.dashboard"
    assert registry.profile_for_path("frontend/self-panel.js").module == "frontend.dashboard"
