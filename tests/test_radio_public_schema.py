import ast
from pathlib import Path


SERVER = Path(__file__).resolve().parents[1] / "src" / "server.py"


def test_radio_public_schema_uses_backend_song_id_list_name_and_array_type() -> None:
    tree = ast.parse(SERVER.read_text(encoding="utf-8"))
    radio = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "radio"
    )
    arguments = {argument.arg: argument.annotation for argument in radio.args.args}

    assert "songIdList" in arguments
    assert "song_ids" not in arguments
    assert ast.unparse(arguments["songIdList"]) == "Optional[list[str]]"
