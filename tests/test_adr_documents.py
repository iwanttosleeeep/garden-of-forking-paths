from pathlib import Path

from ombrebrain.architecture import ADRDocument, ADRRequirementsContract


def test_repository_adr_documents_satisfy_the_contract():
    root = Path(__file__).resolve().parent.parent
    documents = [
        ADRDocument(
            path=str(path.relative_to(root)).replace("\\", "/"),
            text=path.read_text(encoding="utf-8"),
        )
        for path in sorted((root / "docs" / "adr").glob("ADR-*.md"))
    ]

    report = ADRRequirementsContract.default().evaluate_documents(documents)

    assert documents
    assert report.ok, report.to_dict()


def test_docker_image_includes_adr_documents_for_diagnostics():
    root = Path(__file__).resolve().parent.parent
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (root / ".dockerignore").read_text(encoding="utf-8")

    assert "COPY docs/adr/ ./docs/adr/" in dockerfile
    assert "!docs/adr/**" in dockerignore
