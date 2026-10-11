## 結論

S1(環境変数のほかに、cwd・argv・HOME など process の状態の読みも照らせない読みと数える)で、主張の前提を守ったまま責務分離が崩れる例が見つかりました。どの読みを数えるかは、W の検出の関数と定数だけでは決まりません。B が束縛を解けるかどうかと、W の「安全側」の逃げ道(file の sha256 で覆うこと)が一緒に決めています。同じ穴は今の版の環境変数にもあり、固定の要件 2 が今すでに破れていることを実測しました。

## 1. 変更要求と理由

- **要求(S1)**: macro が展開の時に読む値が `os.getcwd()`・`sys.argv`・`os.path.expanduser` など process の状態で決まる時、その使い手の .pyc を照らせない(いつも古い)と判じさせる。
- **理由**: B1 の条は「環境変数など file の外で決まる値」が違えば古いと判じることを求めています。cwd と HOME は環境変数と同じ種類の値です。作業木は数十あって cwd が違い、worker の Pod は HOME も違い、共有の置き場(S5)をまたいで使われます。
- **例**: project の根や設定の在り処を cwd から組む表を、macro が展開に埋めるような形です。

## 2. 関係する主張・前提と、予想に反して変わる所

**元の主張(S1)**
- 「何を照らせない読みと数えるか」は W の検出の関数と定数だけが持つ。
- C・R・L・B は変わらない。予想の範囲は W の検出の関数と定数と検。

**前提を守っているか**: 守っています。
- 読みは `places.py` の top-level の束縛の文 `ROOTS = {"root": os.getcwd()}` です。契約が環境変数について数える形(束縛の文に os を大域で引く名と、読む属性の名が並ぶ)と同じです。
- 標準 library の中の読み、文字列で引く読み、`sys.modules` を通す読み、unchecked-hash の .pyc のどれでもありません。
- Hy 1.x、timestamp の .pyc、共有の置き場という利用状況のままです。
- macro の module が表を `(import pkg.places [ROOTS])` で読むのは、Hy の普通の import です(`from … import` と同じ)。
- S1 の変更そのものは契約を広げる要求で、`expanduser` を入れる所は今の契約では範囲外です。この例は S1 が意図する範囲の中にあります。

**予想に反して変わる物**
- W の digest の作り方(歩みの覆い方)のうち、検出ではない所。
  - `_bound` の逃げ道(`/home/ubuntu/.worktrees/doeff-wt-hy-env-design-check/packages/doeff-hy/src/doeff_hy_bytecode_guard/macro_use.py` 1657–1659: unresolved なら `_fold`)。
  - `_state_value` の既定の枝(同じ file の 1618–1621: 既定値・閉包の cell の中の辿れない値を `_fold`)。
- B(`bound_source.py`)。
  - 契約の文(14–15 行「Hy の import の形は unresolved を返し、歩みの側は安全側に file の全体で覆う」)は、どの直し方を選んでも偽になります。
  - 照らせる形を保ったまま直すには、Hy の import を `Binding.imports` に解くように code(741 行の `unresolved=…`)を変える必要があります。
- C・R・L は、探った範囲では変わりません(印の 1 本の通り道で足ります)。

## 3. どの知識がどこへ漏れているか

**漏れている知識**: 「file の sha256 で覆えば値の変化を見落とさない(安全側)」という W の覆い方の前提です。
- W 自身の docstring(48–52 行)に書かれています。
- `_fold` を呼ぶ所が 6 か所以上あります(`_bound`・`_state_value`・`_compiled`・`_lazy_module`・`_imported_binding`・mutator の枝)。
- B の公開の契約にも同じ前提が書かれています(unresolved なら呼び手が file で覆えば安全)。
- 環境変数の規則(と S1)は、file で覆えない依存を足したので、この前提を偽にします。検出の定数を足しても、歩みが逃げ道に落ちた先の読みは見えません。
- そのため、同じ `os.getcwd()` の読みが、Python の import で束縛すれば数えられ(B が解き、W の `_imported_binding` が辿る)、Hy の import で束縛すれば数えられません。違いを決めているのは B の知識で、W の検出ではありません。

**4 つの区別**

| 区別 | この例で当たる物 |
| --- | --- |
| 共同の不変条件 | 「B が unresolved を返す ⇒ W は file で覆い、それで安全」という W と B の共同の不変条件が、file の外の依存で破れる。これが核心 |
| 隠すはずの知識の漏洩 | 上の前提が、W の中で 1 か所に集まらず逃げ道ごとに散っており、しかも B の契約に書かれている |
| 配線の変更 | 既定値・cell の形を照らせる形で直すには、関数で束縛した大域についても W が B(`binding_of`)を引く必要がある |
| 意図した公開契約の拡張 | B が Hy の import を解くことは、既存の欄 `Binding.imports` の範囲の拡張。ただし計画した拡張ではなく、隠れた前提に押されて要る変更 |

印(R の `ENVIRONMENT_READ`、C の古い判じ)は公開の契約で、漏洩ではありません。

**W だけで閉じる道もあります**: unresolved や逃げ道に落ちた値を印にする道です。ただしこれは検出ではなく覆い方の方針の変更で、環境変数と無関係な Hy の import の表を使う使い手まで、process ごとに展開し直させます。

## 4. 最小例・再現手順・観測

**材料**: `/tmp/blind-A/s1probe/run.py` と `/tmp/blind-A/s1probe/child.py` に、次の小さな package を置きました。
- `tables.py`
  ```python
  import os
  ROWS = {"a": os.environ.get("PROBE_MODE", "plain")}
  ROOTS = {"root": os.getcwd()}
  ```
- `m_name.hy`
  ```hy
  (import probe_s1.tables [ROWS ROOTS])
  (defmacro env-name [] (get ROWS "a"))
  (defmacro cwd-name [] (get ROOTS "root"))
  ```
- 比べる対照: `m_module.hy`(`tables.ROWS` を module の属性で読む)と、`bridge.py`(Python の `from … import`)を通す `m_bridge.hy`。
- 使い手は `(require …)(setv value (macro))` の形です。
- S1 の模し方: 子の process で `macro_use._ENVIRONMENT_NAMES |= {"getcwd"}` とします。主張どおり W の定数だけを変えた形です。

**手順**: `cd /home/ubuntu/.worktrees/doeff-wt-hy-env-design-check && PYTHONPYCACHEPREFIX=/tmp/blind-A/pycache DOEFF_HY_CODE_STORE=/tmp/blind-A/store uv run --frozen python /tmp/blind-A/s1probe/run.py` を走らせます。この中で 3 つの process を順に走らせます。
1. cwd1・環境変数なし
2. 同じ環境でもう 1 度
3. cwd2・`PROBE_MODE=fancy`

**観測(実測)**

| 使い手 | 今の版(模しなし) | S1 を模した版 |
| --- | --- | --- |
| u_env_mod | 3 つ目で compile し直し `fancy`(正しい) | 同じ |
| u_env_bridge | 3 つ目で compile し直し `fancy`(正しい) | 同じ |
| **u_env_name** | **compile しない・`plain` のまま(要件 2 違反)** | 同じ |
| u_cwd_mod | 未対応(S1 を入れていないので想定どおり) | cwd2 で展開し直す(正しい) |
| u_cwd_bridge | 未対応(想定どおり) | cwd2 で展開し直す(正しい) |
| **u_cwd_name** | 未対応(想定どおり) | **cwd1 のまま(S1 の要求を満たさない)** |

**原因の実測** (`/tmp/blind-A/s1probe/trace.py`、`trace_s1.py`)
- cwd-name と env-name の digest は 16 進の sha256 で、印になりません。
- 唯一の `_fold` は `_bound` から `probe_s1.m_name` に対して起きています。
- unresolved の束縛は `m_name.hy:ROWS` と `m_name.hy:ROOTS` です。
- cwd-mod と cwd-bridge は印になります。

**同じ種類の穴(今の版・実測)** (`/tmp/blind-A/s1probe/run_fallbacks.py`)
- 既定値に環境変数で組んだ表を入れた形 `(defn helper-default [[rows {"a" (os.environ.get …)}]] …)` を読む使い手。
- 閉包の cell に入れた形 `(setv reader (make))` を読む使い手。
- どちらも 2 つ目の process(`PROBE_MODE=fancy`)で compile されず、`plain` のままでした。

## 5. 未確認の前提・不足する情報

**実測したこと**
- 上の表と原因の確認は、すべて probe の結果です。
- 要件 3 への影響: 実物の doeff_hy の macro 6 つ(defk・`<-`・val・deff・defrecord・defhandler)の歩みは、`_bound` の unresolved にも `_state_value` の逃げ道にも当たりませんでした。
  - defk に出た `_module→doeff_hy.outcome_forms` は、入れ子の import の照らしが混ざった物です。
  - したがって、W だけで閉じる道(逃げ道を印にする)は、この 6 つの使い手を照らせなくはしません。

**推測・未確認のこと**
- 他の実物の macro(agora の controllers の macro の module など)に、Hy の import で束縛した表や既定値・cell の表があるかは調べていません。全数の検査をしないためです。あれば W だけの道は要件 3 に反し、B の変更が必須になります。
- S1 を印 1 つで入れる前提を置いています。別の印(例 "process-read")を足すと、C の判じ(`macro_use.py` 268 行の `ENVIRONMENT_READ in (recorded, now.digest)`)は等しさで比べるので、新しい印どうしを SAME と判じます。その場合 R と C も変わります。これは code を読んだ推測で、走らせていません。
- S3: 前の版の `_row_check` が、印の行と、今の歩みの import の途中(None)の組を file の行で比べて「合う」と判じうるかは、git を使えないので確かめていません(推測)。
- `record_fingerprint` は印の記録では環境に依らず同じ指紋になります。その利用者(static_cache・analyzer)が使う前に照らし直すかは、読む範囲の外なので不明です。
- `hy.I.os.environ` の形は、code を読む限り検出されません。契約の「module の表を通す読み」に入るかが曖昧で、試していません。

**補足(S2 の主張について・実測)**
- S2 は「`_bound` だけが変わる」と主張しています。しかし印を立てる所は形ごとに違いました(`/tmp/blind-A/s1probe/who_marks.py`)。

  | 表の形 | 印を立てた所 |
  | --- | --- |
  | table | `_bound` |
  | aliased | `_state_value` |
  | got | `function` の名だけの枝 |
  | built | `function` |

- 表 BUILT と、展開の時の読み `mode-now` の両方を使う macro `both` では、`mode-now` の 2 度目の訪れが `_first_visit` で畳まれます。印は「bound BUILT の下」の 1 回だけでした。
- そのため `_bound` だけを値で照らす形に変えると、展開の時の読みを見落とします。これは W の中ですが、`_bound` の外に広がります。

probe の file はすべて `/tmp/blind-A/s1probe/` の下と、指定どおりの `/tmp/blind-A/pycache`・`/tmp/blind-A/store` にだけ置きました。木の file は変えていません。
