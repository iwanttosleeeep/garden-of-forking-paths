from tools.breath.surface import _float_or_default


def test_breath_sorting_preserves_valid_zero_emotion_values():
    assert _float_or_default(0, 0.3) == 0.0
    assert _float_or_default(0.0, 0.5) == 0.0
    assert _float_or_default(None, 0.3) == 0.3
    assert _float_or_default("", 0.5) == 0.5
