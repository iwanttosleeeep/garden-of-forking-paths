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


def test_radio_has_only_human_and_senn_playlist_shelves():
    radio_view = DASHBOARD.split('<div class="content" id="radio-view"', 1)[1].split(
        '<div class="content" id="health-view"', 1
    )[0]

    assert "Ainsley的歌单" in radio_view
    assert "Senn的歌单" in radio_view
    assert "展示给 Senn" in radio_view
    assert "我收藏的" not in radio_view
    assert "每日推荐" not in radio_view
    assert "私人 FM" not in radio_view
    assert "雷达歌单" not in radio_view


def test_radio_keeps_paired_ids_and_renders_senn_notes():
    assert "original_id:String" in DASHBOARD
    assert "encrypted_id:String" in DASHBOARD
    assert "data-radio-ref" in DASHBOARD
    assert "setRadioExposure(this)" in DASHBOARD
    assert "<strong>Senn：</strong>" in DASHBOARD


def test_radio_reference_is_encoded_before_entering_html_attributes():
    assert "encodeURIComponent(JSON.stringify(reference || {}))" in DASHBOARD
    assert "JSON.parse(decodeURIComponent(String(value || '')))" in DASHBOARD
    assert "esc(JSON.stringify(reference))" not in DASHBOARD
    assert "decodeRadioReference(input.dataset.radioRef)" in DASHBOARD
    assert "decodeRadioReference(serializedReference)" in DASHBOARD


def test_radio_shelves_are_separate_cards():
    assert 'class="radio-shelves"' in DASHBOARD
    assert DASHBOARD.count('class="radio-shelf-button') == 2
    assert '.radio-shelf-button[data-radio-view="human"]' in DASHBOARD
    assert '.radio-shelf-button[data-radio-view="senn"]' in DASHBOARD


def test_radio_light_controls_override_the_dark_section_text_color():
    assert '#radio-view .radio-shelf-button[data-radio-view="human"]' in DASHBOARD
    assert '#radio-view .radio-shelf-button[data-radio-view="senn"]' in DASHBOARD
    assert "color:#45391F !important;" in DASHBOARD
    assert "#radio-view .radio-card button" in DASHBOARD
    assert "color:#2F4F4F !important;" in DASHBOARD
    assert "background:#CFE7DB !important;" in DASHBOARD
    assert "#radio-view .btn-primary { color:#45391F !important; text-shadow:none; }" in DASHBOARD


def test_senn_playlist_shelf_uses_the_neutral_radio_card_surface():
    senn_shelf = DASHBOARD.split(
        '#radio-view .radio-shelf-button[data-radio-view="senn"] {', 1
    )[1].split("}", 1)[0]

    assert "background:#F4EDDA !important;" in senn_shelf
    assert "border:1px solid #6E9486 !important;" in senn_shelf
    assert "#D9A8A0" not in senn_shelf


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
