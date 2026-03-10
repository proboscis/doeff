from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_repo_no_longer_contains_doeff_pinjected_package() -> None:
    assert not (ROOT / "packages" / "doeff-pinjected").exists()


def test_workspace_metadata_no_longer_declares_doeff_pinjected() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "doeff-pinjected" not in pyproject


def test_public_docs_do_not_advertise_doeff_pinjected() -> None:
    public_docs = [
        ROOT / "README.md",
        ROOT / "docs" / "01-getting-started.md",
        ROOT / "docs" / "index.md",
        ROOT / "docs" / "12-patterns.md",
        ROOT / "docs" / "13-api-reference.md",
    ]
    for path in public_docs:
        text = path.read_text(encoding="utf-8")
        assert "doeff-pinjected" not in text
        assert "doeff_pinjected" not in text


def test_pinjected_integration_doc_is_removed() -> None:
    assert not (ROOT / "docs" / "10-pinjected-integration.md").exists()
