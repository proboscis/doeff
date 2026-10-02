"""封の file として規則の母集団から外す名指しが、本当に封の表に控えられた file だけであること(agora-redesign #2934)。

docs の下の architecture.hy は、層 ``sealed`` の ``:files`` で、hash で封をした設計の記録の file を 1 つずつ名指して
doeff-linter の規則から外す(中身を封の表が控えるので、規則に合わせて書き換えると封が壊れる)。この検は、名指した file が
どれも、その記録の封の表(``SHA256SUMS*.txt`` か ``design-check.json`` の ``path`` と ``sha256``)に控えられ、hash が中身と
一致することを確かめる — 封の無い file を「封」の理由で外す形・封の表を直さずに中身を変えた形を赤にする。

記録の dir = ``docs/design/<記録>`` か ``docs/design-checks/<記録>``。封の表の path は、その記録の dir からの相対 path。
"""

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import hy
import hy.models

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs"
RECORD_KINDS = ("design", "design-checks")


def _keyword_value(items: list[object], name: str) -> object | None:
    for index, item in enumerate(items[:-1]):
        if isinstance(item, hy.models.Keyword) and item.name == name:
            return items[index + 1]
    return None


def sealed_files_of(declaration: Path) -> list[Path]:
    """宣言の層 ``sealed`` の ``:files`` が名指す file(宣言の在る dir から解いた path)。"""
    named: list[Path] = []
    for form in hy.read_many(declaration.read_text(encoding="utf-8")):
        if not isinstance(form, hy.models.Expression) or str(form[0]) != "defarchitecture":
            continue
        layers = _keyword_value(list(form), "layers")
        if not isinstance(layers, hy.models.List):
            continue
        for layer in layers:
            if not isinstance(layer, hy.models.Expression) or str(layer[0]) != "layer" or str(layer[1]) != "sealed":
                continue
            files = _keyword_value(list(layer), "files")
            if isinstance(files, hy.models.List):
                named.extend(declaration.parent / str(item) for item in files)
    return named


def record_dir_of(path: Path) -> Path:
    """file が属する記録の dir(``docs/design/<記録>`` か ``docs/design-checks/<記録>``)。"""
    relative = path.relative_to(DOCS)
    assert relative.parts[0] in RECORD_KINDS, f"{path} は封の記録の dir の下に無い"
    return DOCS / relative.parts[0] / relative.parts[1]


def _design_check_entries(node: object) -> Iterator[tuple[str, str]]:
    if isinstance(node, dict):
        path, digest = node.get("path"), node.get("sha256")
        if isinstance(path, str) and isinstance(digest, str):
            yield path, digest
        for value in node.values():
            yield from _design_check_entries(value)
    elif isinstance(node, list):
        for value in node:
            yield from _design_check_entries(value)


def seal_entries(record: Path) -> dict[str, str]:
    """記録の封の表が控える (記録の dir からの相対 path → sha256)。"""
    entries: dict[str, str] = {}
    for table in sorted(record.rglob("SHA256SUMS*.txt")):
        for line in table.read_text(encoding="utf-8").splitlines():
            digest, _, path = line.partition("  ")
            if path:
                entries[path.strip()] = digest.strip()
    design_check = record / "design-check.json"
    if design_check.is_file():
        entries.update(_design_check_entries(json.loads(design_check.read_text(encoding="utf-8"))))
    return entries


def all_sealed_files() -> list[Path]:
    return [
        file
        for declaration in sorted(DOCS.rglob("architecture.hy"))
        for file in sealed_files_of(declaration)
    ]


def test_the_declarations_name_sealed_files() -> None:
    # 名指しが 1 つも読めない形(層の名・鍵の綴りを変えた)で、下の検が空のまま緑にならないこと
    assert len(all_sealed_files()) >= 4


def test_every_named_sealed_file_is_in_its_records_seal_table_with_the_same_hash() -> None:
    for file in all_sealed_files():
        record = record_dir_of(file)
        entries = seal_entries(record)
        key = file.relative_to(record).as_posix()
        assert key in entries, f"{file} を封の表が控えていない(封の無い file は sealed で外さない)"
        actual = hashlib.sha256(file.read_bytes()).hexdigest()
        assert actual == entries[key], f"{file} の中身が封の表の hash と違う(封の表を直さずに中身を変えた)"
