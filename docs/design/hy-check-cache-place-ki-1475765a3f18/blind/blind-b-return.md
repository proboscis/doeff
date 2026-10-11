Opus 5.5(claude-opus-5-5)

失敗例を 1 つ作り、両方を同じ写しで実測しました。提示された 5 本の検は全部通り、そのうえで保存から引いた答えが展開し直した答えと違いました(R3 違反)。共有の source と作業木は変えていません。実験は `/tmp/ki1475-blind-B/` の下だけで行いました。

## 1. 機能の要求と差分

**要求(私が置いた仮定):** 所見の規則ごとの重さ(赤か警告か)の定義元を 1 つにしたい。doeff-hy-check と文書の規則の表と editor の拡張が同じ表を読めるよう、表を doeff_hy の中の JSON に置く。ADR-DOE-HY-006 の移行で、module の直下の setv の警告を赤へ上げる時は、表の 1 行だけを替える。

**差分:** `static_check.py` を変え、`static_findings.json` を足します。全文は `/tmp/ki1475-blind-B/variant.diff` です。

```diff
     top_level = scope.findings
+    severities = finding_severities()
     return Projection(
-        tuple(_finding_diagnostic(relative, f) for f in (*found, *top_level)),
+        tuple(_finding_diagnostic(relative, f, severities) for f in (*found, *top_level)),
+FINDING_SEVERITIES = "static_findings.json"
+def finding_severities() -> dict[str, str]:
+    path = Path(__file__).with_name(FINDING_SEVERITIES)
+    if not path.is_file():
+        return {}
+    loaded: object = json.loads(path.read_text(encoding="utf-8"))
+    table = loaded.get("severity") if isinstance(loaded, dict) else None
+    if not isinstance(table, dict):
+        return {}
+    return {str(rule): str(level) for rule, level in table.items() if level in ("error", "warning")}
-def _finding_diagnostic(relative: str, finding: Finding) -> Diagnostic:
+def _finding_diagnostic(relative: str, finding: Finding, severities: dict[str, str]) -> Diagnostic:
-        finding.severity.value,
+        severities.get(finding.rule, finding.severity.value),
```

- 足す表は `{"severity": {"doeff-hy-setv": "warning"}}` です。上げる commit では `"error"` に替えます。
- 禁じられた手(cast・ignore・lint の抑制・動的 import・基点の改変・対象の除外)は使っていません。

## 2. 本来の持ち主と、侵す契約

- **移る判断:** 「この所見を赤にするか」は、今は binding_forms の code(`Finding(RULE_SETV, Severity.WARNING, …)`)が持ち、walker が閉包の digest で覆っています。差分の後は、code でない JSON が決めます。
- **移る知識:** 「保存した展開が何に依ったか」は walker と record の物です。差分の後は、post-processing の `finding_severities` だけが「JSON に依る」ことを知ります。
  - walker は `_LOCATION_NAMES` の規則で、`__file__` を名だけで「置き場を名指すだけ・振る舞いに効かない」と判じ、印に替えます。この code では、`__file__` は振る舞いを決める入力へ辿り着く道です。
- **effect:** post-processing の effect の欄は「source の読み・Hy の compile」だけです。差分は、宣言の無い file の読みを足し、その結果が保存される値(所見の severity)に入ります。
- **侵す契約:**
  - R3
  - walker の不変条件「中身が変われば digest が変わる」
  - record の「module の名で今の環境から引き直す」。JSON は module の名で引けません。保存の entry の記録の files の行は module 名の行だけで、JSON は出ないことを実測しました。
  - store の「今に合う entry だけを返す」
  - S3 の主張(後処理を変えれば記録が古くなる・変更は post-processing だけで済む)
  - S1/S4 の主張「中身が違えば引かない」と、前提 P3

## 3. 各検査が拒否しない理由

実行は `PYTHONPATH` で写しを先に読ませ、pytest を 1 本ずつ名指しました。

| 検査 | 結果 | 拒否しない理由 |
|---|---|---|
| `test_a_macro_naming_the_place_of_its_module_is_current_in_another_tree` | passed(実測) | walker を fixture の macro で試す検。差分は walker も fixture も変えない |
| `test_a_changed_macro_naming_the_place_of_its_module_is_stale_in_another_tree` | passed(実測) | 同上 |
| `test_the_same_doeff_hy_in_another_place_keeps_the_stored_expansion` | passed(実測) | 写した木は JSON も同じ中身で、`__file__` は印になるので引ける。検は「同じ中身なら引く」だけを見て、「code でない中身が違えば引かない」は見ない |
| `test_the_entry_run_as_main_reads_the_store_its_expansion_children_wrote` | passed(実測) | 入口の名(`_POST_PROCESSING_MODULE`)を変えていない |
| `test_a_changed_type_check_post_processing_expands_again` | passed(実測) | 検は static_check.py の code を替えるだけで、data の file を替える形は試さない |
| doeff-linter | error 0 → 0。info の DOEFF022 が 51 → 52(実測) | 基点の鍵と同じ input_key の断面の binary を、写した根で撃った。hook は error だけを数える |
| semgrep | 0 件 → 0 件(実測) | venv の 1.169.0(基点の版と同じ)と `.semgrep.yaml` を、同じ repo 相対の path で .py と .json に撃った |
| pyright | basic で 6 → 6(既存の reportAttributeAccessIssue だけ)、strict で 54 → 59(+5 は reportUnknown*)(実測) | ただし、どの設定がこの file を止めるのかは材料に無い(5 節) |

## 4. 最小の再現手順と結果

**入力:** `/tmp/ki1475-blind-B/demo/proj/probe.hy`(require defk・`(setv limit 3)`・defk 1 つ)と、診断の無い偽の pyright です。

**手順:**
- 写しへ当てる: `/tmp/ki1475-blind-B/scripts/apply_variant.py <写した doeff_hy>`
- 検を撃つ: `/tmp/ki1475-blind-B/scripts/run_one.sh <PYTHONPATH> <名> <node id>`
- 違反を見る: `/tmp/ki1475-blind-B/scripts/drive.sh <PYTHONPATH> <保存先|->`。`python -m` と同じく `__main__` の名で撃ち、保存を引いた数・外れた数と exit を出します。

**結果(すべて実測):**

| 実験 | 木と表 | 保存 | 引いた/外れた | 所見 | exit |
|---|---|---|---|---|---|
| 1 同じ木 | a・warning | cache-1 | 0/1 | setv warning | 0 |
| | a・表を error に替えた | cache-1 | 1/0 | **setv warning** | **0** |
| | a・error | 保存なし | — | setv error | 1 |
| 2 別の置き場・表だけ違う | a・warning | cache-2 | 0/1 | warning | 0 |
| | b・error | cache-2 | 1/0 | **warning** | **0** |
| | b・error | 保存なし | — | error | 1 |
| 対照(D1 の前の 4ccab04ac の `macro_use.py`) | 実験 2 の b | 共有 | 0/1 | error | 1 |
| | 実験 1 の 2 回目 | 同じ | 1/0 | warning | 0 |

R3 が成り立つなら、太字の行は「保存なし」の行と同じになるはずです。

対照の読み:
- 別の木どうしの誤りは D1 で新しく開いた形です(D1 の前は、置き場の path が digest に入って外れていた)。
- 同じ木の中の誤りは、D1 の前から在った形です。

**副次の観察:** 表を関数の中でなく module の直下で読む形(`/tmp/ki1475-blind-B/variant2`)では、`test_the_same_doeff_hy_in_another_place…` が赤になりました(実測)。理由は推測です。`_Walker._bound` の文の loads の道には `_LOCATION_NAMES` が当たらず、path の値が digest に入るのだと思います。つまり、検が拒むかどうかは書き方の形で決まり、拒む時も置き場の理由で拒んでいます。

## 5. 未確認の前提・推測

- **pyright:** 根の `pyrightconfig.json` の include は `doeff`・`packages/doeff-adr/src`・`packages/doeff-adr/tests` だけで、`.pre-commit-config.yaml` に pyright の項はありません(読んで確認)。doeff_hy を止める pyright の設定は分からないので、「材料の意味で pyright を通る」とは断定しません。
- **hook の経路:** semgrep と doeff-linter は、hook の script(`hook_finding_baseline.py`・`uv tool run`)を通して撃っていません。「hook が止めない」は、上の数と基点(この file の基点は 0 件)からの推測です。
- 全数の検と、提示に無い検は撃っていません。
- **要求の現実性:** 表を JSON に置く動機は私の仮定です。日次の段(agora-controllers)で起きる結末も推測です。doeff-min-commit を上げても setv が警告のまま残り、source の変わった file だけが赤になる、という形です。
- **mechanism:** `test_a_changed_type_check…` が通る仕組み(static_check.py の code の変更を何が覆うか)は確かめていません。検が通ることだけを測りました。
- **環境:** Python 3.14.3t、Hy 1.3.1 です。実験の process では、files を `/tmp` の下に留めるため `DOEFF_HY_CODE_STORE`・`PYTHONPYCACHEPREFIX`・`HOME` を `/tmp/ki1475-blind-B` の下へ向けました。
- **制限からの逸れ:** semgrep と pyright の起動で、作業木の `.venv/site-packages` に .pyc が 11 個できました。刻みが私の実行と一致したので、私の物と判じて消し、`git status` と変更日時で作業木が元どおりなのを確かめました。`~/.semgrep` は私の起動の前から在り、更新の時刻も私の semgrep の後なので、触っていません。

WAIT: work — 呼び手の次の依頼
