"""設計が依っている本体の契約を、版で変わる名に依らない「形」で照らす実験(計画段の証拠)。

雛形 `model/check_body_contract.py` は 2.1.263 の逐語で pin していて、版が変わると
契約が生きていても赤になる。ここでは次の規則で pin を組み直し、複数の版で照らす。

- 縮める道具が名を付け替えるのは**束縛の名**(関数名・局所変数・引数)だけ。そこは
  `[\\w$]+` で受ける。JS の識別子は `$` を含むので `\\w+` では足りない
  (実測: 関数名の約 2% が `$` を含む)。
- 文字列の literal・`.` の後ろの property / method の名(`lstat` / `isSymbolicLink` /
  `isFile` / `isDirectory` / `nlink` / `entrypoint`)・構文は付け替えられないので綴りで固定する。
- 同じ束縛が何度も出る所は名前付きの group で「同じ名であること」を pin する。
- 契約を担う断片だけを pin する。関数の頭(引数の数)は版で動く
  (2.1.278 は除外の関数が引数 3 つに組み替わった)。

使い方: python3 shape_pins_across_versions.py <本体> [<本体> ...]
"""
from __future__ import annotations

import re
import sys

IDENT = rb"[\w$]+"


def shape(pattern: bytes) -> re.Pattern[bytes]:
    return re.compile(pattern.replace(b"@", IDENT))


SHAPES: dict[str, re.Pattern[bytes]] = {
    "user-memory-lives-under-the-config-dir":
        shape(rb'case"User":return @\(@\(\),"CLAUDE\.md"\)'),
    "user-layer-drops-symlink-and-hardlink":
        shape(rb'if\((?P<t>@)==="User"&&!(?P<v>@)\)try\{let (?P<s>@)=await @\(\)\.lstat\(@\);'
              rb'if\(@===0&&(?P=s)\.isSymbolicLink\(\)\|\|\((?P=s)\.nlink\?\?1\)>1&&(?P=s)\.isFile\(\)\)'
              rb'return\[\]\}catch\{\}'),
    "the-gate-fires-only-for-local-agent":
        shape(rb'function (?P<g>@)\(\)\{return (?P<e>@)\(\)!=="local-agent"\}'),
    "user-skills-live-under-the-config-dir":
        shape(rb'let @=@\(@\(\),"skills"\),@=@\(@\(\),"\.claude","skills"\)'),
    "skills-entries-accept-a-symlink":
        shape(rb"if\(!(?P<n>@)\.isDirectory\(\)&&!(?P=n)\.isSymbolicLink\(\)\)return null;"),
    "md-excludes-applies-to-the-user-layer":
        shape(rb'if\((?P<t>@)!=="User"&&(?P=t)!=="Project"&&(?P=t)!=="Local"\)return!1;'),
}

# 雛形の逐語(2.1.263)— 依頼者が pod の 2.1.263 で 2026-09-21T07:01Z に green を実測した綴り。
# 形がこの逐語を受けることで、2.1.263 の本体を受けることの代わりにする。
VERBATIM_2_1_263: dict[str, bytes] = {
    "user-memory-lives-under-the-config-dir": b'case"User":return Ke(Se(),"CLAUDE.md")',
    "user-layer-drops-symlink-and-hardlink":
        b'if(t==="User"&&!v)try{let q=await ae().lstat(e);'
        b'if(d===0&&q.isSymbolicLink()||(q.nlink??1)>1&&q.isFile())return[]}catch{}',
    "the-gate-fires-only-for-local-agent": b'function wgr(){return CN()!=="local-agent"}',
    "user-skills-live-under-the-config-dir": b'let r=Ah(Se(),"skills"),o=Ah(HS(),".claude","skills")',
    "skills-entries-accept-a-symlink": b"if(!N.isDirectory()&&!N.isSymbolicLink())return null;",
    "md-excludes-applies-to-the-user-layer":
        b'function Sgr(e,t){if(t!=="User"&&t!=="Project"&&t!=="Local")return!1;',
}

# 弁別: 形の中の契約を担う語を 1 つ動かすと赤になるか
MUTATIONS: dict[str, tuple[bytes, bytes]] = {
    "user-memory-lives-under-the-config-dir": (b'"CLAUDE.md"', b'"CLAUDE.mx"'),
    "user-layer-drops-symlink-and-hardlink": (b"nlink??1)>1", b"nlink??1)>2"),
    "the-gate-fires-only-for-local-agent": (b'"local-agent"', b'"local-agenx"'),
    "user-skills-live-under-the-config-dir": (b'"skills"', b'"skillz"'),
    "skills-entries-accept-a-symlink": (b".isSymbolicLink()", b".isSymbolicLinx()"),
    "md-excludes-applies-to-the-user-layer": (b'"Local"', b'"Locax"'),
}


def check_verbatim_proxy() -> None:
    print("===== 雛形の逐語(2.1.263)を形が受けるか")
    for name, rx in SHAPES.items():
        print(f"  {'OK ' if rx.search(VERBATIM_2_1_263[name]) else 'MISS'} {name}")


def check_body(path: str) -> None:
    with open(path, "rb") as fh:
        body = fh.read()
    print(f"===== {path}  同定(CLAUDE_CONFIG_DIR)={'yes' if b'CLAUDE_CONFIG_DIR' in body else 'NO'}")
    hits = {name: list(rx.finditer(body)) for name, rx in SHAPES.items()}
    for name, ms in hits.items():
        first = ms[0].group(0).decode("latin1") if ms else "-"
        print(f"  {'OK ' if ms else 'MISS'} {name}: 一致 {len(ms)} 件  {first}")
    drop, gate = hits["user-layer-drops-symlink-and-hardlink"], hits["the-gate-fires-only-for-local-agent"]
    if drop and gate:
        m, g = drop[0], gate[0]
        window = body[max(0, m.start() - 4000):m.start()]
        link = list(re.finditer(
            re.escape(m.group("v")) + rb"=" + IDENT + rb"&&\(" + re.escape(m.group("t"))
            + rb'!=="User"\|\|' + re.escape(g.group("g")) + rb"\(\)\)", window))
        print(f"  {'OK ' if link else 'MISS'} drop-branch-is-controlled-by-the-gate: "
              + (link[-1].group(0).decode() if link else "-"))
        reader = re.search(rb"function " + re.escape(g.group("e")) + rb"\(\)\{return " + IDENT
                           + rb"\(\)\.entrypoint\}", body)
        print(f"  {'OK ' if reader else 'MISS'} gate-reader-reads-entrypoint: "
              + (reader.group(0).decode() if reader else "-"))
    for name, rx in SHAPES.items():
        if not hits[name]:
            continue
        before, after = MUTATIONS[name]
        mutated = hits[name][0].group(0).replace(before, after, 1)
        print(f"  {'red-OK' if not rx.search(mutated) else 'STILL-GREEN'} 弁別 {name}: "
              f"{before.decode()} -> {after.decode()}")
    with_dollar = len(re.findall(rb"function [\w$]*\$[\w$]*\(", body))
    total = len(re.findall(rb"function [\w$]+\(", body))
    print(f"  '$' を含む関数名: {with_dollar} / {total}")


if __name__ == "__main__":
    check_verbatim_proxy()
    for target in sys.argv[1:]:
        check_body(target)
