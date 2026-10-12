# /// script
# requires-python = ">=3.11"
# ///
"""設計検証 lt-3TF90EJ6BTXWY5VWVS7GRE1CGW の報告 JSON(version 1)を作り、形式の条件(references/report.md)を自分で確かめる。

使い方: build_report.py <docs/design-checks/lt-…/ の dir> <repo の dir>
design.md と evidence の hash はここで計算して埋める(手で書かない)。形式の条件を 1 つでも破れば、書き出さずに 1 で終わる。
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

DIR = Path(sys.argv[1]).resolve()
REPO = Path(sys.argv[2]).resolve()
BASE = "8ff9709b5730a43b265b3d345c46257a91212b39"


def sha(path: str) -> str:
    """報告の dir の下の file の sha256(報告に埋める hash を手で書かないため)。"""
    return hashlib.sha256((DIR / path).read_bytes()).hexdigest()


def ev(name: str) -> dict:
    """checks の evidence 欄(実行記録の path と hash)。"""
    path = f"evidence/{name}.log.txt"
    return {"path": path, "sha256": sha(path)}


ALL = ["M-store", "M-index", "M-resolve", "M-sweep", "M-callers", "M-indexer", "M-checks"]


def others(*changed: str) -> list[str]:
    """changed に挙げなかった module の id(unchanged_modules を書き漏らさないため)。"""
    return [m for m in ALL if m not in changed]


modules = [
    {
        "id": "M-store",
        "responsibility": "根をまたいで分け合う事実 1 つの置き場(packages/doeff-linter/src/project/facts_cache/store.rs)。鍵・配置・符号化・一時名・根の絶対 path の見張り・最後に使われた時刻の運用・事実を並べて消す口を持つ",
        "owns": "鍵の作り方(長さ前置きの SHA-256 の頭 16 byte)・事実の path(object_path)・一時 file の名と見分け・符号化(JSON / bincode)・LeakGuard の針・読んだ時の更新の間隔(24 時間)・list(配置の深さに依らない)と remove",
        "hides": "file の配置・符号化・一時名・hash の種類・見張りの針。StoredObject の path",
        "interface": "pub(super): object_key・read_object・write_object・list・remove・Format・LeakGuard・ObjectKey・OBJECTS_DIR。非公開: object_path・encode_value・decode_value・temporary_path・is_temporary",
        "effects": "objects/ の下の file の読み書き・古い一時 file の削除",
        "lifetime": "事実は置いたら書き換えない。消えるのは片づけ(M-sweep)だけ",
        "invariants": "同じ鍵なら同じ内容・書きかけは見えない(一時 file から rename)・読めない物は無い物・根の絶対 path を持つ値は置かない",
    },
    {
        "id": "M-index",
        "responsibility": "作業木(根)ごとの表: file → 大きさ・更新時刻・事実の鍵(facts_cache.rs の表の部分)",
        "owns": "行の形 Row・16 塊の分け方 shard_of・どの塊を書き直すか",
        "hides": "事実の中身",
        "interface": "内部(per_file_at が使う)",
        "effects": "根の dir の下の塊の読み書き",
        "lifetime": "根の dir の寿命(作業木が消えれば片づく)",
        "invariants": "印(identity)が違う・壊れた塊は無い物として読む",
    },
    {
        "id": "M-resolve",
        "responsibility": "file 1 つの事実を引く手順(facts_cache.rs の per_file_at と公開の入口)",
        "owns": "早い判じ(大きさ・更新時刻が同じなら中身を読まず前の鍵で引く)→ 中身の hash → 置き場にあれば読む・無ければ作って置く、の順・読む間に file が変わった時は覚えない・返す前に bind を当てる",
        "hides": "表の形と置き場の配置",
        "interface": "per_file・per_file_keyed・per_file_compact・per_file_compact_bound・keyed_value・mark_used(sweep の再公開)",
        "effects": "file の読み(中身の hash)・compute の呼び",
        "lifetime": "1 回の実行",
        "invariants": "答えは cache 無しの compute と同じ・置く値は bind の前の形(根の絶対 path を持たない)・store から使う名は鍵で置く・読む口だけ(名の走査の検が守る)",
    },
    {
        "id": "M-sweep",
        "responsibility": "置き場の寿命(packages/doeff-linter/src/project/facts_cache/sweep.rs)。上限・古い順・使っている根の 10 分・消えた根・使った印",
        "owns": "上限(既定 4 GiB)・IN_USE_SECONDS=600・番の間隔・根の dir を先に消し事実は古い順に消す順・objects の使った印・mark_used",
        "hides": "事実と表の中身・事実の配置(store の list が返した物を remove するだけ)",
        "interface": "pub(super): tend_roots・note_object_bytes。公開: mark_used",
        "effects": "置き場の走査と削除・記録の file",
        "lifetime": "実行ごとに 1 度(tend_roots)+ 新しく置いた量が 32MiB を越えた時 1 度",
        "invariants": "使っている根・置いてから(か更新してから)10 分の内の事実は消さない・消しても答えは変わらない・store から使う名は list・remove・OBJECTS_DIR だけ",
    },
    {
        "id": "M-callers",
        "responsibility": "種類ごとの事実の意味(compute)と、根の絶対 path を持つ値の bind(project/mod.rs の cached_hy_files・project/signatures.rs の World::build)",
        "owns": "事実の中身・どの欄が根の絶対 path を持つか・種類の鍵(failure-types の設定の指紋・program-params の defk の名の指紋)",
        "hides": "置き場の形・鍵の作り方",
        "interface": "compute(rel, path) と bind(&mut value, rel, path)。FileFacts::set_path",
        "effects": "file の読み",
        "lifetime": "なし",
        "invariants": "置く値は中身・rel・渡した鍵だけで決まる・根の絶対 path が無い",
    },
    {
        "id": "M-indexer",
        "responsibility": "Hy の索引の cache の形 CachedHyFile と path 欄の所有(packages/doeff-indexer の hy_index/cache_form.rs)",
        "owns": "索引のどの欄が絶対 path か",
        "hides": "—(欄の形は呼び手に見えるが、path の欄は set_path だけが替える)",
        "interface": "CachedHyFile::of・set_path・into_file",
        "effects": "なし(副作用なし)",
        "lifetime": "なし",
        "invariants": "set_path 以外の欄は中身と根からの path だけで決まる",
    },
    {
        "id": "M-checks",
        "responsibility": "上の責務の不変条件を観測する検と契約: facts_cache.rs の unit 15 本・tests/facts_cache_roots.rs 16 本・tests/facts_cache_shared.rs 7 本・.agents/code-quality.d/doeff-linter-1828.json",
        "owns": "箱の外(binary と一時 dir)から数える方法・どの名をどの file が使ってよいかの表",
        "hides": "—(配置の知識を持たない形で数える)",
        "interface": "cargo test(make test-rust)・code-quality --scope changed",
        "effects": "一時 dir で binary を走らせる",
        "lifetime": "検の 1 走り",
        "invariants": "配置を替えても検が全部通ったまま上限が効かなくなる事が無い・実在の呼び手が約束を破れば検が赤になる",
    },
]

scenarios = [
    {
        "id": "S1", "axis": "storage", "applicability": "applicable",
        "change": "事実の符号化を替える(先頭に版の 1 byte を付け、無ければ読めない物として作り直す)",
        "claim": "符号化は M-store の encode_value / decode_value に閉じ、表・引く手順・片づけ・呼び手は変わらない(design-pre-blind.md の S1)",
        "expected_scope": "M-store だけ",
        "actual_scope": "着地版の写しとの差分は store.rs の 2 関数だけ。lib 14・roots 16・shared 7 が全部緑。store の外の 2 file から符号化・配置・一時名の非公開の名 5 つを使う試み 10 通りは全部 E0603 で拒否",
        "changed_modules": ["M-store"], "unchanged_modules": others("M-store"),
        "reason": "予想どおり。符号化は M-store が隠す知識で、公開契約は変わらない",
        "revision": "不要(予想どおり)",
    },
    {
        "id": "S2", "axis": "effects", "applicability": "applicable",
        "change": "新しい種類の file ごとの事実を足す。値は根の絶対 path を持つ",
        "claim": "変わるのは呼び手(新しい種類の compute と bind)と、その値の型だけ。置き場・表・引く手順・片づけは変わらない(design-pre-blind.md の S2)",
        "expected_scope": "M-callers(+ 値の型)",
        "actual_scope": "既存の 2 種類(Hy の索引・signature の事実)は呼び手と set_path だけで作業木をまたいで分け合えている。約束を破る変種は実在の呼び手で赤になる: Hy の索引で path を空にし忘れる → shared の 2 本が赤、signature で bind を付け忘れる → lib の 2 本が赤",
        "changed_modules": ["M-callers", "M-indexer"], "unchanged_modules": others("M-callers", "M-indexer"),
        "reason": "新しい種類の追加で変わる範囲は呼び手のまま。ただし事前の予想になかった置く所の見張りを M-store に足した(盲検 B の別候補への対応)ので、置き場の側に手を入れない予想は外れた。見張りは種類に依らず、新しい種類ごとには変わらない",
        "revision": "LeakGuard を M-store に足し、Place に持たせて write_object の前に確かめる形にした(第 2 版)。再検証: unit 3 本・L-U1・L-U2b",
    },
    {
        "id": "S3", "axis": "concurrency", "applicability": "applicable",
        "change": "同じ置き場へ多数の linter が並走し、片づけが事実を消し、実行が途中で殺される",
        "claim": "正しさは置き場の中身に依らない。読めない・無い・壊れた事実は作り直し、書きは一時 file から rename、同じ鍵の事実は同じ内容。コードの変更は要らない(design-pre-blind.md の S3)",
        "expected_scope": "なし",
        "actual_scope": "答えは cache 無しと同じ(3 つの種・各 30 round・根 8 本で確かめた実行 600 本、食い違い 0)。ただし M-sweep の不変条件の言い方「10 分の内の事実は消さない」は、読んだだけでは更新時刻が動かない事実に成り立たなかった(盲検 A の実験 2)",
        "changed_modules": ["M-store", "M-sweep"], "unchanged_modules": others("M-store", "M-sweep"),
        "reason": "答えの正しさのための変更は要らなかった(予想どおり)。変わったのは守りの言い方(M-sweep の註と設計)と更新の間隔の定数(M-store)だけで、公開契約の拡張ではなく、元の言い方が実装より強かった",
        "revision": "主張を「置いてから(か更新してから)10 分」に絞って註と設計に書き、読んだ時の更新の間隔を 6 時間から 24 時間に整理した(第 2 版)。再検証: L-A3(絞った主張どおりの観測)・unit reading_an_old_fact_refreshes_its_last_used_time",
    },
    {
        "id": "S4", "axis": "simulation", "applicability": "applicable",
        "change": "置き場の状態(空・温・一部欠け・壊れ・表だけ欠け・事実だけ欠け・同じ根で 1 file を編集)と thread の数(1・2・8)を替えて、同じ木を再現する",
        "claim": "答えは置き場の状態と thread の数の関数ではない。コードの変更は要らない(design-pre-blind.md の S4)",
        "expected_scope": "なし",
        "actual_scope": "8 状態 × 3 thread × 2 本の 48 本で、答えの種類は木ごとに 1 つ(2 つの木で 2 つ)・食い違い 0",
        "changed_modules": [], "unchanged_modules": list(ALL),
        "reason": "予想どおり",
        "revision": "不要(予想どおり)。検の感度は stamp を信じ過ぎる変種(6 本食い違い)・壊れた事実で止まる変種(6 本食い違い)で確かめた",
    },
    {
        "id": "S5", "axis": "hardware", "applicability": "applicable",
        "change": "置き場を書けない・読み専用・dir を作れない・更新時刻を持てない場所へ移す",
        "claim": "変わるのは DOEFF_LINTER_CACHE_DIR の値だけ。書けない時は黙って作り直し、答えは同じ(design-pre-blind.md の S5)",
        "expected_scope": "なし(設定の値だけ)",
        "actual_scope": "温かい読み専用・空の読み専用・途中が file・objects だけ読み専用・まだ無い置き場・更新時刻が全部 1970 年・木の更新時刻が全部 1970 年の 7 本と、書けなかった事の確かめ 2 本で、答えが同じ・失敗しない",
        "changed_modules": [], "unchanged_modules": list(ALL),
        "reason": "予想どおり。本物の別の FS(NFS・overlay)は試していない(design.md 第 7 節)",
        "revision": "不要(予想どおり)。検の感度は書き失敗で止まる変種(3 本食い違い)で確かめた",
    },
    {
        "id": "S6", "axis": "distribution", "applicability": "excluded",
        "reason": "置き場は機体の局所(/work の下)で、linter の印は binary の大きさと更新時刻なので機体をまたいで安定しない。機体間の共有は今回の要求に無く、未要求の分散基盤を先回りして作らない",
    },
    {
        "id": "S7", "axis": "storage", "applicability": "applicable",
        "change": "事実の置き場の配置を変える(1 事実 1 file の fan の段を 1 つ増やす。本命は種類ごとにまとめた file)— 盲検 A の失敗例",
        "claim": "配置は M-store が隠す知識で、M-store の object_path だけが変わる(design-pre-blind.md の M-store の行)",
        "expected_scope": "M-store",
        "actual_scope": "修正前(第 1 版): 1 行の変更で提示された検 34 本が全部通ったまま、片づけが事実を 0 byte と数え、30 日前の事実 30 個と 2 時間前の一時 file が残った。M-store と M-sweep とその検が一緒に変わる必要があった。修正後(第 2 版): 同じ 1 行で lib 659・roots 16・shared 7・focused 3・hy_index 1 が緑・30 日前の事実の残り 0・一時 file の残り 0",
        "changed_modules": ["M-store"], "unchanged_modules": others("M-store"),
        "reason": "修正前は隠すはずの知識(配置の深さ・一時 file の名)が M-sweep とその検へ漏れていた = 漏洩。公開契約の拡張でも配線の変更でもない",
        "revision": "配置・一時名・列挙を M-store に集約し、列挙を配置の深さに依らない list にした。片づけは list が返した物を remove するだけ。再検証: L-A1・L-A2・N-S7(列挙の深さを固定に戻すと unit が赤)",
    },
    {
        "id": "S8", "axis": "concurrency", "applicability": "applicable",
        "change": "引く手順が、表を書き直す場で前の事実を消す(editor の保存ごとに増える事実を抑えたい)— 盲検 B の失敗例",
        "claim": "事実の寿命の判断と削除は M-sweep だけが持つ。引く手順と表には消す口が無い(design-pre-blind.md の M-sweep・M-resolve の行)",
        "expected_scope": "M-sweep",
        "actual_scope": "修正前(第 1 版): 提示された検 34 本が全部通ったまま、他の作業木が使う事実 4 個を消し、別の commit へ移った作業木の後に元の commit の木を作ると 95.9% を書き直した。修正後(第 2・3 版): 同じ差分は E0603(事実の path を作れない)。非公開を緩めた木に同じ差分を当てても動作の検 2 本が赤。親が list・remove を呼ぶ変種は名の走査が赤",
        "changed_modules": ["M-sweep"], "unchanged_modules": ["M-index", "M-callers", "M-indexer"],
        "reason": "修正前は寿命の判断と削除の effects が M-sweep の外(引く手順)へ移れた = 責務の漏洩。この種の機能は M-sweep に置く事になる(置き場の側の公開契約の拡張が要る場合は M-store も変わりうるが、それは検証していない)",
        "revision": "事実の path を作れるのを store だけにした(第 2 版)。並べて消す口の使い手を名の走査で縛った(第 3 版・設計者が前の版の強制の穴に気づいた)。再検証: L-B1・L-B2・N-S8",
    },
]

CHECK_ROWS = [
    ("C-S1-P", ["S1"], "positive",
      "事実の符号化だけを替えた着地版の写しで、lib・roots・shared の検が全部緑",
      "lib 14・roots 16・shared 7 が緑", "S1-p2-encoding-only-integration"),
    ("C-S1-P2", ["S1"], "positive",
      "その変更の差分が store.rs の encode_value・decode_value の 2 関数だけ",
      "diff -rq が store.rs の 1 file だけを示し、diff -u の hunk は 2 つ", "S1-p3-encoding-only-diff"),
    ("C-S1-N", ["S1"], "negative",
      "store の外(sweep.rs・facts_cache.rs)から、符号化・配置・一時名の非公開の名を使うと拒否される",
      "5 つの名 × 2 file の 10 通りが全部 E0603(`名` は非公開)で cargo check 失敗", "L-S1-privacy-of-store-internals-from-sweep-and-parent"),
    ("C-S2-P", ["S2"], "positive",
      "根の絶対 path を持つ既存の 2 種類が、呼び手の compute と bind だけで作業木をまたいで分け合え、置き場に作業木の path が残らず、答えが cache 無しと同じ",
      "着地版で lib 14・roots 16・shared 7 が緑(shared に no_working_tree_path…・a_second_checkout… を含む)", "T-landed-integration-shared-roots"),
    ("C-S2-N1", ["S2"], "negative",
      "Hy の索引の compute が path を空にし忘れると(見張りが置かず作り直し)、shared の検が赤になる",
      "shared 7 本のうち 2 本が赤(a_second_checkout_of_the_same_tree_writes_less_than_a_tenth_of_the_first・a_tree_that_moves_on…)", "L-U1-hy-index-forgot-to-empty-the-path"),
    ("C-S2-N2", ["S2"], "negative",
      "signature の bind を付け忘れると、位置の path が空のまま答えに出る事を lib の検が拾う",
      "lib 659 本のうち 2 本が赤(bindings_carry_types_or_null_when_unknown・effects_are_inferred_transitively_with_absent_and_raise)。統合検の shared は緑のまま = 統合検の層だけでは見えない限界(design.md 第 7 節)", "L-U2b-lib-on-forgot-to-bind-the-path"),
    ("C-S3-P", ["S3"], "positive",
      "並走・片づけ・途中死・壊れた事実を混ぜても、完走した実行と途中死の後の 1 本の答えが cache 無しと同じ",
      "3 つの種で確かめた実行 600 本・食い違い 0。片づけに差し出した事実が実行の間に消えた数は 24・30・20(片づけが実際に走った)", "C3-landed-concurrency-sweep-kill-corrupt"),
    ("C-S3-P2", ["S3"], "positive",
      "絞った主張(置いてから、か更新してから 10 分)どおり、古い事実を読むと更新時刻が今になる",
      "着地版の lib 14 本が緑(reading_an_old_fact_refreshes_its_last_used_time を含む)", "T-landed-lib-facts_cache"),
    ("C-S3-N1", ["S3"], "negative",
      "表の行の大きさ・更新時刻を見ずに信じる変種は、検が答えの食い違いで赤にする",
      "確かめた実行 205 本のうち食い違い 77 本(exit 1)", "N-C3-mutant-stamp-trusted-always"),
    ("C-S3-N2", ["S3"], "negative",
      "壊れた事実を読んだ時に止まる変種は、検が答えの食い違いで赤にする",
      "確かめた実行 243 本のうち食い違い 219 本(exit 1)", "N-C3-mutant-corrupt-fact-panics"),
    ("C-S3-N3", ["S3"], "negative",
      "修正前の言い方(読んだ事実は 10 分は消えない)は成り立たない事が実測できる",
      "2 時間前に置いた事実 30 個を読み直しても更新時刻は動かず、数秒後に別の根の片づけで全部消えた(30 → 0)", "A3-touch-vs-in-use-exp-orig"),
    ("C-S4-P", ["S4"], "positive",
      "置き場の 8 状態と thread 1・2・8 の全部で、答えが cache 無しと同じ",
      "48 本・食い違い 0・答えの種類は 2(2 つの木で 1 つずつ)", "C4-landed-store-state-and-threads"),
    ("C-S4-N1", ["S4"], "negative",
      "同じ根で file を編集した後に古い事実を返す変種(行の stamp を信じ過ぎる)は、検が赤にする",
      "確かめた実行 48 本のうち食い違い 6 本(exit 1)", "N-C4-mutant-stamp-trusted-always"),
    ("C-S4-N2", ["S4"], "negative",
      "壊れた事実で止まる変種は、検が赤にする",
      "確かめた実行 48 本のうち食い違い 6 本(exit 1・答えの種類 3)", "N-C4-mutant-corrupt-fact-panics"),
    ("C-S5-P", ["S5"], "positive",
      "読み専用・書けない・途中が file・更新時刻が 1970 年の置き場でも、答えが cache 無しと同じで失敗しない",
      "確かめた実行 9 本(書けなかった事の確かめ 2 本を含む)・食い違い 0", "C5-landed-unwritable-and-readonly-cache"),
    ("C-S5-N", ["S5"], "negative",
      "置き場の dir を作れない時に止まる変種は、検が赤にする",
      "確かめた実行 9 本のうち食い違い 3 本(終了状態 101・stderr に panic)", "N-C5-mutant-write-failure-is-fatal"),
    ("C-S7-N0", ["S7"], "negative",
      "修正前の木に配置を 1 行替える変更を当てると、提示された検が全部通ったまま事実の上限が効かなくなる",
      "上限 1000 byte の片づけの後に、30 日前の事実 30 個と一時 file 1 個が残り、objects は 19800 → 39600 byte(提示の検 34 本は緑: A1 の 2 つの記録)", "A2-layout-cap-exp-A-copy"),
    ("C-S7-P1", ["S7"], "positive",
      "着地版へ同じ 1 行を当てても、提示された検が全部緑",
      "lib 659・roots 16・shared 7・focused 3・hy_index 1 が緑", "L-A1-presented-tests-on-landed-with-layout-change"),
    ("C-S7-P2", ["S7"], "positive",
      "着地版へ同じ 1 行を当てても、上限の片づけが事実と一時 file を消す",
      "30 日前の事実の残り 0・一時 file の残り 0・objects の byte 19800(配置を替えない版と同じ)", "L-A2-layout-cap-exp-on-landed-with-layout-change"),
    ("C-S7-N1", ["S7"], "negative",
      "列挙が配置の深さを固定で持つ変種(max_depth(3))は、深い配置を数えない事を unit が赤にする",
      "facts_cache の unit 14 本のうち every_object_written_is_listed_and_a_deeper_layout_is_still_counted が赤(他 13 本は緑)", "N-S7-mutant-lister-hardcodes-the-layout-depth"),
    ("C-S8-N0", ["S8"], "negative",
      "修正前の木に、表を書き直す場で前の事実を消す差分を当てると、提示された検が全部通ったまま不変条件と R1 が破れる",
      "再現の検 2 本が赤(元の木では 2 本緑)。提示の検 34 本は B1 の 2 つの記録で緑", "B2-repro-on-B-copy"),
    ("C-S8-P", ["S8"], "positive",
      "最終の版で、別の commit へ移る木・1 つの木での書き換えが他の木の事実を消さない動作の検を含め、全部緑",
      "lib 660・roots 16・shared 7・focused 3・hy_index 1 が緑", "T-final-lib-and-integration"),
    ("C-S8-N1", ["S8"], "negative",
      "着地版へ同じ差分を当てると、事実の path を作れずコンパイルが拒否する",
      "E0603: function `object_path` is private(cargo build 終了状態 101)", "L-B1-privacy-compile-on-landed"),
    ("C-S8-N2", ["S8"], "negative",
      "非公開を緩めた木にその差分を当てても、動作の検が赤にする(第 2 の守り)",
      "shared 7 本のうち 2 本が赤", "L-B2-behavior-tests-on-B-diff-with-privacy-loosened"),
    ("C-S8-N3", ["S8"], "negative",
      "親が store の list・remove を呼ぶ・片づけが read_object を使う・store が IN_USE_SECONDS を持つ変種は、コンパイルが通った上で名の走査が赤にする。コメントに名が出るだけなら通す",
      "M1・M2・M3 は検が赤(意図した理由の言い分つき)、M0・M4 は緑。全変種でコンパイルの失敗 0 行", "N-S8-owner-scan-rejects-names-used-by-the-wrong-file"),
]


def make_check(cid: str, scenarios_: list[str], control: str, expectation: str, observation: str, evidence: str) -> dict:
    """検 1 件の記録(status は実行済みの最小実験だけを passed にした物 — 未実行を登録しない)。"""
    return {
        "id": cid, "scenarios": scenarios_, "control": control,
        "expectation": expectation, "observation": observation, "evidence": ev(evidence), "status": "passed",
    }


checks = [make_check(*row) for row in CHECK_ROWS]

report = {
    "version": 1,
    "task_id": "lt-3TF90EJ6BTXWY5VWVS7GRE1CGW",
    "base_revision": BASE,
    "author": {
        "conversation_id": "c-FNBY8EYQ0VTKJ982S71BNFJB42",
        "model": "claude-sonnet-5-5",
        "effort": "未観測(会話の起動の設定を読めない — 自己申告できない)",
    },
    "requirements": "card ki-9743c30d49bc の受入: c5817ce0d 同じ commit を checkout した 2 つの作業木で doeff-linter を順に走らせた時、2 本目で事実の cache の下に新しく書かれる byte が 1 本目の 10% 未満である事を test で示した / cd2c10f4a zeus の /work で 1 時間に事実の cache の下で更新される file の量が 3.4GB から半分以下に減った事を測って card に書いた(本番の運用の観測で、設計検証の範囲の外)。依頼 lt-3TF90EJ6BTXWY5VWVS7GRE1CGW: 事実の entry を引く値に中身の hash を足し、根ごとの dir には file → entry の小さな表だけを置く。答えは変えない",
    "design": {"path": "design.md", "sha256": sha("design.md")},
    "modules": modules,
    "scenarios": scenarios,
    "checks": checks,
    "unresolved": [],
}

# ---- references/report.md の条件を自分で確かめる ------------------------------------------------------------------
def find_problems() -> list[str]:
    """形式の条件を破っている所の一覧(空なら条件を満たす)。"""
    ids = [m["id"] for m in modules]
    sids = [s["id"] for s in scenarios]
    cids = [c["id"] for c in checks]
    applicable = [s for s in scenarios if s["applicability"] == "applicable"]
    required_scenario_keys = ("change", "claim", "expected_scope", "actual_scope", "changed_modules", "unchanged_modules", "reason", "revision")
    exists = subprocess.run(["git", "-C", str(REPO), "cat-file", "-e", f"{BASE}^{{commit}}"], capture_output=True).returncode == 0
    design = DIR / "design.md"
    evidence_paths = [(c["id"], DIR / c["evidence"]["path"], c["evidence"]["sha256"]) for c in checks]
    return (
        (["module id が重複"] if len(ids) != len(set(ids)) else [])
        + [f"{m['id']}.{key} が空" for m in modules for key in ("responsibility", "owns", "hides", "interface", "effects", "lifetime", "invariants") if not str(m.get(key, "")).strip()]
        + (["scenario id が重複"] if len(sids) != len(set(sids)) else [])
        + ([f"6 軸が揃わない: {sorted({s['axis'] for s in scenarios})}"] if {s["axis"] for s in scenarios} != {"hardware", "storage", "effects", "distribution", "concurrency", "simulation"} else [])
        + (["適用のシナリオが無い"] if not applicable else [])
        + [f"{s['id']} の除外に reason が無い" for s in scenarios if s["applicability"] == "excluded" and not s.get("reason")]
        + [f"{s['id']}.{key} が無い" for s in applicable for key in required_scenario_keys if key not in s or (isinstance(s[key], str) and not s[key].strip())]
        + [f"{s['id']} が未宣言の module {m} を指す" for s in applicable for m in s["changed_modules"] + s["unchanged_modules"] if m not in ids]
        + [f"{s['id']} の changed と unchanged が重なる" for s in applicable if set(s["changed_modules"]) & set(s["unchanged_modules"])]
        + (["check id が重複"] if len(cids) != len(set(cids)) else [])
        + [f"{s['id']} に正常例と失敗例の両方が無い" for s in applicable if {c["control"] for c in checks if s["id"] in c["scenarios"]} != {"positive", "negative"}]
        + [f"{c['id']} が passed でない" for c in checks if c["status"] != "passed"]
        + [f"{c['id']} が未宣言の scenario {sid} を指す" for c in checks for sid in c["scenarios"] if sid not in sids]
        + [f"{cid} の evidence が無い" for cid, path, _ in evidence_paths if not path.is_file()]
        + [f"{cid} の evidence の hash が違う" for cid, path, digest in evidence_paths if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() != digest]
        + [f"{cid} の evidence が 2MB 超" for cid, path, _ in evidence_paths if path.is_file() and path.stat().st_size > 2_000_000]
        + (["design.md が無いか 2MB 超"] if not design.is_file() or design.stat().st_size > 2_000_000 else [])
        + ([f"base_revision {BASE} が repo に実在しない"] if not exists else [])
        + (["base_revision が 40 桁でない"] if len(BASE) != 40 else [])
        + (["unresolved が空でない"] if report["unresolved"] else [])
    )


problems = find_problems()

if problems:
    print("形式の条件を満たさない:")
    print("\n".join(f" - {line}" for line in problems))
    sys.exit(1)

out = DIR / "design-check.json"
out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n")
print(f"書き出した: {out} ({out.stat().st_size} byte)・module {len(modules)}・scenario {len(scenarios)}(適用 {sum(s["applicability"] == "applicable" for s in scenarios)})・check {len(checks)}")
