<!-- 盲検 B の返答(未加工・Agent tool が付けた行頭の 2 字の字下げと、返し手の枠の行だけを外した)。agentId a0947cbdcbe6de3e8 -->
## 盲検 B の失敗例: MCP の引数の検めを server に足すと、型の写しの表が 2 つになる

対象は d8db309 です。読む木は変えていません(終わりに `git status --short` が空、HEAD は d8db309 のまま)。試した作業はすべて `/tmp/ki973b-blind-b/` の下で行いました。

### 1. 機能要求と最小差分

**要求**: agent が必須の引数を省いたり、型の違う値を送ったりすると、handler の奥で None や型の誤りで落ちます。agent に返るのは分かりにくい誤りです。実測では、symbol を省くと `unsupported operand type(s) for +: 'NoneType' and 'str'` が返りました。server の側で schema に照らして値を検め、分かる誤りを返すようにしたい、という要求です。

**差分**: `/tmp/ki973b-blind-b/patch/b-validator.diff`(写しに `patch -p1` で当たります)。
```python
# doeff/mcp.py
#: JSON Schema の型 → server が受ける引数の値の Python の型(docs/12-mcp-tools.md の表と同じ写し)。
_JSON_TYPES: dict[str, type] = {"string": str, "integer": int, "number": float,
                                "boolean": bool, "array": list, "object": dict}
class McpParamSchema:
    def problem(self, value: object) -> str | None:
        if value is None:
            return f"missing required argument: {self.name}" if self.required else None
        expected = _JSON_TYPES[self.type]
        if not isinstance(value, expected):
            return f"argument {self.name}: expected {self.type}, got {type(value).__name__}"
        if self.enum is not None and value not in self.enum:
            return f"argument {self.name}: expected one of {list(self.enum)}"
        return None
class McpToolDef:
    def argument_problems(self, arguments: Mapping[str, object]) -> list[str]: ...  # 各 param の problem を集める
```
```hy
;; mcp_server_loop.hy の _tool-result-with-stack(unknown tool の検めの直後)
(setv problems (.argument-problems tool req.arguments))
(when problems
  (return #("tool" False (.join "; " problems))))
```
doeff の core は jsonschema に依存していないので、表を手で書くのは自然な選び方です(pyproject を読んで確かめました。jsonschema に依存しているのは doeff-agents だけです)。

### 2. 本来の所有者と、侵す契約

- **所有者**: 「JSON Schema の型 → Python の型」の写しの持ち主は M6(`_mcp-handler-param` の match)です。差分は同じ写しを `doeff/mcp.py` の `_JSON_TYPES` にもう 1 つ書き、M8 がその表で値を断ります。定義元が 2 つになります。
- **移る判断**: 「JSON の number が handler にどの型で届くか」の判断が M6 の外へ出ます。
  - M6 での `float` は文字の注記です。pyright は int を float に代入できるので、型検査の上では int も受けます。
  - 写した表では `isinstance(v, float)` になり、int を断ります。同じ語が別の判断になります。
  - "integer" はその逆で、表は `true` を通します(`isinstance(True, int)` が真のため)。
- **侵す契約**:
  - M8 の不変条件「引数の値は JSON を解いた値」が、M6 を通らない表によって変わります。M6 の docstring は今も「server はそのまま渡す」と書いています。
  - S3 の主張「変わるのは M6 だけで、M8 は変わらない」が崩れます。M6 に型を足すと、`_JSON_TYPES` も直さない限り M8 が KeyError を出します。
  - JSON Schema の公開の意味からも外れます。number は整数の値を含み、integer は true を含みません。

### 3. 提示された各検が拒否しない理由(どれも差分を当てた後に実測で通りました)

| 検 | 結果 | 拒否しない理由 |
|---|---|---|
| 表の検(M9) | 47 passed | 見るのは 8 macro の展開と、その展開が `do` をどこから引くかだけです。差分は macro の展開を変えず、`_tool-result-with-stack` は一覧に入っていません。 |
| 注記の型検査の検 | 2 passed | 型検査のための展開の注記が型として食い違うかを見ます。差分は展開も、McpToolDef の組み立ての signature も変えません。 |
| MCP の macro の検 | 10 passed | 注記の文字・SyntaxError・McpToolDef の組み立てを見ます。handler は直に呼ぶ(`tool.handler("hello")`)ので M8 を通らず、検めは一度も走りません。 |
| `doeff-hy-check --strict` | `order_tool.hy` も `typed_tool_sample.hy` も赤 0 件(差分の前も後も) | 型の上では float が int を含むので、矛盾が見えません。 |

### 4. 最小の手順と期待(どれも実測)

1. 読む木を `/tmp/ki973b-blind-b/tree` へ `cp -a` で写します。写しの `.git` は読む木の管理 dir を指すので、`../tree.git-pointer.txt` へ退かします。
2. 検体 `/tmp/ki973b-blind-b/samples/order_tool.hy` を置きます。number の price と integer の qty を持つ tool で、本体は `(notional (float price) qty)` です。
3. 上の 3 つの検と doeff-hy-check を、差分の前と後で走らせます。どちらも全部緑です。
4. `uv run python /tmp/ki973b-blind-b/exp/run_tool_via_m8.py /tmp/ki973b-blind-b/samples order_tool limit_order '<JSON>'` で、本物の `_tool-result-with-stack` を 1 回呼びます。

| 引数 | 差分の前 | 差分の後 |
|---|---|---|
| `"price": 1500`(正しい JSON の number) | `('tool', True, '7203: 150000.0')` | `('tool', False, 'argument price: expected number, got int')` — 退行 |
| `"qty": true` | `('tool', True, '7203: 1500.0')`(price は 1500) | `('tool', True, '7203: 1500.0')`(price は 1500.0)— 型の違う値を通す |
| symbol を省く | `NoneType + str` の誤り | `missing required argument: symbol` — 要求そのものは満たす |

5. S3 の代わりの確かめとして `exp/new_type_via_m8.py` を走らせました。M6 に "null" を足した後に展開が作るのと同じ McpToolDef を Python で組んでいます。`{"cursor": "abc"}` を送ると KeyError('null') が tool の誤りとして返らずに外へ抜けます。

### 5. 未確認・推測・不足する情報

- **推測**: 本物の HTTP と VM の loop(`run-tool-with-stack`)は通していません。`_tool-result-with-stack` を `full-stack []` で直に呼んだだけです。
  - 検めはこの関数の中に在るので、結果は同じになると見ています。
  - KeyError が抜けた時に HTTP の thread が `TOOL_RESPONSE_TIMEOUT`(125 秒)まで待つかどうかは測っていません。
- **推測**: agent が number の引数に整数の値(1500)を送る頻度は分かりません。
- **前提**: 実装者が jsonschema で `input_schema()` を検めた場合は、2 つ目の表は生まれません。この失敗例は、手で書いた表を前提にしています。
- **未確認**: `scripts/run_changed_tests.py` が mcp.py と mcp_server_loop.hy の変更から選ぶ他の検は、提示外なので走らせていません。
- **実測(差分とは別の、今の版に在る事実)**:
  - (a) 提示の静的な検の検体そのもの(`_typed_mcp_tool(False)`)は strict で赤 0 件です。ところが M8 に `"n": 2` を通すと、`take: pre-condition type error: n expected float, got int` で落ちます。
  - (b) M8 は省かれた**必須の**引数にも None を渡します。そのため、`str` と注記された引数に None が届きます。
  - (c) `:default` を `:required` または `:enum` と同じ引数に書くと、定義の時に TypeError で落ちます。strict でも `reportCallIssue: Parameter "name" is already assigned` が出ます。これは `_parse-mcp-params` の `~@(if has-required [`(:required …)] [])` が原因です。また docs/12 は `:default` を「省かれた時の既定値」と書いていますが、M8 は既定値を当てません。
- **読む木への副作用**: 写しの `.git` を退かす前に、写しの中で `git status` を 1 回走らせました。そのため、読む木の worktree の index の stat の記録(`/work/toolset/repos/doeff/.git/worktrees/doeff-blind-ki973b-d8db309/index`)が書き換わりました。中身は変わっていません。

**作った file**(すべて `/tmp/ki973b-blind-b/` の下):
- 差分: `patch/b-validator.diff`
- 検体: `samples/order_tool.hy`、`samples/typed_tool_sample.hy`、`samples/default_tool.hy`
- 実験: `exp/run_tool_via_m8.py`、`exp/new_type_via_m8.py`、`exp/combo_runtime.py`、`exp/combo_req_default.hy`、`exp/combo_default_only.hy`、`exp/combo_enum_default.hy`
- 写しの木: `tree/`
