from pathlib import Path


def test_toolbox_does_not_query_retired_tunnel_state():
    dashboard = (Path(__file__).resolve().parent.parent / "frontend" / "dashboard.html").read_text(encoding="utf-8")

    assert "隧道状态:" not in dashboard
    assert "if (r2 && r2.ok)" not in dashboard


def test_dashboard_uses_memo_language_for_visible_memory_controls():
    dashboard = (Path(__file__).resolve().parent.parent / "frontend" / "dashboard.html").read_text(encoding="utf-8")

    assert "在备忘录详情页用" in dashboard
    assert "breath 默认条数" in dashboard
    assert "检查重复备忘录" in dashboard


def test_me_settings_card_uses_the_standard_full_width_flow_layout():
    dashboard = (Path(__file__).resolve().parent.parent / "frontend" / "dashboard.html").read_text(encoding="utf-8")

    assert "#settings-view #sec-me" in dashboard
    assert "width: 100%;" in dashboard
    assert "text-align: left;" in dashboard
    assert "#sec-me { min-height" not in dashboard


def test_me_settings_card_does_not_close_before_logout():
    dashboard = (Path(__file__).resolve().parent.parent / "frontend" / "dashboard.html").read_text(encoding="utf-8")

    assert "      <div>\n        </div>\n      </div>\n\n      <div>\n        <div style=\"font-weight:600;font-size:13px;margin-bottom:6px;\">退出登录" not in dashboard
