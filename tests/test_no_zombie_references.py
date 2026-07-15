"""僵尸引用哨兵 — 已退休模块不得再被 src/ 引用。

背景:第七刀删除 web/tunnel.py 时,server.py 里的 import 因匹配失误残留,
而 compileall / ruff / pytest 均不覆盖「入口脚本 import 已删模块」这类
故障,容器上线才崩(2026-07-14)。本哨兵按死亡名单全仓扫描 + 校验
server.py 的本地 import 都真实存在;任何一刀漏扫残留,CI 直接红。
"""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"

ZOMBIES = [
    "web.tunnel",
    "import_memory",
    "migrate_engine",
    "_load_tunnel_config",
    "_start_tunnel",
    "_stop_tunnel",
    "VNextPreflightReportBuilder",
    "cloudflared",
    "ImportEngine",
    "MigrateEngine",
]


def test_src_has_no_zombie_references():
    offenders = []
    for py in SRC.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="replace")
        for z in ZOMBIES:
            if z in text:
                offenders.append(f"{py.relative_to(SRC)}: {z}")
    assert not offenders, "已退休模块死而复生:\n" + "\n".join(offenders)


def _local_module_exists(mod: str) -> bool:
    return (SRC / (mod.replace(".", "/") + ".py")).exists() or (
        SRC / mod.replace(".", "/") / "__init__.py"
    ).exists()


def test_entrypoint_local_imports_resolve():
    """server.py 顶层 import 的本地模块必须真实存在于 src/。"""
    tree = ast.parse((SRC / "server.py").read_text(encoding="utf-8", errors="replace"))
    missing = []
    for node in ast.walk(tree):
        mods = []
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            mods = [node.module]
        elif isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        for mod in mods:
            root = mod.split(".")[0]
            if not _local_module_exists(root) and not (SRC / root).is_dir():
                continue  # 第三方库,交给运行时
            if not _local_module_exists(mod):
                missing.append(mod)
    assert not missing, "server.py import 了不存在的本地模块: " + ", ".join(missing)
