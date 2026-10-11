# 事前の主張(盲検 A・B の前に保存 — 2026-10-11 13:46 UTC)

対象: doeff main ce9c19488dab4e03ef07f4055702a6b3df8f23af(L2053)の packages/doeff-hy/src/doeff_hy_bytecode_guard/。
依頼 lt-W2MTRW7402AP7E8DPEXQ2P7F5Y・card ki-38508e6e56fd。完了の範囲 = 実装完了まで(main に着地済み)。

## 固定した要件

1. 不変条件 B1(packages/doeff-hy/architecture.hy): Hy の module の .pyc の記録の digest が依った物 — macro を定義した file の中身・
   macro が辿る値の module の file の中身・環境変数など file の外で決まる値 — のどれかが記録を作った時と違えば、照らし
   (macro_use.record_is_current)は古いと判じ、展開し直させる。process をまたいでも同じ。
2. 新しい process の照らしは、前の process が読んだのと同じ中身の source を解き直さない(費用 — bound_source.binding_of)。
3. 受入の条(card): c8a812c6d 失敗ケースが素朴な形で赤・直した後に緑 / c44cacf91 照らしの持ち主の見立てが card に在る /
   c40d0dda5 doeff-hy の bytecode の検が緑で agora-controllers の doeff-min-commit を上げた。
   (性能の条 cefc8ccca は配り役が子 card ki-be596ac8276f へ移した — この検証の要件に入れない。)

## 責務(module)と公開契約

| id | 責務 | 持つ知識 | 隠す知識 | 公開の口 |
|---|---|---|---|---|
| macro_use | 記録の digest の作り方と照らし方(閉包の歩み)。process の中の digest の覚え(_REMEMBERED — 同じ関数 object と、辿った file の状態が同じ時だけ使う)。照らし 1 回の中の stat の覚え(_CHECKED_STATES — record_is_current の間だけ) | 何を digest に入れるか・import の途中の状態の扱い | 歩みの内部の順と覚えの形 | record_is_current(record, hy_version, sha256_of)・macro_digest・value_digest・MacroRecording |
| bound_source | 辿れない大域を覆う source の文の読み(純: 中身・名・読み方で決まる)。process の中の覚え(_SOURCES・_PARSED・_BINDINGS)と、置き場の .binding の entry の鍵・形 | 読み方(Python の ast・Hy の読み)・entry の形(BindingParts)・鍵の欄 | 置き場の並び・書き方 | binding_of(path, name) -> Binding | None |
| code_store | 置き場の dir・並び・鍵の綴り(content_key)・entry の読み書き・時刻・壊れた entry の扱い | 置き場の在処(DOEFF_HY_CODE_STORE)・一時の file から置き換える書き | entry の中身の意味 | store_dir・store_is_named・content_key・entry_path・read_entry・write_entry・discard_entry・種類の末尾(CODE_SUFFIX・IMPORTS_SUFFIX・BINDING_SUFFIX) |
| loader_hooks | import の口の包み(.pyc・保存先の code・compile)。記録の照らしを呼ぶ | いつ照らすか・いつ compile し直すか | digest の作り方 | install・record_is_current_here・file_sha256 ほか |
| records | 記録の形(code の定数・JSON) | RECORD_TAG・行の形 | 照らし方 | MacroRecord ほか・record_of・with_record |

決め: digest そのものは process をまたいで覚えない(生きた値 — 別の module から来た素の値・環境変数で決まる値 — に依るので、
file の状態だけでは信じられない)。process をまたいで覚えるのは bound_source の純な読みだけ。鍵 = 印 BINDING_TAG・
bound_source.py の中身の sha256・sys.version の全部・拡張子の種類(py / hy / other)・Hy の source なら Hy の版と hy.repr の登録の
表の指紋(Hy 自身と組み込みの外の関数が登録されていれば置き場を使わない)・名・source の中身。読めない file(OSError)は覚えず、
解けない source の None は覚える。

## 変更シナリオと事前の主張

| id | 軸 | 変える要求 | 主張 | 予想の波及 |
|---|---|---|---|---|
| S-hw | hardware | 別の CPU・OS の機体で同じ置き場を使う | 対象外: 読みは純 Python の ast・Hy の読みで機体に依らない。置き場は機体ごと(/work/state)。Python の build の違いは sys.version が鍵で分ける | — |
| S-storage | storage | 置き場の在処や並び(dir の分け方・掃除の作法)を替える | code_store だけが変わる。bound_source・macro_use は変わらない(並び・書き方を知らない) | code_store |
| S-effects | effects | 置き場に書けない(読み取り専用・容量切れ)・source が読めない | 結果(束縛・digest・照らしの判じ)は変わらず、速さだけが落ちる。code_store の書きの失敗は bound_source の答えに出ない | なし(振る舞いの変更なし) |
| S-dist | distribution | 作業木・版・機体をまたいで置き場を共有する(worker の root の入れ替え・hardlink の引き継ぎ) | 同じ中身の source は当たり、違う中身・違う読み方の code では当たらない。digest は覚えないので、別の木の macro・別の module の値・環境変数の違いは毎 process 辿り直して古いと判じる(B1) | なし |
| S-conc | concurrency | 同じ置き場に複数の process・thread が同時に読み書きする | 書きは一時の file から置き換え(code_store)なので半分の entry は読まれない。壊れた entry は名指して除き解き直す。照らしの stat の覚えは ContextVar で照らし 1 回・thread ごと | code_store の作法のまま |
| S-sim | simulation | doeff の模擬(仮想の時計)の下で動かす | 対象外: この層は Python の起動の時点(.pth)で入り Program の外(architecture.hy の startup の層)。時刻を読まない(置き場の時刻は掃除のためだけ) | — |

前提: Hy 1.3.1 の hy.read_many は毎回新しい reader(use_current_readers=False)。hy.repr は process の共有の登録の表を引く。
source の bytes を読めた時の読みは path に依らない(path は拡張子と誤りの文だけ)。
