;;; 本番の土台で閉じているかの検(test_foundation_closure.hy)の見本。
;;;
;;; 本体 = 業務の effect(Ping)・時計(Delay)・設定(Ask)・子の task(Spawn)を出す Program。翻訳の handler が Ping を外の世界の effect
;;; ⚠ 組み立ての関数を defn で書いているのは analyzer の穴(issue #837)待ちの一時の形 — defn を書かない決まり(ADR-DOE-HY-004)に反する。
;;;   #837 が着地して analyzer が defk の組み立てを読めるようになったら defk に改める。
;;; (Raw)に訳し、本番の土台が Raw・時計・設定に答え、scheduler が Spawn に答える。handler の節は 2 つ以上の式で書く — (resume 値) の
;;; 1 つだけの節は analyzer が読めない(foundation_check の頭の註の 5)。組み立ての関数は list の literal を返す素の関数で書く —
;;; doeff-effect-analyzer の analyze_env が読めるのはこの形だけ(defk・deff の契約で包んだ本体は読めない — foundation_check の頭の註の 2)。
(require doeff-hy.macros [defk defhandler <- val])
(import doeff [EffectBase DoExpr])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.scheduler [Spawn Wait])
(import doeff_time [Delay DelayEffect sync-time-handler])


(defclass Ping [EffectBase]
  "業務の effect(翻訳の handler が外の世界の effect に訳す)。")

(defclass Raw [EffectBase]
  "外の世界の effect(本番の土台が答える)。")


(defhandler translate
  {:tags {:context "doeff-cluster-test" :role "protocol"}}
  (Ping [] (<- r (Raw)) (resume r)))

(defhandler raw-world
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (Raw [] (val one 1) (resume one)))

(defhandler fake-clock
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (DelayEffect [seconds] (val none None) (resume none)))

(defhandler fake-settings
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (Ask [key] (val one "1") (resume one)))


(defk child []
  {:pre [] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "子の task(Spawn で運ばれる — 同じ handler の下で数える)。"
  (<- a (Ping))
  a)


(defk business []
  {:pre [] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "業務の本体: 業務の effect・時計・設定・子の task を出す。"
  (<- a (Ping))
  (<- (Delay 1.0))
  (<- b (Ask "ROUNDS"))
  (<- t (Spawn (child)))
  (<- c (Wait t))
  (+ a c))


(defk job [foundation]
  {:pre [(: foundation (| type DoExpr))] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "引数で受けた土台で本体を包む job(analyzer は引数の土台を追えない — foundation_check の頭の註の 4)。"
  (<- r (foundation (business)))
  r)


(defn translation-handlers []  ; defk にできない: analyzer の analyze_env は list の literal を return する素の関数しか読めない(#837 待ち — 着地したら defk に改める)
  "本体の中で並べる翻訳の組。"
  [translate])

(defn production-handlers []  ; defk にできない: analyzer の analyze_env は list の literal を return する素の関数しか読めない(#837 待ち — 着地したら defk に改める)
  "閉じている本番の土台の組(時計・設定・外の世界)。"
  [(state) fake-clock fake-settings raw-world])

(defn forgetful-handlers []  ; defk にできない: analyzer の analyze_env は list の literal を return する素の関数しか読めない(#837 待ち — 着地したら defk に改める)
  "時計を入れ忘れた本番の土台の組。"
  [(state) fake-settings raw-world])

(defn python-clock-handlers []  ; defk にできない: analyzer の analyze_env は list の literal を return する素の関数しか読めない(#837 待ち — 着地したら defk に改める)
  "時計を Python で書いた handler の工場(sync-time-handler)で並べた組 — analyzer は節を読めない。"
  [(state) (sync-time-handler) fake-settings raw-world])
