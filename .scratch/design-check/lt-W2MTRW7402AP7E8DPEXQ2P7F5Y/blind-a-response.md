**結論:** 責務分離の失敗例を 1 つ見つけ、実際に動かして再現しました。Hy の source の拡張子を 1 つ足す変更です(例として `.hym` を使います)。doeff_hy の登録と、宣言された定義点 `code_store.HY_SOURCE_SUFFIXES` だけを直すと、`macro_use` の中にある拡張子の写しが古いまま残ります。その結果、不変条件 B1 が何の知らせもなく破れます。macro の file が変わっても照らしは「今に合う」と判じ、`user.value` は 3 のままになります(正しくは 30)。

## 1. 変更要求と、それが要る理由
- **要求:** doeff-hy に Hy の source の拡張子をもう 1 つ足す(例: macro を定義する module 用の `.hym`)。
- **理由:**
  - `.hyk`(kleisli)と `.hyp`(program)を後から足した前例があります(commit 7209384fe)。
  - `doeff_hy/macros.hy` は拡張子ごとに使える macro を強制しています。つまり module の役割を拡張子で分ける作法で、役割を 1 つ増やすのは同じ軸の延長です。
- `.hym` という名前と用途は私の仮定です(推測)。

## 2. 関係する主張・前提と、予想に反して変わるもの
- **前提は守っています。**
  - Hy 1.3.1・`hy.read_many`・`hy.repr`・Python・置き場・機体は変えません。
  - 前提「source の読みは path に依らない(path で使うのは拡張子と誤りの文だけ)」の内側で、拡張子の集合が 1 つ増えるだけです。契約の適用範囲内と判断します。
  - ただし、事前の 6 軸(S-hw〜S-sim)のどれにも当たらない機能追加です。
- **関係する主張:**
  - B1: macro を定義した file の中身が違えば、照らしは古いと判じる。
  - 責務表: macro_use は「何を digest に入れるか」、bound_source は「読み方」、records は「記録の形」を持つ。拡張子の集合はどの行にも書かれていません。
  - コード側の宣言: `loader_hooks.py:29-31` に「定義点は保存先の module」とあり、定義点は `code_store.py:51` の `HY_SOURCE_SUFFIXES` です。
- **宣言どおりなら予想される波及:** `doeff_hy/__init__.py` の登録 2 か所と、`code_store.HY_SOURCE_SUFFIXES` だけ。
- **実際に追加で要る変更:**
  - `macro_use.py:1557` `_python_source` の literal `(".py", ".hy", ".hyk", ".hyp", ".pyc")` — 抜けると B1 が破れます。
  - `bound_source.py:216` `_HY_SUFFIXES` — 抜けても遅くなるだけです(file 全体で覆う安全側)。
  - `records.py:510` `_SOURCE_SUFFIXES` — 抜けると `macro_dependencies` の答えからその file が落ちます。

## 3. どの知識がどこへ漏れているか
| 変更箇所 | 区別 | 根拠 |
|---|---|---|
| `doeff_hy/__init__.py` の登録 | 配線の変更 | import の仕組みに拡張子を教える所 |
| `code_store.HY_SOURCE_SUFFIXES` | 共同の不変条件(要る写し) | guard は doeff_hy を import できない(`architecture.hy` の `:forbid-modules`)ので、両方を揃える必要がある |
| `macro_use._python_source` | 隠すはずの知識の漏れ。B1 に効く | 「どの file が source か」は import の口の知識。macro_use は「source の無い class は公開の形だけで覆う」(#3938)という判断に、この知識を自前の literal で持っている。知らない拡張子は拡張の module 扱い、つまり危険側に倒れる(同じ module の docstring にある「安全側に倒す」と逆向き) |
| `bound_source._HY_SUFFIXES` | 漏れ(重複だが安全側) | 集合そのものは定義点から引ける(bound_source は code_store を既に遅延 import している) |
| `records._SOURCE_SUFFIXES` | 漏れ(別の責務の重複) | 「記録の形」の module が、file 単位の依存の辿り(`macro_provider_files`)と拡張子の集合も持っている |

公開契約の拡張は要りません。`record_is_current`・`binding_of`・`macro_dependencies` の形は変わりません。

## 4. 最小の入力と再現手順
- **試しの file:** `/tmp/blind-a-sep/probe.py`・`/tmp/blind-a-sep/run.sh`
- **命令:** `sh /tmp/blind-a-sep/run.sh <変種>`
- **作る package:**
  - `macros.hym`:
    ```
    (defclass Scaler []
      (defn factor [self] 3))
    (setv TABLE {"a" 1})
    (defmacro scaled [] (.factor (Scaler)))
    (defmacro looks [] (get TABLE "a"))
    ```
  - `user.hy` は `scaled` を require して `(setv value (scaled))` とするだけ。
- **手順:** 各段を別の process で走らせます(置き場と pycache は `/tmp/blind-a-sep/runs/<変種>/` の下)。
  1. setup: package を書く。
  2. first: import して、記録の付いた .pyc を書く。
  3. edit: `(defn factor [self] 3)` を `30` に替える。
  4. second: 新しい process で .pyc の記録を照らし、import し直す。
- **変更の作り方:** source を編集する代わりに、`import doeff_hy` の後で名を置き換えて作りました。
  - doeff_hy の登録 2 か所と同じ処理を `.hym` について行う。
  - `code_store.HY_SOURCE_SUFFIXES` と `loader_hooks.HY_SOURCE_SUFFIXES` の両方を置き換える(loader_hooks は名を import の時に写すため)。
  - 他の写しは変種ごとに足す。
- **結果(実測):**

| 変種 | 足した写し | second の照らし | user.value | scaled の digest(前→後) | macro_dependencies | binding_of TABLE |
|---|---|---|---|---|---|---|
| hy(対照) | — | False | 30 | 774c995b487c→f744158cbd47 | [macros.hy] | 文 2 つ |
| hym-defpoint | 登録 + code_store | **True** | **3** | 437dc7f45958→437dc7f45958 | [] | None |
| hym-bs-rc | + bound_source + records | **True** | **3** | 437d…→437d…(同じ) | [macros.hym] | 文 2 つ |
| hym-mu | macro_use だけ | False | 30 | 774c…→f744… | [] | None |
| hym-full | 全部 | False | 30 | 774c…→f744…(対照と同じ) | [macros.hym] | 文 2 つ |

- **見るべき結果:** hym-defpoint では、macro を定義した file が変わったのに記録が今に合うと判じられ、古い展開(3)が使われます。
- **原因:** `macro_use._class` の中で `_python_source(home)` が `.hym` に対して偽になります。そのため `_extension_class` を通り、`factor` は `("method",)` としか入らず、本体の定数(3 か 30)が digest に入りません。
- B1 を左右するのは macro_use の写しだけだと、hym-bs-rc と hym-mu の比較で切り分けました。

## 5. 未確認の前提・不足する情報
- **推測:**
  - 新しい拡張子を足す要求が実際に来るか(前例からの類推)。
  - 実装者が定義点だけを直すか。macro_use の写しは名前の付いた定数ではなく関数の中の literal なので、`HY_SOURCE_SUFFIXES` や `_HY_SUFFIXES` で grep しても出ません。`.hyk` で grep すれば出ます。
- **未確認:**
  - records の抜けが doeff-adr の item_cache の古い当たりにまでつながるか。`macro_dependencies` の答えから file が落ちる所までは実測しましたが、item_cache 側は読んでも走らせてもいません。
  - source を実際に編集した場合の実行。今回は名の置き換えで代えています。
  - 既存の pytest・pyright・semgrep は走らせていません。semgrep の設定を grep した限りでは、拡張子の写し同士を結ぶ規則はありませんでした。検は `.hy` だけを使い、拡張の class の検は `__file__` を `.so` に向けた形だけです。
  - この木の外にも拡張子の写しがあるかは見ていません(doeff-cluster の worker の道具・agora-controllers)。
- **環境:** Python 3.14.3 の free-threading build と Hy 1.3.1 で実測しました。doeff の木は変えておらず(`git status` は空、新しい file も無し)、試しの file は `/tmp/blind-a-sep` の下だけです。
- **別の候補(再現していません):** 「書かない設定では名指しの置き場だけに足す」という書きの方針(`sys.dont_write_bytecode and not code_store.store_is_named()`)が、`loader_hooks.py:456`・`dataclass_code.py:64`・`bound_source.py:340` の 3 か所にあります。この方針を変える要求は、S-storage の「bound_source は書き方を知らない」に反して bound_source の変更を要します。ただし結果への影響は速さだけで、コードを読んだだけです。

WAIT: work — 呼び手の次の指示
