# service の宣言を値と関数で書く — 盲検の前に固定した設計と主張

依頼: agora-redesign#639 実装依頼書 2(依頼 id lt-R5RQNYWQDEYZ1QXKMER5VRPQ3X)。担当: 会話 c-QKRSZM0HRE0G3K33RYKG1R622T(claude-opus-5-5)。

この文書は盲検の反例を依頼する前に書き、以後は書き換えない。反例を受けた結論は `design.md` に書く。

## 完了の範囲と経緯

- 範囲 = 実装完了まで。実装は本線に入っている: doeff `0e7a5aa6`(関数 `service` を追加)・`40378572`(業務の語の直し)・
  `31459aea` と `611f47cb`(`macros.hy` と登録簿 `REGISTRY` の削除)、agora-controllers `4aefa59c` と `d3b7ef22`(呼び手の移行)。
- 経緯: 設計検証の記録はこの依頼に要求されていたが、実装を先に本線へ入れ、記録を後から作る。したがってこの文書の主張は
  「実装済みの設計について、盲検の前に固定した主張」であり、実装の前に固定した予測ではない。
- 基準の版: doeff `611f47cb15aa8b454fe312a546194b633297b088`、agora-controllers `d3b7ef22981a8dcb3a685fa15542defdf74c8b77`。

## 固定した要件

1. `packages/doeff-cluster/src/doeff_cluster/macros.hy` の `defservice`・`defsystem` を、macro を定義してよい名簿の外から無くす。
   名簿は増やさない(ADR-DOE-HY-005 R5)。
2. 道 A(macro を doeff-hy へ移す)か道 B(宣言を値と関数で書く)を選び、理由を記録する。道 B を採った。
3. 受入: `docs/adr/defadr_doeff_hy_005_typecheck_and_record_fences.hy::test_adr_doe_hy_005_macros_live_only_in_doeff_hy` の違反から
   `macros.hy` が消える。`packages/doeff-cluster/tests` の検が変更の前後で同じ本数・同じ結果。他 repo の呼び手を同じ日に直す。

## 置き換えた部品の責務と、責務ごとの要否・持ち主

macro `defservice` / `defsystem` がしていたことを 1 つずつ挙げ、要るかと、置き換えた後の持ち主を決めた。

| macro の責務 | 要否 | 置き換えた後の持ち主 |
| --- | --- | --- |
| 本体(Program を作る関数 `<名>-program`)を定義する | 要る | 呼び手(アプリ)が `defk` で直に書く |
| 契約の map が無い本体を `defn [do]` で包む | 要らない(使い手 0 — 移行の時点の本体 89 本すべてが契約つき) | 無し |
| 関数の参照 `module:attr` を導く | 要る(coordinator へ渡す宣言の形) | `service_model.program-reference`(M1) |
| `:requires`・`:config` などの鍵を文字列にする | 要る(宣言は JSON で渡る) | 呼び手が文字列の鍵で書き、`service_model.string-keyed`(M1)が検める |
| 知らない項目・`:env` の欠けを断る | 要る | 関数 `service` の keyword 引数(知らない引数・必須の `env` の欠けは呼び出しの時点で TypeError) |
| 宣言を登録簿 `REGISTRY` へ書く | 要らない(本番の読み手 0。読み手は登録を確かめる検の 1 行だけ) | 無し(消した) |
| 束ねた名前(変数名)と service の名前を一致させる | 要らない(機械が使うのは名前の文字列と関数の参照だけ) | 無し(呼び手の書き方の慣習) |
| `defsystem`: 名前と service の組を束ねる | 要る | 値 `System` の constructor そのもの |

## 責務(module)

| id | 責務 | 持つ知識 | 隠す知識 | 公開の口 | effect | 寿命 | 不変条件 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| M1 宣言の値 | service 1 本の宣言を、実行先で解ける関数の参照と JSON にできる設定を持つ不変の値として組み、組めない宣言をその場で断る(`service_model`: `ServiceDef`・`System`・`service`・`program-reference`・`string-keyed`・`UPDATE-FORMS`) | 宣言の欄(name・factory・env・requires・config・readiness・update・base-from)・参照の導き方・`update` の閉じた語彙 | 参照を `__module__`・`__qualname__` から導くこと・欄を鍵の順の tuple に正規化すること | `(service name program :env E [:requires d] [:config d] [:readiness d] [:update s] [:base-from d]) → ServiceDef`・`(System name #(…))`。失敗 = ValueError(入れ子の関数・lambda・未知の update)・TypeError(文字列でない鍵・知らない引数) | なし(module の読み込みの時に値を組むだけ) | module の最上位の不変の値。資源を持たないので解放は不要 | `(resolve (program-reference f))` は f そのもの。鍵は文字列。update ∈ {recreate, handoff} |
| M2 宣言の投影 | 同じ `System` から (a) 1 process で全 service を回す Program と (b) coordinator へ渡す JSON の宣言を導く(`system-main`・`system-main-program`・`system-declaration`・`service-program`・`config-of`・`resolve`) | coordinator の宣言の JSON の形・設定の上書きの重ね方・JSON の鍵から Hy の引数名への変換 | (a) で関数を直に使うか参照を解くか | `system-declaration(system, revision, overrides?) → list[dict]`・`system-main(system, overrides?) → Program[list]`・`service-program(service, overrides?) → Program` | (a) は doeff の scheduler の `Spawn`・`Gather` だけ。(b) は純粋 | 呼ぶたびに組む。状態を持たない | JSON の factory は M1 が導いた参照のまま。config = 宣言の config に上書きを重ねた物。設定の鍵が本体の引数に無ければ Program を組む時に失敗する |
| M3 実行先 | 宣言の JSON の factory を解き、config を引数に Program を作り、env の handler の組で走らせる(`job_entry.run-service`・coordinator) | process の起動・handler の組み立て・記録の層 | worker の process・commit の準備 | coordinator の宣言 JSON(M2 の出力) | 実 I/O(process・HTTP・file) | service の process の寿命。落ちたら coordinator が起こし直す | factory は `module:attr` で解ける |
| M4 service の本体 | 業務の周回(アプリの `defk <名>-program`) | 業務の判断・答え | — | `defk` の契約(`:pre` の引数 = 設定の鍵、`:post` の答え) | 型のある doeff の effect だけ(共有の保存 `ReadShared`/`WriteShared`・scheduler の Semaphore・宛先ごとの effect)。file・lock・socket・子 process を直に触らない | Program の寿命(周回を続ける・cycles で有限) | module の最上位の `defk`。直の I/O が無い |
| M5 本体の I/O の検査 | 宣言が名指す本体の中の直の I/O を、dir を問わず断る(agora-controllers `controllers/worker/adr/defadr_worker_business_code_touches_io_only_through_effects.hy` の `service-bodies-in` と `.semgrep.yaml` の worker の 4 規則) | 本体の特定(宣言 `(service "名" <symbol> …)` の symbol → 同じ module の最上位の `defk`)・断る語の正規表現 | form の範囲を Hy の reader で取ること | pytest の検 `test_a_service_body_that_touches_a_file_is_red`・`test_the_service_bodies_in_this_repo_touch_no_lock_socket_file` | 検査の道具として file を読む | 検の走行ごと | 読めた本体が 0 本なら赤(空の検査を見分ける) |
| M6 macro の置き場の検査 | doeff の repo で macro を定義してよいのは doeff-hy と名簿の file だけ(ADR-DOE-HY-005 R5) | 名簿 MACRO-OWNER-ROSTER | — | `test_adr_doe_hy_005_macros_live_only_in_doeff_hy` | 検査の道具として file を読む | 検の走行ごと | 名簿の外の `defmacro` は赤 |

## 強制方法

| 守る責務 | 強制方法と選んだ理由 | 実装箇所 | 実行経路 | 限界 |
| --- | --- | --- | --- | --- |
| M1 の参照が実行先で同じ関数へ解ける | 宣言の時点で入れ子の関数・lambda を ValueError で断る(値を組む関数の中の検め — 実行先で解けない宣言を作らせない) | `service_model.program-reference` | module の読み込みの時(宣言を組むたび)。検 = `packages/doeff-cluster/tests/test_service_declaration.hy` | 最上位でも、後から別の値で名前を束ね直した関数は検めない |
| M1 の鍵が JSON で渡る | 文字列でない鍵を TypeError | `service_model.string-keyed` | 宣言を組むたび | 値が JSON にできるかは検めない |
| M1 の update の語彙 | 閉じた語彙 `UPDATE-FORMS` の外を ValueError | `service_model.service` | 宣言を組むたび | — |
| M4 の本体に直の I/O が無い | Hy の reader で本体の form を取り、断る語の正規表現を当てる(generic の semgrep は複数行の Lisp の form の終わりを切れず、契約の map を持つ本体に届かなかった) | agora-controllers ADR の `service-bodies-in`・`SERVICE-BODY-IO-RE` | 検の実行(着地の門の焦点の列には入っていない・日次の全体の検で走る)。`controllers/worker/lab`・`services` の module 全体は `.semgrep.yaml` の 4 規則 | 断る語の一覧の外(例: `time.sleep`)は見ない。本体が呼ぶ別の関数の中は見ない(module 全体の規則は lab・services の dir だけ) |
| macro を名簿の外に置かない | 名簿の外の `defmacro` を赤にする検 | doeff `docs/adr/defadr_doeff_hy_005_typecheck_and_record_fences.hy` | doeff の日次の全体の検 | doeff の repo の中だけ。agora-controllers の macro は見ない |

## 変更シナリオ(6 軸)と事前の予測

| id | 軸 | 適用 | 変える要求 | 主張 | 予想する変更範囲 | 変わる module | 変わらない module |
| --- | --- | --- | --- | --- | --- | --- | --- |
| S-DIST | distribution | 適用 | 1 process の main で確かめた系を、同じ宣言のまま cluster へ出す(新しい service を足して coordinator へ宣言する) | 宣言の値(M1)を変えずに M2 が JSON を出し、M3 が参照を解いて 1 process の main と同じ関数を得る。前提: 本体の関数は module の最上位にある | アプリの宣言(`service` の 1 式と `System` への追加)と本体だけ | M4 | M1・M2・M3・M5・M6 |
| S-CONC | concurrency | 適用 | 書き手の service を、版の入れ替えの時に書きが重ならないように渡す(handoff) | 宣言の `:update "handoff"` を書くだけで M2 が JSON の `update` 欄に写し、M3 が新旧を並べて起こす。書きを 1 つに絞るのは本体の名前付き lease(M4)。前提: coordinator は handoff を実装済み | アプリの宣言の 1 引数と本体の lease の扱い | M4 | M1・M2・M3・M5・M6 |
| S-HW | hardware | 適用 | service を特定の種類の node(k3s の node・GPU を持つ機体)へ置く | `:requires` の文字列の鍵の写像に条件を書くだけ。M1・M2 は中身を解釈せず JSON へ写し、置き場を決めるのは coordinator の方策(M3) | アプリの宣言の `:requires`(と、新しい条件の語なら coordinator の方策) | M4(条件の語が新しければ M3) | M1・M2・M5・M6 |
| S-EFF | effects | 適用 | service の本体が新しい外部の副作用(HTTP の読み・通知の送信)を要る | effect の型と handler(env の module)を足し、本体は effect を出すだけ。宣言(M1)と投影(M2)は変わらない。本体が直に I/O を書けば、どの dir でも M5 が赤にする | アプリの effect の型・handler・env・本体 | M4(と env の module) | M1・M2・M3・M5・M6 |
| S-STORE | storage | 適用 | service の状態の置き場を worker の手元から共有の保存(や DB)へ変える | 本体は `ReadShared`/`WriteShared` の effect で読み書きし、置き場の選択は env の handler。本体が `sqlite3`・`open` などで直に保存すれば M5 が赤にする | env の handler(本体は変えない) | env の module | M1・M2・M3・M4・M5・M6 |
| S-SIM | simulation | 適用 | 系の全体を仮想の時計の上で決定的に回す(agora-controllers の `controllers/agora_sim`) | 同じ宣言の値を `System` で束ね、M2 の `system-main` が handler だけを差し替えて回す。宣言と本体は変わらない。設定の鍵が本体の引数に無ければ Program を組む時点で失敗し、黙って既定値にならない | 回す側の handler の組と上書きの設定だけ | なし(組み立ての入口だけ) | M1・M2・M3・M4・M5・M6 |

## 盲検の前に分かっている限界(盲検の入力には含めない)

- `ServiceDef` の constructor は公開で、`service` を通さずに組めば M1 の検めを迂回できる。
- 設定の鍵と本体の引数の食い違いは宣言の時点では検めない(Program を組む時に失敗する)。
- M5 の断る語の一覧は固定の正規表現で、一覧の外の I/O と本体から呼ぶ別の関数の中は見ない。
