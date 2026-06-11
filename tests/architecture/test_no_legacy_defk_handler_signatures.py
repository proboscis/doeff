import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_HY_SOURCES = REPO_ROOT / "packages"
DEFK_SIGNATURE_RE = re.compile(r"^\s*\(defk\s+([^\s\[]+)\s+\[([^\]]*)\]", re.MULTILINE)
HANDLER_LIKE_PARAMS = {"effect", "eff", "k"}


def _legacy_defk_handler_signatures() -> list[str]:
    violations: list[str] = []
    for hy_file in sorted(PRODUCTION_HY_SOURCES.glob("*/src/**/*.hy")):
        text = hy_file.read_text(encoding="utf-8")
        for match in DEFK_SIGNATURE_RE.finditer(text):
            params = set(match.group(2).split())
            handler_params = sorted(params & HANDLER_LIKE_PARAMS)
            if not handler_params:
                continue
            line_number = text.count("\n", 0, match.start()) + 1
            relative_path = hy_file.relative_to(REPO_ROOT)
            violations.append(
                f"{relative_path}:{line_number} {match.group(1)} "
                f"uses handler-like params {handler_params}"
            )
    return violations


def test_package_production_hy_handlers_do_not_use_defk_signatures() -> None:
    """Production Hy handlers must use defhandler instead of legacy defk(effect, k)."""
    assert _legacy_defk_handler_signatures() == []
