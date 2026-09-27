;;; Executable ADR: 名を持ち道具が読む宣言は def* で書く。外の世界に触る class(client・store)と状態の変わる class
;;; (模擬の store・キャッシュ)は作らず、土台の handler と session val / var に置く。値の class(Point2D)は許す。
;;;
;;; 出自 = operator 裁定 2026-09-27(Claude Code の会話・agora-redesign #798・逐語は :problem の fact)。
;;; coordinator の提案を operator が "perfect, lets go with def*" で採り、同じ日に的を 2 度絞った(class 全般 → 処理を持つ class →
;;; 外の世界に触る class と状態の変わる class。値の class は許す)。判定は名前でなく中身の証拠で、理由の註は要らない。
;;;
;;; 置き場の判断: ADR-DOE-HY-004(関数の語彙は defk のみ)の続きに足さず、新しい冊にした。HY-004 は関数の定義
;;; (defn / deff / defk)の語彙と、理由の註の無い deff の台帳を持つ冊で、型と class の定義は別の軸 — 同じ冊に混ぜると
;;; 台帳・針・law が 2 つの軸にまたがる。この冊は HY-004(関数)・HY-005 R5(macro は doeff-hy にだけ置く)と組になる。
;;;
;;; 追記(2026-09-28・operator 逐語 "lets add them"): 臭いの規則(doeff-linter DOEFF121〜125・Jev の DOEFF205)と、失敗の型の印
;;; (defrecord の :failure True)を R9・R10(と Jev の R11)として足した。置き場の判断: ADR-DOE-CORE-EFFECTS-003(Absent / Raise)は同じ日に
;;; 別の便(wt/absent-raise-impl)が大きく書き換えている最中で、同じ file に条を足すと着地で衝突する。臭いの規則は「道具が読む
;;; 宣言(def*)で書く」この冊の R1 の続き — 失敗の型の印は defrecord の頭の辞書に置く宣言で、linter が実行せずに読む。
;;;
;;; 戻し方: この ADR を足した commit を revert する(ADR の file 1 つが消える。DOEFF119 と defrecord の :tags / :check は
;;; 別の便の実装なので、それぞれの commit を別に戻す)。

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest val var <-])
(require doeff-hy.record [defrecord defwire])
(import dataclasses [dataclass FrozenInstanceError])
(import doeff_hy.wire [parse dump Malformed])


;; 生きた probe — データの型は defrecord で 1 行に建ち、実行時の値はその型を呼んで作る。
(defrecord ProbeBudgetRow
  #^ str name
  #^ int used
  #^ int limit)

;; 生きた probe(R8)— JSON の境目の型は defwire で宣言し、parse で解いて確かめ、dump で JSON へ戻す。
(defwire ProbeBudgetWire
  "予算の 1 行(JSON の境目)"
  {:names :camel}
  #^ str name
  #^ int used-units)


(defadr ADR-DOE-HY-007
  :title "名を持ち道具(linter・索引・エディタ・型検査)が読む宣言は def*(doeff-hy に置き、読み方の規則つき)で書く。外の世界に触る class(method に生の副作用・資源を欄に持つ — client・store)は土台の handler(資源は session val)に、状態の変わる class(method が self を書き換える — 模擬の store・キャッシュ)は handler の session var に置き換える。値の class(欄が変わらず method は欄から計算するだけ — Point2D)・例外・Enum・Protocol・外の基底を継ぐ物は許す。欄だけの型は defrecord を勧める。判定は名前でなく中身の証拠(doeff-linter DOEFF119・理由の註は要らない)"
  :status "accepted"
  :scope ["docs/adr/defadr_doeff_hy_007_def_macros_over_classes.hy"
          "packages/doeff-hy/src/doeff_hy/record.hy"
          "packages/doeff-hy/src/doeff_hy/wire.hy"
          "packages/doeff-hy/src/doeff_hy/json_value.py"
          "packages/doeff-linter"
          "packages/doeff-linter/src/project/smells.rs"]
  :problem
    [(fact
       "operator 裁定 2026-09-27(逐語 2 つ): \"and i wonder, if we should keep preferring def* macros  instead of making classes.\" / \"perfect, lets go with def*\""
       :evidence "Claude Code の会話(2026-09-27・agora-redesign #798)— coordinator 経由")
     (fact
       "operator 裁定 2026-09-27(的の絞り込み・逐語 2 つ): \"well, use of defclass maybe okay,,, but i dont really see the reason to use them in doeff+hy code, like **client or something like that\" / \"well, a class like Point2D/Point3D could be a class right? but clients and stores... they are completely suited for handlers/effects..\""
       :evidence "Claude Code の会話(2026-09-27・agora-redesign #798)— coordinator 経由")
     (fact
       "operator の問い 2026-09-27(逐語): \"yeah pure data like AST/numerics are good for data classes. global services are always handler/effects, then what about stateful objects like game entity,, like a character?\""
       :evidence "Claude Code の会話(2026-09-27・agora-redesign #798)— coordinator 経由")
     (fact
       "実測: agora-controllers に defclass 848・defrecord 338(coordinator の計測・2026-09-27)。同じ日のこの席の数え(git grep の出現数・origin/main 380ae943)は defclass 819・defrecord 414。doeff(origin/main 772a4405)は defclass 614・defrecord 98。"
       :evidence "git grep -c '(defclass' / '(defrecord' -- '*.hy'")
     (fact
       "defrecord は doeff-hy が所有する template macro で、欄の名前と型を宣言した凍結の dataclass を 1 行で建てる。今は :tags と :check の節を持たない — 検めを要する型は素の defclass と __post_init__ で書かれている。"
       :evidence "packages/doeff-hy/src/doeff_hy/record.hy(defrecord)・ADR-DOE-HY-005 R4")
     (fact
       "operator の決定 2026-09-28 未明(逐語): \"lets add them\" — 臭いを拾う規則を doeff-linter に足す。題材は agora-controllers の controllers/kanban/core/tag_judgment.hy の decide-tag で、決定の時点の linter はこの file に何も出していなかった。"
       :evidence "Claude Code の会話(2026-09-28・agora-redesign #798)— coordinator 経由")
     (fact
       "decide-tag(agora-controllers 068dee21)の形: core の判断の中で (.get payload \"subject\") を (isinstance subject str) で検める / (match said (Refusal) (do (<- refused … (rejected (+ said.reason \": \" said.detail))) (return refused))) で受けた断りを包み直して返す / (<- bad-subject WritePlan …) の直後に (return bad-subject) / for の中で (:= writes (+ writes #(…)))。本線 0a528aa1 に当てると DOEFF121 78・122 9(Refusal に印を付けた後)・123 112・124 168・125 30。"
       :evidence "doeff-linter wt/hy-smells の editor-json(agora-controllers wt/hy-smells)")
     (fact
       "defk / deff / defp / defhandler / defeffect の頭の辞書は :tags を受け、定義の属性に残す(agora-redesign #800)。素の defclass にはタグの置き場が無く、タグから定義を並べる閲覧に現れない。"
       :evidence "packages/doeff-hy/src/doeff_hy/declarations.hy")
     (fact
       "operator 2026-09-28 未明(逐語 2 つ・R8 の出自): \"I realized JSONValue is being used\" / \"JsonValue is in wire.hy,\"。続けて(逐語): \"and i dont think we should make anyone use that directry instead of actually parsing and validating it like pydantic does\""
       :evidence "Claude Code の会話(2026-09-28・agora-redesign #840)— coordinator 経由")
     (fact
       "operator 2026-09-28 未明(逐語 2 つ): \"hmm, cant we have some def* macro for this?\" — 案 A(別の macro defwire)と案 B(defrecord に :wire の節)を示し、operator: \"A\""
       :evidence "agora-redesign #840 のコメント(2026-09-27T16:22Z)")
     (fact
       "実測: agora-controllers の本線に JsonValue(名前を変えた素の dict — dict | list | str | int | float | bool | None)を使う file が 36・179 行。core 7 file の判断の関数が JsonValue を受けて素の dict / list を返し、protocol は JsonValue を手で分解して読む。定義は doeff_records.wire の他に TypeAlias の写しが 4 つ・その場の定義が 3 つ。"
       :evidence "git grep -nwE 'JsonValue|JSONValue' -- controllers services(agora-controllers・2026-09-28)・agora-redesign #840 の本文")]
  :context
    [(interpretation
       "def* の宣言は、名前・欄・契約・タグを macro の形で固定するので、linter・索引・エディタ・型検査が読み方の規則(HY-005 R5 の投影)1 つで読める。素の class は method の中に何でも書けるので、道具はそれが値の型なのか振る舞いなのかを読み分けられない。")
     (interpretation
       "client・store のように外の世界に触る method は effect を通らないので、handler の差し替え(模擬)・記録と再生・層の規則(生の副作用は土台だけ)が効かず、生の副作用が土台の外に漏れる。接続や資源は土台の handler が (session val …) で持ち、状態は handler の (session var …) で持てば、どちらも effect の向こうに入る。")
     (interpretation
       "値の class(Point2D・Point3D — 欄が変わらず、method は欄から計算するだけ)は外の世界にも変わる状態にも触らないので、上の害が無く許す。線は名前ではなく中身 — method の中に生の副作用があるか、資源を欄に持つか、self を書き換えるか — で引く。")
     (interpretation
       "dataclass の __post_init__ で書いていた検めは、defrecord の :check の節へ移す — 検めが型の宣言の一部として道具から読める。")
     (interpretation
       "JSON を JsonValue のまま運ぶと、欄の名の綴り違いも型の違いも使う所まで見えず、読む関数ごとに手で分解する(同じ形の知識が呼び手の数だけ散る)。境目で一度だけ、型を宣言した値へ解いて確かめれば、内側は型のある値だけを見る。解き方は pydantic の TypeAdapter が持つ(欄の名の写し・知らない欄・厳しい型の検め・JSON Schema)ので、自前の分解の関数を書かない。宣言を def* にすると、欄と型と wire の名を道具が実行せずに読める(R1)。")]
  :decision
    [(rule R1 "名を持ち道具(linter・索引・エディタ・型検査)が読む宣言は def* で書く。def* の macro は doeff-hy に置き、読み方の規則(投影)を同じ便で持つ(ADR-DOE-HY-005 R4・R5)。")
     (rule R2 "欄だけの型は defrecord で書くことを勧める(情報)。defrecord は :tags({:context … :role …})と :check(値を作る時の検め)の節を持つ。dataclass の __post_init__ に書いていた検めは :check へ移す。")
     (rule R3 "外の世界に触る class(method に生の副作用がある・資源〔接続・file・socket〕を欄に持つ — client・store・手順を包む物。例 PgStore・Client)は作らない。土台の handler に置き換え、資源は (session val …) で持つ。")
     (rule R4 "状態の変わる class(method が self を書き換える — 模擬の store・キャッシュ・ゲームのキャラクターのような entity。例 FakeRecordStore)は作らない(警告)。状態を持つ entity は、値を defrecord(不変)・振る舞いを新しい値を返す純粋な関数(defk)・状態を world の handler の (session var …) 1 か所に置き、変化は effect(例 GetCharacter・UpdateCharacter)で流す。理由: self を書き換えると変化が effect を通らず、模擬・記録と再生・並行が効かない。速さのために書き換えが要る時(大量の entity・numpy の配列・ECS)も、書き換えは handler の中だけで行う。")
     (rule R5 "許す class: 値の class(欄が変わらず、method は欄から計算するだけ — Point2D・Point3D)・例外・Enum・Protocol・外の library の基底を継ぐ物。理由の註は要らない。判定は名前でなく中身の証拠(method の中の生の副作用・資源の欄・self の書き換え)で、正本は doeff-linter の DOEFF119。DOEFF119 と defrecord の :tags / :check が着地するまで、この冊の law は未配線。")
     (rule R7 "区分の表: 純粋なデータ(AST・数値・Point2D)= defrecord(純粋な method は可)/ global な service(client・store)= 土台の handler と effect / 状態を持つ entity(ゲームのキャラクターなど)= 値(defrecord)+ 純粋な関数 + world の handler の session var。")
     (rule R8 "JSON の境目の型は defwire で宣言する(doeff-hy の record.hy・operator の決定 2026-09-28 \"A\")。書き方は defrecord と同じ欄の形に、頭の辞書 {:names :camel|:snake|:kebab|{欄 \"wire の名\" …} :unknown :reject|:ignore :tags … :check […]} を必ず置く(:names は必須 — wire の欄の名の写しを黙って決めない・明示の辞書は欄を全部名指す・:unknown の既定は :reject)。展開は defrecord をそのまま使い(凍結・キーワード引数だけ・:tags・:check)、型の __pydantic_config__ と __doeff_wire__(WireShape — 欄の名の写し・知らない欄の扱い・TypeAdapter)を置く。解き手は doeff_hy.wire の parse(JSON の値・凍らせた JSON)・parse-json(JSON の文字列)・dump・dump-json・json-schema の 1 か所で、型の検めは厳しい(文字列を数にしない・真偽を数にしない・配列は tuple の欄に・defenum の欄は値の綴り)。形の違う JSON の答えは Malformed(どの型の・どの欄が・なぜ — 5 つ目の失敗の種類・ADR-DOE-CORE-EFFECTS-003 R17)。Absent / Raise の段階 2 が本線に入るまで parse は Malformed を値で返し(答えの型 = (| T Malformed))、段階 3 の切り替えで Raise(Malformed) に寄せる。JSON の値(JsonValue — 唯一の定義は doeff_hy.json_value)に触ってよいのは、doeff_hy.wire と、送受信そのものを行う foundation の module だけ(doeff-linter DOEFF120 — 既存の分は使う repo の登録簿)。入れ子の欄の型も defwire の型にする。dump / dump-json は既定値と同じ欄を書かない(入れ子の型の欄も — `(setv #^ (| str None) x None)` の None は null でなく欄が無い。読みは無い欄を既定値で埋めるので往復する。既定値の無い欄は None でも null で書く。追補 2026-09-28: 画面の設定の行の入れ子の agent の宣言 — 契約が任意の欄を null でなく欄の無いことで表す。戻し方 = wire.hy の :exclude-defaults を外す)。戻し方: defwire を defrecord と手書きの TypeAdapter に展開し直す(使う側の parse / dump の書き方はそのまま)。この条は 2026-09-28 に #840 の担当が足した。")
     (rule R9 "臭いの規則(doeff-linter・決定的・重さの既定は warning): DOEFF121 = 判断の層(設定 smells.shape_check_layers)の定義が文字列の鍵の (.get x \"欄\") とその欄への isinstance で入力の形を検める(直し方 = protocol の境目で defwire の型に parse)/ DOEFF122 = match の腕が失敗の型を受け、受けた値かそれを包み直した値を return するだけ(手書きの例外の再送出 — 直し方 = (<- (Raise …)) と呼ぶ側の on-raise。成功の早い抜けは拾わない)/ DOEFF123 = (<- x T (f …)) の直後の (return x) で x を他で使わない(直し方 = (return (! (f …))) か Raise)/ DOEFF124 = 同じ値の 2 つ以上の欄を + か f 文字列で 1 本の文字列につなぐ / DOEFF125 = for / while の中の (:= xs (+ xs #(…)))(直し方 = 内包表記)。JsonValue・dict の型の注記は DOEFF120(agora-redesign #840)の持ち分で重ねない。重さは初め info とし、Absent / Raise の段 1・2 が doeff の本線に入ったら warning に上げると決めていた — 2026-09-28 に入った(cf6ef9db)ので既定を warning にした。repo の設定 [tool.doeff-linter.rules.<ID>] severity で info に下げられる(error にはしない)。登録簿に載った既存の当たりは info。")
     (rule R10 "失敗の型は名前で決め打ちせず、宣言から取る: (a) defeffect の :failure / :absent に挙げた型(ADR-DOE-CORE-EFFECTS-003 R5)、(b) defrecord の頭の辞書の :failure True(effect の答えでない失敗の値 — 検めの関数が返す断りなど)。印は defrecord が class の属性 __doeff_failure__ に残し、doeff-linter は実行せずに読む。型は file の import と定義の場所で module まで解くので、同じ名の型が別の module にあっても宣言した方だけが失敗の型になる。印の形の選び方(2026-09-28・f143ee92 の席が決めた戻せる決定): defeffect の :failure は型の列だが、defrecord の :failure は字面の True / False — 型そのものの性質だから。Absent / Raise の作業(doeff-hy の macros.hy・handle.hy)と同じ file を触らないよう、印は record.hy の defrecord にだけ置いた。戻し方: 頭の辞書の受ける鍵から :failure を外し、DOEFF122 を defeffect の宣言だけで判じる形に戻す。")
     (rule R11 "Jev の規則 DOEFF205: 役が judgment / program の定義(定義の :tags か module の頭のタグ)に、入力の形の検めと業務の判断が混ざっているかを、混ざっている / 形の検めだけ / 判断だけ / どれでもない から Jev に選ばせる。物差しは repo の architecture.hy の層(設定 semantic.mixed_concerns.layer — agora は core)の説明。重さは warning まで(error にしない)。較正の正例 = decide-tag、反例 = 形の検めの無い純粋な判断(card-tags-of)。撃つのは --semantic / --semantic-all の時だけで、保存ごとの実行は cache を読むだけ(未判定は合格に数えない)。")
     (rule R12 "service の依存(doeff-linter DOEFF116)で、依存先のどの層を読んでよいかは読む側の層ごとに architecture.hy の layer の :dependency-layers で宣言する(無ければ :open-layers — 既定は intent)。operator の決定 2026-09-28 朝(逐語 \"A okay\" — 案 A「組み立ての entry に限り、依存先の service の protocol(翻訳の handler)も読んでよい。entry は全体を組む所なので」)を、層の名を linter に書かずに宣言で表すため。agora は (layer entry … :dependency-layers [intent protocol])。:depends-on に無い service を読むのは今までどおり違反、:dependency-layers を持たない層は今までどおり :open-layers だけ(2026-09-28・f143ee92 の席が決めた戻せる決定。戻し方: layer の :dependency-layers を読む形を外し、DOEFF116 を :open-layers だけで判じる形に戻す)。")
     (rule R6 "改訂の記録(2026-09-27):初版の『振る舞いを持つ class を作らない・外の library が class を要求する所だけ理由の註つきの逃げ道』は、operator の的の絞り込み 2 つで R3〜R5 に置き換えた。戻し方: 的を広げ直すなら、この改訂の commit を revert する(初版の R2〜R5 と law へ戻る)。")]
  :laws
    [(law world-touching-classes-become-foundation-handlers
       :statement "for_all class c(Hy・例外・Enum・Protocol・外の基底を継ぐ物を除く): c の method に生の副作用が無く、c の欄に資源(接続・file・socket)が無い — 外の世界に触る物は土台の handler で、資源は session val"
       :counterexamples
         [(counterexample "(defclass PgStore [] (defn put [self row] (with [c (psycopg.connect self.dsn)] …))) — 書きが effect を通らず、模擬へ差し替えも記録と再生もできない")
          (counterexample "(defclass BudgetClient [] (defn spend [self n] (requests.post self.url …))) — client は effect と土台の handler にする")]
       :enforced-by ["doeff-linter DOEFF119"]
       :wiring "未配線(2026-09-27)— DOEFF119 は doeff-linter に未着地")
     (law state-changing-classes-become-session-var
       :statement "for_all class c(Hy・上と同じ除外): c の method は self を書き換えない — 変わる状態は handler の session var"
       :counterexamples
         [(counterexample "(defclass FakeRecordStore [] (defn put [self row] (.append self.rows row))) — 模擬の store の状態が handler の外に在り、handler の差し替えで入れ替わらない")
          (counterexample "キャッシュの class が (setv (get self.entries key) v) で覚える — 状態は (session var …) で持つ")
          (counterexample "(defclass Character [] (defn take-damage [self n] (-= self.hp n))) — キャラクターの変化が effect を通らず、記録と再生・並行が効かない。値は defrecord、take-damage は新しい値を返す関数、状態は world の handler の session var に置き、UpdateCharacter で流す")]
       :enforced-by ["doeff-linter DOEFF119"]
       :wiring "未配線(2026-09-27)— DOEFF119 は doeff-linter に未着地(この law は警告)")
     (law value-classes-are-allowed
       :statement "for_all class c: c の欄が変わらず、c の method が欄から計算するだけ ⇒ c は DOEFF119 の違反ではない(Point2D・Point3D)"
       :counterexamples
         [(counterexample "Point2D に norm の method があるだけで違反にする — 名前や method の有無で判定すると、害の無い値の class まで断る")]
       :enforced-by ["doeff-linter DOEFF119"]
       :wiring "未配線(2026-09-27)— DOEFF119 は doeff-linter に未着地")
     (law json-is-parsed-into-declared-types
       :statement "for_all module m(doeff_hy.wire と architecture.hy の :wire-modules に名指した foundation の module を除く): m は JsonValue / JSONValue / JsonObject / JSONObject を使わない ∧ for_all JSON の値 v と defwire の型 T: parse(T, v) は T の値か Malformed(どの型の・どの欄が・なぜ)で、T の値なら parse(T, dump(parse(T, v))) = parse(T, v)"
       :counterexamples
         [(counterexample "(defk state-of [row] {:pre [(: row JsonValue)] …} (.get row \"state\")) — JSON を手で分解して読む。綴り違いと型の違いが使う所まで見えない")
          (counterexample "core の判断の関数が (: value JsonValue) を受けて素の dict を返す — 型のある値に解かずに内側へ運ぶ")
          (counterexample "{\"landedAt\": \"3\"} を int の欄に黙って 3 として入れる — 型の違いを業務の失敗(Malformed)にせず飲み込む")]
       :enforced-by ["doeff-linter DOEFF120" "packages/doeff-hy/tests/defwire_deftests.hy"]
       :wiring "一部配線(2026-09-28)— parse と dump の往復・型違いの Malformed は defwire_deftests.hy が確かめる。DOEFF120 は doeff-linter の別の便(agora-redesign #840)")
     (law judgment-does-not-check-json-shape
       :statement "for_all 定義 d ∈ 判断の層: d の中に、文字列の鍵で読んだ欄 (.get x \"欄\") への isinstance が無い — 入力の形は通信の境目で型のある値に解く"
       :counterexamples
         [(counterexample "(val subject (.get payload \"subject\")) (when (not (isinstance subject str)) …) — core の判断が JSON の形を検めている")]
       :enforced-by ["doeff-linter DOEFF121"]
       :wiring "配線(2026-09-28)— doeff-linter DOEFF121(wt/hy-smells)")
     (law failures-are-raised-not-returned
       :statement "for_all match の腕 a: a の型が宣言した失敗の型 ⇒ a の本体は受けた値(か、それから作った値)を return するだけではない — 失敗は Raise で出し、受ける所は on-raise"
       :counterexamples
         [(counterexample "(match said (Refusal) (do (<- refused Plan (rejected (+ said.reason \": \" said.detail))) (return refused)) (TagsAccepted) None) — 手書きの再送出")]
       :enforced-by ["doeff-linter DOEFF122"]
       :wiring "配線(2026-09-28)— doeff-linter DOEFF122(wt/hy-smells)。失敗の型は宣言(defrecord :failure True・defeffect :failure / :absent)から")
     (law binds-are-not-returned-straight-away
       :statement "for_all (<- x T (f …)) の直後の (return x): x が定義の中で他に使われる"
       :counterexamples
         [(counterexample "(<- bad-subject WritePlan (rejected …)) (return bad-subject) — 名は返すためだけ")]
       :enforced-by ["doeff-linter DOEFF123"]
       :wiring "配線(2026-09-28)— doeff-linter DOEFF123(wt/hy-smells)")
     (law typed-values-are-not-flattened-into-text
       :statement "for_all + か f 文字列 e: e が同じ値の 2 つ以上の欄を文字列と一緒につながない"
       :counterexamples
         [(counterexample "(+ said.reason \": \" said.detail) — 型のある断りを 1 本の文字列に潰す")]
       :enforced-by ["doeff-linter DOEFF124"]
       :wiring "配線(2026-09-28)— doeff-linter DOEFF124(wt/hy-smells)")
     (law accumulators-are-not-rebuilt-in-loops
       :statement "for_all for / while の本体: (:= xs (+ xs #(…))) / (setv xs (+ xs […])) が無い — 蓄えは内包表記で 1 度に作る"
       :counterexamples
         [(counterexample "(for [word adding] (:= writes (+ writes #((AttachTag …))))) — 毎回作り直す蓄え")]
       :enforced-by ["doeff-linter DOEFF125"]
       :wiring "配線(2026-09-28)— doeff-linter DOEFF125(wt/hy-smells)")
     (law judgments-do-not-mix-shape-checks
       :statement "for_all 定義 d(役 judgment / program): d は入力の形の検めと業務の判断を混ぜない(Jev の判定・warning まで)"
       :counterexamples
         [(counterexample "decide-tag — 形の検め(subject の型・add / remove の形)と札の付け外しの判断が 1 つの defk に並ぶ")]
       :enforced-by ["doeff-linter DOEFF205"]
       :wiring "配線(2026-09-28)— doeff-linter DOEFF205(wt/hy-smells・Jev の cache を読む)")]
  :enforcement
    [(deftest test-adr-doe-hy-007-values-are-made-by-calling-a-defrecord-type
       ;; 実演: データの型は defrecord の 1 行で建ち、値はその型を呼んで作る(凍結 — 書き換えは断られる)。
       (val row (ProbeBudgetRow :name "lab" :used 3 :limit 10))
       (assert (= #(row.name row.used row.limit) #("lab" 3 10)))
       (assert (= row (ProbeBudgetRow :name "lab" :used 3 :limit 10)))
       (var refused False)
       (try
         (setv row.used 4)  ; 値の中身の書き換えを試す(断られることの検)
         (except [FrozenInstanceError]
           (:= refused True)))
       (assert refused "defrecord の値は凍結されている(書き換えは断られる)"))
     (deftest test-adr-doe-hy-007-json-is-parsed-into-a-defwire-type
       ;; 実演(R8): JSON は defwire の型へ解いて確かめる — 欄は wire の名で読み、型の違いは Malformed、dump で同じ JSON へ戻る。
       (<- row (parse ProbeBudgetWire {"name" "lab" "usedUnits" 3}))
       (assert (= row (ProbeBudgetWire :name "lab" :used-units 3)))
       (<- raw (dump row))
       (assert (= raw {"name" "lab" "usedUnits" 3}))
       (<- wrong (parse ProbeBudgetWire {"name" "lab" "usedUnits" "3"}))
       (assert (isinstance wrong Malformed))
       (assert (= (lfor f wrong.fields f.field) ["usedUnits"])))]
  :plans ["agora-redesign #798" "agora-redesign #840"])
