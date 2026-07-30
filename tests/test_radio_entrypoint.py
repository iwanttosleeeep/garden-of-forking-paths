from pathlib import Path


ENTRYPOINT = (
    Path(__file__).resolve().parents[1] / "entrypoint.sh"
).read_text(encoding="utf-8")


def test_radio_device_identity_is_not_initialized_as_empty_json():
    assert ': > "$RADIO_STATE_DIR/device.json"' not in ENTRYPOINT
    assert '[ -f "$RADIO_DEVICE_FILE" ] && [ ! -s "$RADIO_DEVICE_FILE" ]' in ENTRYPOINT
    assert 'mv "$RADIO_DEVICE_FILE" "$RADIO_DEVICE_FILE.empty"' in ENTRYPOINT


def test_radio_device_identity_stays_on_the_persistent_volume():
    assert 'RADIO_DEVICE_FILE="$RADIO_STATE_DIR/device.json"' in ENTRYPOINT
    assert 'ln -s "$RADIO_DEVICE_FILE" /root/.netease_mcp_device.json' in ENTRYPOINT
