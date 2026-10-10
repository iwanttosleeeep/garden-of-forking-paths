"""Keep explicitly shipped documentation available to the Docker builder."""

from pathlib import Path
import shlex


def test_copied_documentation_is_present_and_allowlisted():
    root = Path(__file__).resolve().parents[1]
    rules = (root / ".dockerignore").read_text().splitlines()
    markdown_exclusion = rules.index("*.md")
    for line in (root / "Dockerfile").read_text().splitlines():
        if not line.startswith("COPY "):
            continue
        for source in shlex.split(line)[1:-1]:
            if source.startswith("docs/") and source.endswith(".md"):
                assert (root / source).is_file(), source
                assert f"!{source}" in rules[markdown_exclusion + 1:], source
