## 盲検 B の結果: 4 つの検査を全部通る責務違反の実装を 1 つ作れた

違反を入れた写しの木で K1〜K4 をそのまま実行し、全部通りました。そのうえで、設計が主張する S3(実行時の `do` の出所を M1 だけで替える)と S1(defmcp-tool に文の guard を足す)が、この違反の下では成り立たないことを測りました。読ませる版の作業木は変えていません。HEAD は d4c8d7ce9 のままです。

### 1. 機能の要求と差分

**要求**(実装の版への後追いの掃除・レビューの指摘として出そうな物): 「defmcp-tool の実行時の展開に、使わない import が 2 行増えた(`EffectBase` と guard の補助 4 つ)。handler が名で読むのは decorator の `_doeff_do` だけ。class の本体に書いた tool では、class に使わない属性が生える。実行時は `_doeff_do` だけの import に戻したい。型検査のための展開は今のまま(module の頭の `_doeff_do` を読む)。」

**差分**(写しの木の `packages/doeff-hy/src/doeff_hy/macros.hy`、defmcp-tool の中):
```diff
+  ;; handler が名で読む補助は decorator の `_doeff_do` だけ(文の guard も最後の値の guard も使わない)。…
   (_scoped-definition _hy-compiler name [
-     (_helper-imports)
+     (if (_static-view?) '(do) '(import doeff.do [do :as _doeff_do]))
      '(import doeff.mcp [McpToolDef McpParamSchema])
```

### 2. 本来の持ち主と、破る契約

- **M1 の知識が M3 に写る**: 補助の出所の module(`doeff.do`)と別名 `_doeff_do` は、M1 の表 `_helper-imports` が定義の macro から隠す知識です。「型検査のための展開では何も出さない」という決めも M1 が持ちます。差分では、この両方が M3(defmcp-tool の本体)に 2 つ目の定義元として写ります。
- **M3 が判断を持つ**: 「どの補助が要るか(guard を使わないか)」の判断も M3 が持つことになります。
- **破る契約**:
  - M3 の不変条件「補助の名を自分で import しない — 補助の import は M1 を通す」
  - 公開の契約「定義の macro の展開が名で読む補助は、実行時は M1 が import し」
  - 変更シナリオの主張 S3「M1 の 1 か所で替わり、defmcp-tool に個別の編集は要らない」
  - 変更シナリオの主張 S1「補助の import に触れずに済む」

### 3. 各検査が拒否しない理由

| 検査 | 拒否しない理由 |
|---|---|
| K1 | 型検査のための展開だけを読む。違反は `_static-view?` の枝で `'(do)` を出すので、型検査の展開の文字列は実装の版と同じになる(実測で一致)。`_` で始まる別名の import は頭だけ、`_doeff_do` は 1 つ。 |
| K2 | 型検査の展開が同じなので、pyright に掛かる入力も同じ。 |
| K3 | 実行時の handler の本体は `_doeff_do` 以外の補助を読まない(展開を見て確認。`<-` は `__import__('doeff_core_effects.outcomes')` で引く)。McpToolDef の値・schema・run の答えは変わらない。 |
| K4 | 関数の本体の中は `_scoped-definition` が包んだままなので、外の関数の局所の名は作らない。class の本体の検 `(abs 1) text` は guard を使わないので、`_doeff_do` だけで走る。 |

### 4. 手順と結果(すべて実測)

1. 写しの木を作る: `git -C <読ませる版> archive HEAD | tar -x -C /tmp/ln19-blind-b/tree` の後 `make sync`(13 秒)。上の差分を当て、展開の出力で差分が効いていることを確かめた。
2. 違反の木で、提示された command のまま K1〜K4 を実行した。

| 検査 | 結果 |
|---|---|
| K1 | 1 passed |
| K2 | False・True とも passed(pyright は skip されず走った) |
| K3 | 8 passed |
| K4 | 32 passed |

3. **S3 の模擬**(`s3_follow.py`): 1 つの process の中で M1 の `_helper_imports` だけを差し替え、`do` を `ln19_alt_do`(通った関数の名を記録する包み)から取るようにした。module・class・関数の本体に tool を 1 つずつ置いて比べた。

| | defk | tool(3 か所) | run の答え |
|---|---|---|---|
| 実装の版 | 新しい `do` を通る | 3 か所とも通る | hi |
| 違反の版 | 新しい `do` を通る | 3 か所とも通らない(旧い `doeff.do` のまま) | hi |

   違反の版でも run の答えは変わらないので、取り残しは動きの検には出ません(黙って起きる)。

4. **S1 の適用**(`apply_s1.py`): 設計の主張どおり、M3 の本体の組み立てだけを替えた(`_wrap-statement-guard` の wrap と `(_install-guard-globals handler)` の呼び)。
   - 実装の版 + S1: guard が効く(文の位置に捨てた `(Pure 1)` で RuntimeError)。K1/K2 は 3 passed、K3 は 8 passed、K4 は 32 passed。
   - 違反の版 + S1: `NameError: name '_install_guard_globals' is not defined`。K3 は 7 failed、K4 は 1 failed(`test_class_body_definition_runs_without_module_definitions[defmcp-tool]`)。
   - K4 の残りが通ったのは、検の header の module の段の defk が補助を module の globals に入れるためと読んだ(header の中身からの推論)。違反の下では、defmcp-tool が動くかどうかが同じ module の先の defk の有無に依ります。

### 5. 測っていない前提・足りない情報

- **推測**: 要求の動機は、設計本文の「実行時の展開の import が 2 行増える」と展開の実測から作った物です。実際にそういう要求が出たかは知りません。class に属性が生えることは展開の文字列で見ただけで、`vars(cls)` は数えていません。
- **推測**: S3 は `_helper_imports` を差し替えた模擬で、`doeff.do` を実際に動かす変更ではありません。
- **未測**: 同じ類いの違反として、decorator に fnk 用の `_do-reference` を使う形も考えられますが、試していません。
- **未測**: 提示されていない検査(全体の pytest・semgrep・linter・M6 の検など)は走らせていません。それらが違反を止めるかは分かりません。
- **違反例とは別の実測**: 無変更の実装の版で、型検査のための展開の `from doeff.mcp import McpToolDef, McpParamSchema` は tool ごとに module の直下に並びます(畳まれない)。`(McpToolDef … :bogus 5)` を置いた検体で、tool 3 個では reportCallIssue が 1 件、70 個では 0 件でした。
  - 原因が「宣言が 64 を超えて McpToolDef が Unknown になる」ことだというのは推測です。reveal_type の出力は JSON に出ず、確認できていません。
  - K2 は defk の呼び(`_doeff_do`)だけを見るので、この種の取り残しは検査の範囲の外です。

### 作った実験の file(すべて `/tmp/ln19-blind-b/` の下)

- `violation.diff`・`macros.orig.hy`・`macros.violation.hy`・`macros.s1.hy`・`macros.violation_s1.hy`・`apply_s1.py`
- `tree/`(写しの木。今は違反の版の macros.hy が入っている)
- `k1-k4.violation.out.txt`・`expansion.violation.out.txt`・`show_expansion.py`・`show_bind.py`
- `s3_follow.py`・`s3/ln19_alt_do.py`・`s3_follow.original.out.txt`・`s3_follow.violation.out.txt`
- `s1_guard_probe.py`・`s1_on_both.out.txt`
- `gen70.py`・`probe70/n3/tools.hy`・`probe70/n70/tools.hy`・`probe70/mcptooldef_probe.out.txt`

WAIT: work — 依頼した会話の次の依頼
