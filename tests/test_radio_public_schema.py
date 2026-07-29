import ast
from pathlib import Path


SERVER = Path(__file__).resolve().parents[1] / "src" / "server.py"


def test_radio_public_schema_accepts_backend_and_cached_connector_names() -> None:
    tree = ast.parse(SERVER.read_text(encoding="utf-8"))
    radio = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "radio"
    )
    arguments = {argument.arg: argument.annotation for argument in radio.args.args}

    assert "songIdList" in arguments
    assert "song_ids" in arguments
    assert ast.unparse(arguments["songIdList"]) == "Optional[list[str | int]]"
    assert ast.unparse(arguments["song_ids"]) == "Optional[str | int | list[str | int]]"


def test_radio_maps_cached_song_ids_before_dispatch() -> None:
    source = SERVER.read_text(encoding="utf-8")

    assert "resolved_song_ids = _t_radio.resolve_song_id_list(" in source
    assert "songIdList=songIdList" in source
    assert "song_ids=song_ids" in source
    assert "songIdList=resolved_song_ids" in source
