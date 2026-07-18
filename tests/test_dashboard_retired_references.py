from pathlib import Path


def test_toolbox_does_not_query_retired_tunnel_state():
    dashboard = (Path(__file__).resolve().parent.parent / "frontend" / "dashboard.html").read_text(encoding="utf-8")

    assert "隧道状态:" not in dashboard
    assert "if (r2 && r2.ok)" not in dashboard
