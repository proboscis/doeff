# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = _json_lines.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
LINE_END: str

def jsonl_lines(text: str) -> _Program[tuple, object]:
    ...

def whole_lines_end(data: bytes) -> _Program[int, object]:
    ...
