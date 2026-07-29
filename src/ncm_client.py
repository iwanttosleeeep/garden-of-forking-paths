"""Bounded bridge to NetEase's official ``@music163/ncm-cli`` package.

Only the music-reading and playlist-management commands used by Garden are
exposed here.  No shell is involved and callers cannot provide arbitrary CLI
arguments.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Awaitable, Callable


NCM_CLI_VERSION = "0.1.6"
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_TEXT_LENGTH = 300
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class NCMClientError(ValueError):
    """A safe, user-facing NetEase Music bridge error."""


Runner = Callable[[list[str], float], Awaitable[Any]]


def _bounded_text(value: object, label: str, *, limit: int = MAX_TEXT_LENGTH) -> str:
    text = str(value or "").strip()
    if not text:
        raise NCMClientError(f"请填写{label}")
    if len(text) > limit:
        raise NCMClientError(f"{label}过长")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in text):
        raise NCMClientError(f"{label}包含无效字符")
    return text


def _json_from_text(value: str) -> Any:
    clean = _ANSI_RE.sub("", value or "").strip()
    if not clean:
        return {}
    try:
        return json.loads(clean)
    except ValueError:
        pass
    # Some CLI versions print log lines before a pretty-printed JSON object.
    # Recover that object without depending on a particular log prefix.
    decoder = json.JSONDecoder()
    decoded: list[Any] = []
    for match in re.finditer(r"[\[{]", clean):
        try:
            payload, _end = decoder.raw_decode(clean[match.start() :])
        except ValueError:
            continue
        decoded.append(payload)
    if decoded:
        return max(decoded, key=lambda item: len(json.dumps(item, ensure_ascii=False)))
    # Older versions occasionally emit compact JSON on the final line.
    for line in reversed(clean.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except ValueError:
            continue
    return {"text": clean}


def _is_logged_in(payload: Any) -> bool:
    """Recognise login responses emitted across official CLI/API versions."""
    if isinstance(payload, list):
        return any(_is_logged_in(item) for item in payload)
    if not isinstance(payload, dict):
        text = str(payload or "")
        return ("已登录" in text or "登录成功" in text) and "未登录" not in text

    for key in ("loggedIn", "logged_in", "isLoggedIn", "isLogin", "loginSuccess"):
        if payload.get(key) is True:
            return True
    if payload.get("success") is True:
        return True

    message = str(payload.get("message") or payload.get("msg") or payload.get("text") or "")
    if ("已登录" in message or "登录成功" in message) and "未登录" not in message:
        return True

    for key in ("data", "result", "loginStatus"):
        if _is_logged_in(payload.get(key)):
            return True
    for key in ("account", "profile", "user"):
        value = payload.get(key)
        if isinstance(value, dict) and value:
            return True
    return False


def _api_error(payload: Any) -> str:
    """Return an official API error even when the CLI exits with status zero."""
    if not isinstance(payload, dict):
        return ""
    try:
        code = int(payload.get("code"))
    except (TypeError, ValueError):
        code = 0
    if code >= 400 or payload.get("success") is False:
        return str(payload.get("message") or payload.get("msg") or "网易云音乐操作失败")[:500]
    return ""


def _song_id_values(value: object) -> list[int]:
    """Normalize Garden input to the numeric ID array required by the CLI."""
    raw_items: object = value
    if isinstance(value, bool):
        raise NCMClientError("songIdList 只能包含歌曲 ID")
    if isinstance(value, int):
        raw_items = [value]
    elif isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            try:
                raw_items = json.loads(text)
            except ValueError as exc:
                raise NCMClientError("songIdList 必须是歌曲 ID 数组") from exc
        else:
            raw_items = text.split(",")
    if not isinstance(raw_items, (list, tuple, set)):
        raise NCMClientError("songIdList 必须是歌曲 ID 数组")

    songs: list[int] = []
    seen: set[int] = set()
    for item in raw_items:
        if isinstance(item, bool):
            raise NCMClientError("songIdList 只能包含数字歌曲 ID")
        song_text = str(item or "").strip()
        if not song_text:
            continue
        if not song_text.isascii() or not song_text.isdecimal():
            raise NCMClientError("songIdList 只能包含数字歌曲 ID")
        song = int(song_text)
        if song <= 0:
            raise NCMClientError("songIdList 只能包含数字歌曲 ID")
        if song not in seen:
            songs.append(song)
            seen.add(song)
    if not songs:
        raise NCMClientError("请填写songIdList")
    serialized = json.dumps(songs, ensure_ascii=False, separators=(",", ":"))
    if len(serialized) > 2000:
        raise NCMClientError("songIdList过长")
    return songs


def _song_list_shape_error(exc: NCMClientError) -> bool:
    message = str(exc).lower()
    return any(
        token in message
        for token in ("参数", "parameter", "songidlist", "array", "数组", "json")
    )


async def run_cli(args: list[str], timeout: float = 40.0) -> Any:
    """Run one allowlisted CLI invocation without a shell."""
    executable = os.environ.get("OMBRE_NCM_CLI", "ncm-cli").strip() or "ncm-cli"
    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            "--output",
            "json",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise NCMClientError("Radio 组件尚未安装；请重新构建 Garden 镜像") from exc
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise NCMClientError("网易云音乐响应超时，请稍后再试") from exc
    if len(stdout) + len(stderr) > MAX_OUTPUT_BYTES:
        raise NCMClientError("网易云音乐返回的数据过大，请缩小范围")
    output = stdout.decode("utf-8", errors="replace").strip()
    errors = stderr.decode("utf-8", errors="replace").strip()
    payload = _json_from_text(output or errors)
    if process.returncode != 0:
        message = payload.get("message") if isinstance(payload, dict) else ""
        raise NCMClientError(str(message or errors or output or "网易云音乐命令执行失败")[:500])
    api_error = _api_error(payload)
    if api_error:
        raise NCMClientError(api_error)
    return payload


class NCMClient:
    """Semantic, allowlisted facade used by both Radio UI and MCP tool."""

    def __init__(self, runner: Runner | None = None):
        self._runner = runner or run_cli

    async def _call(self, args: list[str], timeout: float = 40.0) -> Any:
        return await self._runner(args, timeout)

    async def configure(self, app_id: object, private_key: object) -> dict[str, Any]:
        app = _bounded_text(app_id, "App ID", limit=128)
        secret = _bounded_text(private_key, "Private Key", limit=8192)
        await self._call(["config", "set", "appId", app], 15.0)
        await self._call(["config", "set", "privateKey", secret], 15.0)
        return {"configured": True, "app_id_masked": f"{app[:3]}…{app[-3:]}" if len(app) > 7 else "***"}

    async def status(self, *, configured: bool = False) -> dict[str, Any]:
        try:
            version = await self._call(["--version"], 8.0)
        except NCMClientError:
            version = {"text": NCM_CLI_VERSION}
        logged_in = False
        login_message = "尚未登录"
        try:
            checked = await self._call(["login", "--check"], 15.0)
            logged_in = _is_logged_in(checked)
            if isinstance(checked, dict):
                login_message = str(checked.get("message") or ("已登录" if logged_in else login_message))
        except NCMClientError as exc:
            login_message = str(exc)
        return {
            "configured": bool(configured),
            "logged_in": logged_in,
            "login_message": login_message,
            "cli_version": NCM_CLI_VERSION,
            "cli": version,
        }

    async def start_login(self) -> Any:
        return await self._call(["login", "--background"], 25.0)

    @staticmethod
    def _intent(text: str) -> list[str]:
        return ["--userInput", text[:MAX_TEXT_LENGTH]]

    async def playlists(self, scope: str = "created") -> Any:
        if str(scope or "created").strip().lower() != "created":
            raise NCMClientError("Radio 只读取我创建的歌单")
        return await self._call(
            ["playlist", "created", *self._intent("读取我创建的歌单")]
        )

    async def playlist_tracks(self, playlist_id: object, *, alternate_id: object = "") -> Any:
        primary = _bounded_text(playlist_id or alternate_id, "playlist_id", limit=128)
        alternate = str(alternate_id or "").strip()
        candidates = [primary]
        if alternate and alternate != primary:
            candidates.append(_bounded_text(alternate, "encrypted_id", limit=128))
        last_error: NCMClientError | None = None
        for index, value in enumerate(candidates):
            try:
                return await self._call(
                    [
                        "playlist",
                        "tracks",
                        "--playlistId",
                        value,
                        *self._intent("读取指定歌单曲目"),
                    ]
                )
            except NCMClientError as exc:
                last_error = exc
                message = str(exc).lower()
                identifier_error = any(
                    token in message for token in ("参数", "parameter", "identifier", "playlist id")
                )
                if index == 0 and len(candidates) > 1 and identifier_error:
                    continue
                raise
        raise last_error or NCMClientError("读取歌单曲目失败")

    async def search(self, query: object, kind: str = "all") -> Any:
        keyword = _bounded_text(query, "搜索关键词")
        wanted = str(kind or "all").strip().lower()
        if wanted not in {"all", "song", "album", "playlist"}:
            raise NCMClientError("kind 必须是 all / song / album / playlist")
        return await self._call(
            ["search", wanted, "--keyword", keyword, *self._intent(f"搜索音乐：{keyword}")]
        )

    async def create_playlist(self, name: object) -> Any:
        title = _bounded_text(name, "歌单名称", limit=80)
        return await self._call(
            ["playlist", "create", "--playlistName", title, *self._intent(f"创建歌单：{title}")]
        )

    async def add_tracks(self, playlist_id: object, song_ids: object) -> Any:
        """Add tracks while translating Garden's ``song_ids`` to the CLI contract."""
        playlist = _bounded_text(playlist_id, "playlist_id", limit=128)
        songs = _song_id_values(song_ids)
        encoded_values = (
            json.dumps(songs, ensure_ascii=False, separators=(",", ":")),
            ",".join(str(song) for song in songs),
        )
        for index, encoded in enumerate(encoded_values):
            try:
                return await self._call(
                    [
                        "playlist",
                        "add",
                        "--playlistId",
                        playlist,
                        "--songIdList",
                        encoded,
                        *self._intent("把指定歌曲加入歌单"),
                    ]
                )
            except NCMClientError as exc:
                # A validation error means the write was rejected before it
                # could mutate the playlist. Retry only that safe failure with
                # the older comma-separated representation.
                if index == 0 and _song_list_shape_error(exc):
                    continue
                raise
        raise NCMClientError("添加歌曲失败")
