# 盲検 A の返答: DOEFF002 の初期化の集合に 1 つ足すと、関係のない規則の直しまで同じ commit に入る

## 結論

DOEFF002 の初期化の集合を 1 つ広げる時、鍵の替えが遅れていると、DOEFF002 と関係のない規則の新しい所見まで、同じ commit で直す事になります。直す先は M4・M6 のような、他の規則の持ち主の module です。S1 は「M3 は version の 1 行、M4〜M6 の code は変わらない」と予想しています。この予想は「基点の鍵が HEAD の linter の鍵と同じ」という、書かれていない前提の上でしか成り立ちません。しかも、その前提を保つ仕組みがありません。

- これは条件つきの失敗です。今の HEAD は、基点の鍵と HEAD の鍵が同じです(実測 E)。今すぐ 1 つの commit で行えば、S1 の予想どおりに収まります。
- 繰り返し: 2 つ目の失敗 F の前提ではなく実績です。10-03〜10-11 の 8 日間に、linter の組み立ての入力を変える commit が 28 本入り、基点の file を書いた commit は 0 本でした。直近の `__set_name__` の直しでも、まさにこの波及が起きました(4ccab04ac)。
- 推測と実測は、各項に【実測】【推測】と付けて分けます。

## 1. 変更の要求と、それが要る訳

**要求(S1 の軸の続き)**
- thread をまたいで共有され、lock を持つ object を、pickle や `copy.deepcopy` で別の process(`ProcessPoolExecutor` 等)へ渡せるようにします。
- `threading.Lock` は pickle できません。そのため `__getstate__` で lock を落とし、`__setstate__` で作り直すのが Python の定型です。
- 例: `program_effects.py` の `_Reader` は `self.lock = threading.RLock()` を持っています。これを process へ渡す必要が出るかどうかは【推測】です。

**この形を DOEFF002 は誤りと見ます【実測 A】**
- `__setstate__` の中の `self._lock = threading.Lock()` が error になります。
- `__setstate__` は、pickle から戻す時の初期化の口です。`__init__` は呼ばれません。
- このままだと、選べる道は 2 つしかありません。
  - 作り直した後は変わらない lock に `_mut_lock` と名乗らせる。これは「作られた後に変わる状態を名で見せる」という目的に反します。
  - noqa で外す。
- `__set_name__` を足した時(7b3a9b190)と同じ理屈で、M1 の `is_init_method` に `__setstate__` を足すのが筋になります。

## 2. 関係する主張と、予想に反して変わる所

**S1 の主張**
- 初期化の集合を変えるのは `is_init_method` の 1 か所だけ。
- 予想の範囲は M1(とその検・M2 の文)。M3 は `version` の 1 行で、M4〜M6 の code は変わらない。
- 前提は「代入は `self.<名> = …` の形」。この例の `self._lock = threading.Lock()` は、この前提を守っています。

**S2 の主張**
- 鍵の替えは `version` の 1 行と、下がる向きの数だけ。
- 鍵の合わない状態では「測れない」と名乗る。
- 判定に入る所見は直す。

**実際に要る変更(●は予想の外)**

| 所 | 中身 | 区別 |
|---|---|---|
| M1 `doeff002_…rs` | 頭の註(3〜6 行)・`is_init_method`(25 行)・所見の文の文字列 `"…outside __init__/__post_init__/__set_name__…"`(198 行)の 3 か所に、同じ集合が手で書かれている | 契約の拡張。M1 の中なので範囲内(「1 か所」は字面では 3 か所) |
| M2 `rule_info.rs:29`・`report.rs:31` | 集合を列挙した説明文が 2 つ | 文の写し。範囲内 |
| M3 `doeff-linter.json` の `version` | 鍵の替え。替えないと、hook も日次も古い linter で黙って測る【実測 D】 | 共同の不変条件。範囲内 |
| ● 他の規則の所見の持ち主 | 鍵は「規則ごと」ではなく「linter の crate 全体 + doeff-indexer」で 1 つ。前に鍵を替えてから入った linter の変更が、まとめて判定に入る。そこで増えた error は、同じ commit で直す(基点に足さない) | 予想の外の波及 |
| ● M4 `tools/architecture.hy` など | 実例 4ccab04ac: DOEFF002 の直しの鍵の替えで DOEFF004 の所見が判定に入り、`tools/architecture.hy` に層 `release` と外しを足し、検 5 file を直した【実測 F・G】 | 予想の外の波及 |

複数の責務を持つ module は次の 2 つです。
- `doeff-linter.json`: 全部の規則の数と、ただ 1 つの鍵を 1 file に持ちます。
- `tools/architecture.hy`: 層の宣言と、複数の規則の外しを持ちます。

## 3. どの知識がどこへ漏れるか

**持ち主のいない判断**
「HEAD の linter の意味と、関門が測る意味が同じか」を判断する所が、どこにもありません。

- hook(`linter_for_checking`)は、基点の鍵の binary を探すだけです。HEAD の鍵とは比べません。
- 日次(`gate_tools.sh linter`)は、「基点の file を最後に書いた commit」の linter を組みます。
- 鍵の決め方が、基点の file と `gate_tools.sh` の git log の 2 か所に別々に書かれています。
- semgrep には、版が違えば赤にする仕組みがあります(`hook_finding_baseline.py:371-376`、検 `test_a_lock_version_other_than_the_baseline_is_named_and_red`)。doeff-linter の側には同じ仕組みがありません。
- 「linter の版を変えた便が lower する」という決まりは、docstring(17〜18 行)に書いてあるだけで、どこも強いていません。

**漏れる先**
- 他の規則の持ち主の意味の変更(例: DOEFF004 を広げた c0b389c42・f6b62abdb)の代価が、次に鍵を替える人へ先送りされ、押し付けられます。
- DOEFF002 を変える人が、DOEFF004 の意味と M4 の層の宣言まで知って直す事になります。これは、隠すはずの知識が漏れているという事です。

**意図した物と漏れの区別**
- 契約の拡張(意図): `__setstate__` を集合に足す事。
- 文の写し: M2 の 2 file。`editor.rs:344` を通って IDE の `statement` へ出る文も同じです。IDE の検の fixture(`ide-plugins/vscode/doeff-runner/test-fixtures/lint/rewrites.json:876` など)には、直す前の古い文が残っています。fixture は動いている linter と比べられていないので、赤にはなりません【推測】。
- 共同の不変条件(意図・S2 に明記): `version` の 1 行と、数を下げる事。
- 漏洩: 規則の単位ではなく道具の単位の鍵と、鍵を替える時機を誰も強いない事。これにより、関係のない規則の所見が DOEFF002 の変更に乗ってきます。

## 4. 最小の入力・再現の手順・観測する事

【実測】一時の dir は `mktemp -d` で作り(/tmp/tmp.NKvkQ5sIhM)、最後に消しました。作業木は `git status --short` で空のままです。B = `/home/ubuntu/.cache/doeff-linter-snapshots/02fca1e258949b32bb62c7ad708aab9873e0b252/doeff-linter` です。

**A. 入力(s1_setstate.py)**
```python
import threading
class SharedCounter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mut_count = 0
    def bump(self) -> None:
        with self._lock:
            self._mut_count += 1
    def __getstate__(self) -> dict[str, object]:
        return {"_mut_count": self._mut_count}
    def __setstate__(self, state: dict[str, object]) -> None:
        self.__dict__.update(state)
        self._lock = threading.Lock()
```
```
$ B --no-log --no-config --enable DOEFF002,DOEFF003 --output-format json s1_setstate.py
"rule": "DOEFF002", "severity": "error", "violations": [{"file": "s1_setstate.py","line": 20,"source": "self._lock = threading.Lock()"}]   rc=1
```

**D. M1 だけ変えて鍵を替えない状態は、黙って古い linter で測られる(実例 7b3a9b190 で模した)**
```
$ cd <作業木>; for c in 780ef711a 61f060009 7b3a9b190 4ccab04ac HEAD; do (scripts/doeff_linter_locked.py key $c・git show $c:基点の version・git log -1 $c -- 基点) ; done
780ef711a | HEAD鍵=bee71305… | 基点の鍵=bee71305… | 日次が組む commit=780ef711a
61f060009 | HEAD鍵=c3f8ad3d… | 基点の鍵=bee71305… | 日次が組む commit=780ef711a
7b3a9b190 | HEAD鍵=5c59502e… | 基点の鍵=bee71305… | 日次が組む commit=780ef711a
4ccab04ac | HEAD鍵=5c59502e… | 基点の鍵=5c59502e… | 日次が組む commit=4ccab04ac
HEAD      | HEAD鍵=5c59502e… | 基点の鍵=5c59502e… | 日次が組む commit=4ccab04ac
$ uv run --no-project python -I -c '…h.linter_for_checking(top, <7b3a9b190 の基点>)…'
check が選ぶ道具: Instrument(command=('/home/ubuntu/.cache/doeff-linter-snapshots/780ef711aa44…/doeff-linter',), version='… bee713059f…')
$ git show 780ef711a:…/doeff002_mutable_attribute_naming.rs | grep -A2 "fn is_init_method"
        func_name == "__init__" || func_name == "__post_init__"
```
- M1 を直した 7b3a9b190 でも、関門は `__set_name__` を知らない 780ef711a の binary を選びます。
- このとき「測れない」とは名乗りません。S2 の「鍵の合わない状態では測れないと名乗る」は、「基点の鍵 ≠ HEAD の鍵」と読む限り成り立ちません。

**E. 鍵の替えの未払い**
```
$ git log --oneline 780ef711a..61f060009 -- <doeff-linter と doeff-indexer の src・data・Cargo.toml・Cargo.lock・build.rs> | wc -l   → 28
$ git log --oneline 780ef711a..61f060009 -- scripts/hook_finding_baseline/doeff-linter.json | wc -l          → 0
$ git log 780ef711a..61f060009 -- …/doeff004_no_os_environ.rs  → c0b389c42・f6b62abdb(DOEFF004 を広げた)
```

**F. 鍵を替えると、他の規則の所見が M6 に出る(61f060009 の版の写しに、今の B を当てた)**
```
$ B --no-log --no-config --enable DOEFF004 --output-format json *.py   (m6_before)
DOEFF004 error [('test_import_gc.py', 112), ('test_wheel_remote_store.py', 183)]
$ 同じ (m6_now)  → 所見なし
```
4ccab04ac の commit の文では、`test_wheel_remote_store.py` は鍵の替えの前から赤だった 1 行で、鍵の替えで判定に入ったのは `test_import_gc.py` などの方です。

**G. 鍵を替えた commit が触った file**
```
$ git show --stat --format= 4ccab04ac
 program_effects.py | test_dataclass_code_store.py | test_import_gc.py | doeff-linter.json (2 +-) | tests/test_hook_finding_baseline.py | tests/test_wheel_remote_store.py | tools/architecture.hy (16 ++++++++--)
```

**依頼側での再現の手順【推測・未実行 — cargo build と commit が要るため】**
1. 使い捨ての worktree で、別の規則の意味の変更が 1 つ入った後の版を選びます。今の HEAD は未払いが 0 なので、例えば 61f060009 を使います。
2. `is_init_method` に `|| func_name == "__setstate__"` を足し、所見の文と M2 の 2 file を合わせ、build して commit します。
3. 入力 A に `scripts/hook_finding_baseline.py check doeff-linter` を当てます。古い意味で測られ、「測れない」と出ない事を確かめます。
4. `--linter <自分の build> lower doeff-linter` を走らせます。stderr の「今の版で基点より多い組」に DOEFF002 以外の規則の組が出て、M4・M6 の類の file を直さないと赤が消えない事を確かめます。

## 5. 未確認の事・足りない情報

**【実測】した事**
- 上の A・D・E・F・G。
- 次の補足の候補 1〜3 の実測分。

**ほかに見えた候補(主の例ではない)**
1. **「何を構築と見るか」を DOEFF119 も別に持つ。**
   - `project/mod.rs:4564-4568` の集合は `__init__|__post_init__|__new__` で、M1 と既に食い違っています。
   - 【実測】Hy の `(defclass Field [] (defn __set_name__ [self owner name] (setv self._held …)) …)` に DOEFF119 を当てると、「変わる状態を持つ(__set_name__: self._held)」の warning が出ました。DOEFF002 はこれを初期化と見ます。
   - ただし Hy だけの規則で、doeff の pyproject では DOEFF119 は無効です。S1 の前提(Python の代入の形)の外になります。
2. **名の決まりを DOEFF003 も写して持つ。**
   - `doeff003_…rs:38-46` の `is_mutable_attribute` が、M1 の名の決まりを写しています。
   - 【実測】`__init__` でだけ置く `_mutex` を DOEFF003 は可変と数え、6 個で warning を出しました。遅れて作る `_mutex` を DOEFF002 は通しました(`_mut` で始まるため)。
   - 名の決まりを `_mut_` に締める変更は、M1 の契約の変更に加えて、M1〜M6 の外の DOEFF003 の変更が要ります。
3. **責務分離ではないが、S1 の「型に依らず強いる」が前提の形の中で崩れる。**
   - 【実測】次の形の代入は所見 0 でした: `async def` の中、`try … finally` の中、`async with` の中、tuple の左辺(`self._left, self._right = 1, 2`)。
   - M1 は `Stmt::FunctionDef` だけを歩き、Try の orelse/finalbody を歩きません。DOEFF001・004・006 は `AsyncFunctionDef` を扱っています。

**【推測】の事・足りない情報**
- `_Reader` や他の共有 object に、pickle が実際に要るかどうか。
- 次に鍵を替える時に、どれだけの未払いが溜まっているか。時機によります。
- land-arm など、この repo の外で「基点の鍵 = HEAD の鍵」を見張る仕組みがあるか。この repo の scripts と tests を git grep した限りでは、`linter_for_writing` が HEAD の鍵を読むだけで、比べる所はありません。
- 他の repo で DOEFF119 が有効かどうか。読める範囲の外です。
- S2 の「鍵の合わない状態」が「binary が無い」の意味なら、その読みでは主張は成り立ちます(binary が無い時は「測れない」と出る)。
- 読める範囲の外に触れた事: 断面の置き場(`~/.cache/doeff-linter-snapshots`)の dir の名と `input_key` を一覧し、D の模しはその置き場を読みました。古い binary は走らせていません。

model の自己申告: Claude Opus 5.5(claude-opus-5-5)
