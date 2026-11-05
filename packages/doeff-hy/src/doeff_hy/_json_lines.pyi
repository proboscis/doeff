# Type declarations produced by doeff_hy._static_stub — do not edit by hand (source = _json_lines.hy; regenerate = python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from doeff import Program as _Program
LINE_END: str

def jsonl_lines(text: str) -> _Program[tuple, object]:
    ...

def whole_lines_end(data: bytes) -> _Program[int, object]:
    ...
