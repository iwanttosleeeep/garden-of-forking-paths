import pytest

from web import _shared as sh
from web import meta


@pytest.mark.asyncio
async def test_settings_memo_stats_excludes_sterling_journals(monkeypatch):
    class Manager:
        async def list_all(self, include_archive=False):
            return [
                {"metadata": {"type": "permanent"}},
                {"metadata": {"type": "permanent"}},
                {"metadata": {"type": "dynamic"}},
                {"metadata": {"type": "journal", "source_tool": "sterling"}},
                {"metadata": {"type": "dynamic", "tags": ["__journal__", "source:sterling"]}},
                {"metadata": {"type": "archived"}},
            ]

    monkeypatch.setattr(sh, "bucket_mgr", Manager())

    assert await meta._memo_stats() == {"permanent": 2, "dynamic": 1, "archive": 1, "total": 3}
