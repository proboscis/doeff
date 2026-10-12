"""設計検証の報告 JSON(version 1)を作る。参照 file の sha256 は実 file から計算する。

使い方: uv run --no-project python evidence/scripts/build_report.py   (docs/design-checks/lt-2SJX…/ の下で)
"""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TASK = "lt-2SJXY98Q643WGJK2ASWJ3N8PWH"
BASE = "dc3385b9a4bb105937d9dad6671545f7b34c80f2"


def ref(path: str) -> dict[str, str]:
    return {"path": path, "sha256": hashlib.sha256((ROOT / path).read_bytes()).hexdigest()}


def module(mid: str, responsibility: str, owns: str, hides: str, interface: str, effects: str, lifetime: str, invariants: str) -> dict[str, str]:
    return {
        "id": mid,
        "responsibility": responsibility,
        "owns": owns,
        "hides": hides,
        "interface": interface,
        "effects": effects,
        "lifetime": lifetime,
        "invariants": invariants,
    }


MODULES = [
    module(
        "expansion-names",
        "handle・defhandler の展開が読む doeff の名の `_doeff_` の別名の綴りと import の唯一の定義元(packages/doeff-hy/src/doeff_hy/expansion_names.py)",
        "別名の頭 `_doeff_`・名ごとの置き場(WithHandler=doeff_vm、Get・Put=doeff_core_effects.effects、他は doeff)",
        "Hy の import の model の組み方(位置を持たない model を作る事)・置き場ごとのまとめ方",
        "alias(name)・alias_symbol(name)・home(name)・import_form(names)・import_source(names)",
        "なし(純粋な関数)",
        "なし(資源を持たない)",
        "alias_symbol と import_form は呼ぶたびに位置を持たない新しい model を返す。表の名は置き場から import できる",
    ),
    module(
        "clause-ops-table",
        "節の操作(resume・transfer・finish・reperform・pass・raise)の表 CLAUSE-OPS と各行の書き換え(clause_ops.hy)",
        "操作の名・終わり方・引数の数・節へ戻るか・末尾で替える先・必要な node の名とその読み方",
        "handle.hy に操作の名を写さない",
        "clause-op・named-op・node-names・ops-ending・linter-table-json",
        "なし(展開の時の純粋な判断)",
        "なし",
        "行の rewrite が node を読む綴りは alias-symbol で引く。操作の名は節の本体の頭の名として予約される",
    ),
    module(
        "handler-expansion",
        "handle・defhandler の展開(dispatcher・prelude の import・WithHandler の被せ・session の lazy 初期化)を置き場(module/関数/class)ごとに組む(handle.hy)",
        "展開の形・置き場ごとの import の置き方",
        "節の操作の node の名の一覧(表から引く)",
        "macro handle・defhandler・with-handler",
        "展開の時はなし。実行時の展開は WithHandler を被せる",
        "defhandler の定義した値は name の束縛の寿命",
        "展開が読む doeff の名は `_doeff_` の別名だけ。module の段は import を並べ、関数・class の本体はすぐ呼ぶ関数の中で import する",
    ),
    module(
        "session-write",
        "session var の書き換え `(do (setv x v) (yield (Put key (Some x))))` を作る(binding_forms.py の `_assign`・macros.hy の `set!`)",
        "書き換えの形",
        "保存先の key の作り方(handle.hy の初期化と同じ key)",
        "展開の中の `(:= x v)`・`(set! x v)`",
        "展開の時はなし",
        "なし",
        "Put・Some は別名で引く(型検査のための展開と同じ綴り)",
    ),
    module(
        "static-head-imports",
        "型検査のための展開の module の頭に 1 度だけ置く補助の import の一覧(static_view.py の SESSION_STATIC_IMPORTS・STATIC_HELPER_IMPORTS)",
        "補助の名の一覧",
        "なし",
        "文字列定数(doeff-hy-check が module の頭へ置く)",
        "なし",
        "なし",
        "session の Put・Some は expansion-names の import_source から作る",
    ),
    module(
        "session-protocol",
        "session の値を Get / Put で外の handler へ開く公開の契約(session.py の session_key・ADR-DOE-HY-006 R8・README・外の handler の検)",
        "key の形と Get / Put の意味",
        "なし",
        "doeff_hy.session.session_key(module, handler, name)",
        "状態の効果 Get / Put",
        "セッションの間",
        "外の handler がこの key の Get に答えれば初期化の式を走らせずに値を差し替え、Put を受ければ書き込みを観測できる",
    ),
    module(
        "linter-table",
        "CLAUSE-OPS から書き出した、doeff-linter が読む操作の名・終わり方(packages/doeff-linter/data/clause_ops.json・scripts/export_clause_ops.py)",
        "生成物の中身",
        "なし",
        "scripts/export_clause_ops.py --write",
        "file の書き込み(手で実行する)",
        "なし",
        "表と file が 1 字も違わない(tests/test_linter_clause_ops.py が照らす)",
    ),
    module(
        "bytecode-guard",
        "compile 済みの module の cache の有効・無効を、使った macro の関数の code の閉包の digest で判じる(doeff_hy_bytecode_guard)",
        "digest の作り方と照らし方",
        "なし",
        "macro_use.macro_digest ほか",
        "file の読み(cache)",
        "process の覚え",
        "macro が読む値(素の値・束縛する文)が変われば digest が変わる",
    ),
    module(
        "name-guards",
        "展開の綴りの衝突を機械で止める検(tests/test_handle_writer_names.py・tests/test_expansion_names.py)",
        "監視する名・標本の展開・一般の形の判定",
        "なし",
        "pytest の検",
        "なし",
        "なし",
        "標本の展開が読む・束ねる名は書き手の物か展開の物だけ",
    ),
    module(
        "writer-namespace",
        "利用者の module・class・関数の名の束縛(doeff-hy の外)",
        "書き手の名(effect の class を含む)",
        "なし",
        "なし",
        "なし",
        "利用者の module の寿命",
        "展開が足す名は `_doeff` / `_hy` の頭か、hy・doeff_hy だけ。書き手の同じ名を上書きしない",
    ),
]

SCENARIOS = [
    {
        "id": "S1",
        "axis": "effects",
        "applicability": "applicable",
        "change": "節の操作 `(throw 例外)` を足す(続きへ例外を投げて handler を外す・doeff の node `TransferThrow`、非末尾では続きの後に節へ戻る `ResumeThrow`)",
        "claim": "変えるのは clause-ops-table だけ(CLAUSE-OPS に行を足し、rewrite が `alias-symbol` で node を引く)。handler-expansion・expansion-names・session-write・static-head-imports・writer-namespace は変えない。足した node は `_doeff_TransferThrow` の別名で import され、書き手の module に `TransferThrow` があっても隠さない。",
        "expected_scope": "clause_ops.hy(+ 検)",
        "actual_scope": "盲検 B の差分(clause_ops.hy 26 行)を overlay で当てると、関連の検は 243 件が緑・赤 1 件(tests/test_linter_clause_ops.py — 生成物 packages/doeff-linter/data/clause_ops.json の書き直しが要る)。node は別名で読まれ、handler-expansion・expansion-names・session-write・static-head-imports は変わらない。同時に、書き手の関数 `throw` の呼びが節の本体で黙って操作に書き換わる(基準では pay(-5) が ValueError、overlay では検めごと消える)。盲検 A の型検査の失敗例(class の本体で別名が解けない・handler 65 本以上で Unknown)は基準版にも在る既存の穴で、この変更の退行ではない。設計者が A の probe を基準版と比べて見つけた退行 1 件(import_form が reader の位置 1:1 を持つ model を作り、型検査の診断の位置が handler の行でなく 1:1 になる)は L2234 で直した",
        "changed_modules": ["clause-ops-table", "linter-table"],
        "unchanged_modules": ["handler-expansion", "expansion-names", "session-write", "static-head-imports", "writer-namespace"],
        "reason": "linter-table は別の card が足した派生物の配線で、検が書き直しを求める(契約どおり)。`throw` が書き手の同名の関数を隠すのは、操作の名が節の本体の頭の名として予約される(CLAUSE-OPS の前からの性質)ことの現れで、node の名の衝突とは別の根 — 契約の拡張(新しい予約語)であり隠すはずの知識の漏洩ではない。機械の検では止められない(足す前の呼びと足した後の呼びを source から見分けられない)",
        "revision": "盲検 A・B を受けて tests/test_handle_writer_names.py に、名の一覧に依らない一般の形(展開が読む名は書き手の物か展開の物)と位置の検を足し、expansion_names.import_form が reader を通さないよう直した(L2234)。clause_ops.hy の header に行を足す前の手順(4 repo の節の本体で同じ頭の名の呼びを引く)を書いた。操作の名の予約は機械では止めず、§8 に限界として残す",
    },
    {
        "id": "S2",
        "axis": "storage",
        "applicability": "applicable",
        "change": "session val / session var の保存先を、状態の効果 `Get`・`Put`(+ 値の包み `Some`)から、別の効果の組(例 `ReadSession`・`WriteSession`)へ替える",
        "claim": "変えるのは expansion-names(置き場)と、その名を読む 3 つの綴り(handler-expansion の lazy 初期化、session-write の `_assign` と `set!`)と static-head-imports の名の一覧。clause-ops-table・置き場ごとの組み立て・writer-namespace は変えない。書き手は `Get`・`Put` の名を自由に使え続ける。",
        "expected_scope": "expansion_names.py・handle.hy の lazy 初期化の関数・binding_forms.py の `_assign`・macros.hy の `set!`・static_view.py(+ 検の監視の名)",
        "actual_scope": "名を読む綴りは 4 module・8 行(evidence/s2-census.txt)で、予想どおり。素の `Put` を lazy 初期化で読む変異は 37 件の赤(挙動 28・9 つの名 4・一般の形 4・source の走査 1 — evidence/s2-mutant-bare-put.txt)。ただし `Get`・`Put` と key は外の handler への公開の契約(session-protocol — session.py・README・ADR-DOE-HY-006 R8・外の handler が Get に答えて値を差し替え Put を観測する検 test_val_var_lazy.py)で、保存先を替えるとこの契約の側も変わる。保存先を替える変更そのものは実行していない(census と変異で測った)",
        "changed_modules": ["expansion-names", "handler-expansion", "session-write", "static-head-imports", "name-guards", "session-protocol"],
        "unchanged_modules": ["clause-ops-table", "writer-namespace", "bytecode-guard"],
        "reason": "盲検 A の補足: S2 は保存先の契約の変更で、漏洩ではない。事前の予想の範囲に session-protocol が入っていなかったので、宣言し直した(session-protocol を changed_modules に含める)。展開の綴りの 4 module の波及は予想どおりで、綴りは alias-symbol を引く 1 行ずつ",
        "revision": "S2 を契約の変更として宣言し直した。素の Put の変異を一般の形・名の一覧・挙動の検が拒否する事を測った。保存先を替える実際の変更は実装していない(本設計の範囲外)",
    },
    {
        "id": "S3",
        "axis": "distribution",
        "applicability": "applicable",
        "change": "単一機体から、版の違う doeff-hy を持つ複数の process(worker・会話の Pod・作業木)が、共有の code store(DOEFF_HY_CODE_STORE)にある compile 済みの module を読む形へ",
        "claim": "古い展開(素の名を import した code)で作った cache は、新しい macro の process では使われない — macro の依存の記録(doeff_hy_bytecode_guard)の digest は macro の関数の code の閉包で、`expansion_names` の関数と、それが読む表の値(`_HOMES`・`PREFIX`)を覆う。表を後で書き換えても cache は冷える。変えるのは expansion-names の表の値だけで、bytecode guard は変えない。",
        "expected_scope": "expansion_names.py の表の値(bytecode guard は変えない)",
        "actual_scope": "overlay に doeff_hy を複写し、別 process で handle・defhandler の macro の digest を計算した: 無変更の 2 回と無関係な docstring の書き換えは同じ digest(handle d740a532…・defhandler c8f7d51c…)、_HOMES の値の書き換えと PREFIX の書き換えは別の digest(evidence/s3-macro-digest.txt)。bytecode guard は変えていない。実際の code store の cache の無効化(.pyc の読み直し)そのものは測っていない(digest の変化で代える)",
        "changed_modules": ["expansion-names"],
        "unchanged_modules": ["bytecode-guard", "handler-expansion", "clause-ops-table", "writer-namespace"],
        "reason": "予想どおり。digest は素の値を値で、dict・list を束縛する文の source で覆う(macro_use.py の記述)",
        "revision": "不要 — 主張が実測で成り立った",
    },
    {
        "id": "X-hardware",
        "axis": "hardware",
        "applicability": "excluded",
        "reason": "展開は compile 時の文字列・model の変換で、機器の種類や資源に依らない",
    },
    {
        "id": "X-concurrency",
        "axis": "concurrency",
        "applicability": "excluded",
        "reason": "展開は入力の model から出力の model を作る純粋な関数で、表は module の定数(書き換えない)。alias_symbol は呼ぶたびに新しい Symbol を作り、共有の可変な状態を持たない",
    },
    {
        "id": "X-simulation",
        "axis": "simulation",
        "applicability": "excluded",
        "reason": "時計・IO を差し替える形が展開に無い。展開の出力の決定性は既存の gensym の正準化(test_gensym_canonical.py)が持ち、この変更は gensym を足さない",
    },
]


def check(cid: str, scenarios: list[str], control: str, expectation: str, observation: str, evidence: str) -> dict[str, object]:
    return {
        "id": cid,
        "scenarios": scenarios,
        "control": control,
        "expectation": expectation,
        "observation": observation,
        "evidence": ref(evidence),
        "status": "passed",
    }


CHECKS = [
    check("C1", ["S1"], "positive", "着地した main(fb4dd0f3b)で、書き手の名の effect を受ける挙動の検・展開の構造と位置と一般の形の検・表の検・節の操作の表の検・linter の表の検・型検査の検(pyright strict)がすべて緑", "240 passed(終了の状態 0)", "evidence/final-main-tests.txt"),
    check("C2", ["S1"], "positive", "表に throw・transfer-throw の行を足した(clause_ops.hy だけ)overlay でも、既存の検が行を受け入れる(足した node は別名で読まれる)。赤は生成物 clause_ops.json の書き直しを求める検の 1 件だけ", "243 passed・1 failed(tests/test_linter_clause_ops.py::test_linter_table_is_the_table_written_out — 生成物が表と食い違う)", "evidence/s1-throw-overlay-tests.txt"),
    check("C3", ["S1"], "negative", "表の行の書き換えが新しい node を素の Symbol で読む変異(ResumeBare)を、9 つの名の一覧は拾えず、名の一覧に依らない一般の形が拾う", "tests/test_handle_writer_names.py の一般の形の検 18 件が緑(変異の検は『一覧では 0 件・一般の形が ResumeBare を拾う』を assert して通る)", "evidence/general-guard-mutant.txt"),
    check("C4", ["S1"], "negative", "位置の退行を直す前の綴り(import_form が module の名を hy.read で作る)に戻した overlay で、位置の検が赤になる", "5 failed(import_form が位置を持つ model を作る 4 件 + 型検査の展開の別名の import が 1 行目に置かれる 1 件)", "evidence/position-regression-red.txt"),
    check("C5", ["S1"], "negative", "盲検 B の失敗例(表に throw の行を足すと、書き手の関数 throw の呼びが節の本体で操作に書き換わる)が実際に成立するかを、基準と overlay の対で確かめる。成立すれば契約の拡張として分類し、足す前の手順を書く。機械の拒否は期待しない(限界として §8 に記す)", "成立した: 基準は pay(5)=5・pay(-5)=ValueError、overlay は generator を返す(検めごと消える)/ log に -5 が残り one-shot violation。契約の拡張(新しい予約語)として分類し、clause_ops.hy の header に足す前の手順を書いた。機械の検は拒否しない", "evidence/b-throw-reproduction.txt"),
    check("C6", ["S1"], "negative", "盲検 A の型検査の失敗例(class の本体で別名が解けない・handler 70 本で Unknown)が基準版にも在る既存の穴で、この変更の退行でない事を、基準版と直した版の対で確かめる", "基準版 dc3385b9a も class の本体の素の Pass・WithHandler が未定義(9 件)、N=70 は 70 件の赤。直した main も同じ(class の本体 7 件・N=70 は 70 件)。退行ではない", "evidence/a-many-handlers-reproduction.txt"),
    check("C7", ["S2"], "positive", "session var の挙動(4 つの置き場 × 7 つの名)と、外の handler が Get に答えて session の値を差し替え Put を観測する公開の契約の検が緑", "36 passed", "evidence/session-behavior.txt"),
    check("C8", ["S2"], "negative", "lazy 初期化が素の `Put` を読む変異(handle.hy の 1 行)を、挙動の検・9 つの名の検・一般の形・source の走査が拒否する", "37 failed(挙動 28・9 つの名 4・一般の形 4・source の走査 1)", "evidence/s2-mutant-bare-put.txt"),
    check("C9", ["S3"], "positive", "展開の結果に効かない行(module の docstring の 1 行)だけを書き換えても macro の digest は変わらない(cache は再利用される)", "overlay の plain・plain2・docline の handle=d740a532…・defhandler=c8f7d51c… で同じ", "evidence/s3-macro-digest.txt"),
    check("C10", ["S3"], "negative", "表の値(_HOMES の Get の置き場・PREFIX)を書き換えると macro の digest が変わる(古い cache は使われない)", "homes: handle=1e73921f…・defhandler=b22db0d3…、prefix: handle=a9fc7adf…・defhandler=c49df7b4… と、無変更と違う", "evidence/s3-macro-digest.txt"),
]

REPORT = {
    "version": 1,
    "task_id": TASK,
    "base_revision": BASE,
    "author": {
        "conversation_id": "c-MPWEY1GKS4FAWWR2JS2EDG5SAS",
        "model": "claude-sonnet-5-5",
        "effort": "既定(この会話の起動の設定・自己申告)",
    },
    "requirements": [
        "R1: 書き手が Resume・Transfer・Pass・WithHandler・Get・Put・Some のどれかを自分の effect にした module で、module の段・defk の本体・素の defn の本体・class の本体の handle・defhandler の節が、その effect を受けて答える(card ki-16180480129e の受入 c5b1c7792)",
        "R2: module の段の handle・defhandler の後も、書き手の module の名は書き手の物のまま。展開が module の globals に足す名は書き手が書いた名か展開の物だけ(同 c131d09dd)",
        "R3: 実行時の展開と型検査のための展開は、これらの名を素の名で読みも束ねもしない(読むのは `_doeff_` の頭の別名だけ)",
        "R4: 展開が読む名の綴りは 1 か所の表から引き、節の操作の表 CLAUSE-OPS の行の追加・退役に追従する",
    ],
    "design": ref("design.md"),
    "modules": MODULES,
    "scenarios": SCENARIOS,
    "checks": CHECKS,
    "unresolved": [],
}

if __name__ == "__main__":
    (ROOT / "report.json").write_text(json.dumps(REPORT, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("wrote", ROOT / "report.json")
