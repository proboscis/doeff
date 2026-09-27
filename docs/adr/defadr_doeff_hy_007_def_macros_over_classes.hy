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
          "packages/doeff-linter"]
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
     (rule R8 "JSON の境目の型は defwire で宣言する(doeff-hy の record.hy・operator の決定 2026-09-28 \"A\")。書き方は defrecord と同じ欄の形に、頭の辞書 {:names :camel|:snake|:kebab|{欄 \"wire の名\" …} :unknown :reject|:ignore :tags … :check […]} を必ず置く(:names は必須 — wire の欄の名の写しを黙って決めない・明示の辞書は欄を全部名指す・:unknown の既定は :reject)。展開は defrecord をそのまま使い(凍結・キーワード引数だけ・:tags・:check)、型の __pydantic_config__ と __doeff_wire__(WireShape — 欄の名の写し・知らない欄の扱い・TypeAdapter)を置く。解き手は doeff_hy.wire の parse(JSON の値・凍らせた JSON)・parse-json(JSON の文字列)・dump・dump-json・json-schema の 1 か所で、型の検めは厳しい(文字列を数にしない・真偽を数にしない・配列は tuple の欄に・defenum の欄は値の綴り)。形の違う JSON の答えは Malformed(どの型の・どの欄が・なぜ — 5 つ目の失敗の種類・ADR-DOE-CORE-EFFECTS-003 R17)。Absent / Raise の段階 2 が本線に入るまで parse は Malformed を値で返し(答えの型 = (| T Malformed))、段階 3 の切り替えで Raise(Malformed) に寄せる。JSON の値(JsonValue — 唯一の定義は doeff_hy.json_value)に触ってよいのは、doeff_hy.wire と、送受信そのものを行う foundation の module だけ(doeff-linter DOEFF120 — 既存の分は使う repo の登録簿)。入れ子の欄の型も defwire の型にする。戻し方: defwire を defrecord と手書きの TypeAdapter に展開し直す(使う側の parse / dump の書き方はそのまま)。この条は 2026-09-28 に #840 の担当が足した。")
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
       :wiring "一部配線(2026-09-28)— parse と dump の往復・型違いの Malformed は defwire_deftests.hy が確かめる。DOEFF120 は doeff-linter の別の便(agora-redesign #840)")]
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
