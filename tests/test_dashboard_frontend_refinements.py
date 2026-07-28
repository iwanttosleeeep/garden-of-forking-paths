from pathlib import Path


DASHBOARD = (
    Path(__file__).resolve().parents[1] / "frontend" / "dashboard.html"
).read_text(encoding="utf-8")


def test_fernweh_reuses_the_concept_depth_interaction():
    assert "networkState.mode = 'fernweh';" in DASHBOARD
    assert "networkState.visibleNodes = getNeighbors(hit, networkState.depth, d);" in DASHBOARD
    assert "networkState.visibleNodes = getNeighbors(networkState.focusNode, v, networkData);" in DASHBOARD
    assert "var visibleEdges = edges.filter(function (e)" in DASHBOARD
    assert "FERNWEH · DEPTH " in DASHBOARD
    assert "function _fwViewport(W, H, nodes, positions, focusNode, depth)" in DASHBOARD
    assert "ctx.scale(viewport.scale, viewport.scale);" in DASHBOARD
    assert "(mx - W / 2) / viewport.scale" in DASHBOARD
    assert "networkState.depth = v;" in DASHBOARD
    assert "scale: 1 + (4 - normalizedDepth) * 0.10" in DASHBOARD


def test_reading_uses_senn_and_the_shorter_copy():
    assert ">已读完</button>" in DASHBOARD
    assert "这一段读完了" not in DASHBOARD
    assert "Senn ' + readingPercent(ai" in DASHBOARD
    assert "item.author === 'ai' ? 'Senn' : 'YOU'" in DASHBOARD
    assert "['Senn', progress.ai" in DASHBOARD
    assert "Claude Connector可以通过同一个 <code>read_book</code> 按需读取。" in DASHBOARD
    assert "商业书正文不会被复制" not in DASHBOARD
    assert "微信读书数据也不会写入 Memos" not in DASHBOARD


def test_radio_login_copy_is_clean_and_keeps_each_url():
    assert ">打开登录链接</a>" in DASHBOARD
    assert "打开登录链接 ' + (index + 1)" not in DASHBOARD
    assert "没有播放器，也不会把 Memos、日记或聊天内容发送给网易云。" not in DASHBOARD


def test_memo_detail_action_labels_are_centered():
    detail_actions = DASHBOARD.split(".detail-actions button {", 1)[1].split("}", 1)[0]

    assert "display: inline-flex !important;" in detail_actions
    assert "justify-content: center !important;" in detail_actions
    assert "text-align: center !important;" in detail_actions
    assert "position: relative !important;" in detail_actions
    assert ".detail-actions button > svg" in DASHBOARD
    assert "left: 8px !important;" in DASHBOARD


def test_small_compose_and_memo_edit_labels_stay_compact():
    self_button = DASHBOARD.split(".self-compose > button {", 1)[1].split("}", 1)[0]

    assert "height: 28px;" in self_button
    assert "font-size: 10px;" in self_button
    assert 'font-size:11px;font-weight:500;color:var(--accent)' in DASHBOARD


def test_retired_network_shims_and_state_are_removed():
    assert "function renderConceptNetwork(" not in DASHBOARD
    assert "function drawConceptNetwork(" not in DASHBOARD
    assert "networkState.panOffset" not in DASHBOARD
    assert "networkState.scale" not in DASHBOARD
