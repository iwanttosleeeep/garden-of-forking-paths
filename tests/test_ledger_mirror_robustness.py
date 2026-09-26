import json
from concurrent.futures import ThreadPoolExecutor

from ledger_mirror import LedgerMirror


def _append(ledger, number=0):
    return ledger.append_event(
        event_type="TraceTouched",
        trace_id=f"memory-{number}",
        trace_kind="dynamic",
        body="synthetic test memory",
    )


def test_repeated_appends_scan_history_only_once(tmp_path, monkeypatch):
    ledger = LedgerMirror(tmp_path / "events.jsonl")
    original = ledger.iter_events
    scans = 0

    def counted_events():
        nonlocal scans
        scans += 1
        yield from original()

    monkeypatch.setattr(ledger, "iter_events", counted_events)
    for number in range(100):
        assert _append(ledger, number)["seq"] == number + 1

    assert ledger.latest_seq() == 100
    assert scans == 1
    assert len(list(original())) == 100


def test_sequence_cache_observes_other_instances_and_restart(tmp_path):
    path = tmp_path / "events.jsonl"
    first = LedgerMirror(path)
    second = LedgerMirror(path)
    for expected in range(1, 11):
        writer = first if expected % 2 else second
        assert _append(writer)["seq"] == expected
    assert _append(LedgerMirror(path))["seq"] == 11
    assert first.latest_seq() == second.latest_seq() == 11


def test_sequence_cache_observes_external_truncation_replacement_and_removal(tmp_path):
    path = tmp_path / "events.jsonl"
    ledger = LedgerMirror(path)
    _append(ledger)
    _append(ledger)

    path.write_text("", encoding="utf-8")
    assert _append(ledger)["seq"] == 1

    replacement = tmp_path / "replacement.jsonl"
    replacement.write_text(json.dumps({"seq": 50}) + "\n", encoding="utf-8")
    replacement.replace(path)
    assert _append(ledger)["seq"] == 51

    path.unlink()
    assert _append(ledger)["seq"] == 1


def test_non_object_json_does_not_poison_future_writes(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('null\n[]\n"unexpected"\n42\n', encoding="utf-8")
    ledger = LedgerMirror(path)

    assert list(ledger.iter_events()) == []
    assert ledger.latest_seq() == 0
    assert _append(ledger)["seq"] == 1
    report = ledger.verify_integrity()
    assert report["ok"] is False
    assert report["invalid_lines"] == [1, 2, 3, 4]
    assert report["valid_events"] == 1
    assert report["latest_seq"] == 1


def test_invalid_numeric_sequence_does_not_break_append(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"seq": Infinity, "schema_version": 1}\n', encoding="utf-8")
    ledger = LedgerMirror(path)

    assert _append(ledger)["seq"] == 1
    assert ledger.verify_integrity()["invalid_lines"] == [1]


def test_external_partial_line_is_preserved_and_separated(tmp_path):
    path = tmp_path / "events.jsonl"
    ledger = LedgerMirror(path)
    _append(ledger)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 2')

    assert _append(ledger)["seq"] == 2
    assert [event["seq"] for event in ledger.iter_events()] == [1, 2]
    assert ledger.verify_integrity()["invalid_lines"] == [2]


def test_threaded_instances_allocate_unique_sequences(tmp_path):
    path = tmp_path / "events.jsonl"
    ledgers = [LedgerMirror(path) for _ in range(4)]

    def append_number(number):
        return _append(ledgers[number % len(ledgers)], number)["seq"]

    with ThreadPoolExecutor(max_workers=8) as executor:
        sequences = list(executor.map(append_number, range(100)))

    assert sorted(sequences) == list(range(1, 101))
    assert [event["seq"] for event in ledgers[0].iter_events()] == list(range(1, 101))
    assert ledgers[0].verify_integrity()["ok"] is True
