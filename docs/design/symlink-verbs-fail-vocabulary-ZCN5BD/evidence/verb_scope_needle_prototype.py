"""門を「動詞の単位」へ動かせるかの試作。

semgrep は `substrate.hy` を **file 単位**で exclude しているので、file の中に 3 つ目の
hand-roll が生えても緑のまま。Hy 自身の reader で読めば、`os.symlink` の出現が
「許した 2 つの動詞の本体の中」に在るかを**正確に**(綴りの見当ではなく構文木で)言える。
"""

import hy  # noqa: F401
from hy import models
from hy.reader import read_many

SOURCE = "packages/doeff-agents/src/doeff_agents/sessionhost/substrate.hy"
# 許す 2 点(物理が逆なので 1 つへは畳めない — 実装 c-EWR4R2XY… の註)
ALLOWED = (
    ("deff", "ensure-symlink-outcome"),   # 置き換える据え付け: 一意な仮 + rename
    ("FsLinkArtifact", None),             # 置き換えない敷設: symlink を撃って EEXIST を読み直す
)
# ⚠ Hy の reader は `os.symlink` を `(. os symlink)` へ割る(実測 = この file の下の出力)。
# 綴りの grep ではなく**構文木の形**で見分ける: (. os symlink) と (.symlink_to …)。
BANNED_ATTR = ("os", "symlink")
BANNED_METHOD = ".symlink_to"


def walk(form, depth=0):
    yield depth, form
    if isinstance(form, (models.Sequence,)):
        for child in form:
            yield from walk(child, depth + 1)


def head_of(form):
    if isinstance(form, models.Sequence) and len(form):
        first = form[0]
        if isinstance(first, models.Symbol):
            return str(first)
    return None


def name_of(form):
    if isinstance(form, models.Sequence) and len(form) > 1:
        second = form[1]
        if isinstance(second, models.Symbol):
            return str(second)
    return None


def main():
    source = open(SOURCE, encoding="utf-8").read()
    forms = list(read_many(source, filename=SOURCE))

    allowed_ranges = []
    for _, form in ((d, f) for top in forms for d, f in walk(top)):
        head, name = head_of(form), name_of(form)
        for want_head, want_name in ALLOWED:
            if head == want_head and (want_name is None or name == want_name):
                allowed_ranges.append((want_head, want_name, form.start_line, form.end_line))

    print("-- 許した動詞の本体の範囲(構文木から) --")
    for head, name, start, end in allowed_ranges:
        print(f"  ({head} {name or ''}) 行 {start}-{end}")

    hits = []
    for _, form in ((d, f) for top in forms for d, f in walk(top)):
        if not isinstance(form, models.Expression) or not len(form):
            continue
        head = form[0]
        if not isinstance(head, models.Symbol):
            continue
        if str(head) == BANNED_METHOD:
            hits.append((BANNED_METHOD, form.start_line))
        elif str(head) == "." and [str(x) for x in form[1:]] == list(BANNED_ATTR):
            hits.append(("os.symlink", form.start_line))

    print("-- 禁じた綴りの出現 --")
    for spelling, line in hits:
        inside = [n or h for h, n, s, e in allowed_ranges if s <= line <= e]
        verdict = f"許した動詞 {inside[0]} の中" if inside else "★ 動詞の外(門が赤にすべき)"
        print(f"  行 {line:4d}  {spelling:12s} → {verdict}")

    outside = [(s, l) for s, l in hits if not any(a <= l <= b for _, _, a, b in allowed_ranges)]
    print(f"-- 判定: 動詞の外の出現 = {len(outside)} 件(0 なら緑)--")


if __name__ == "__main__":
    main()
