;;; Executable ADR: doeff-cluster の job が受け取るのは Program の値 1 つだけ — handler は Program の中の
;;; with-handlers で与え、実行器(job_entry)は既定の handler を 1 つも足さない。service と task は同じ入口で、
;;; 設定は Program の中の Ask と、それに答える os.environ を読む handler で読む。
;;;
;;; 出自 = operator 裁定 2026-09-27(Claude Code の会話・agora-redesign #829・逐語は :problem の fact)。
;;;
;;; 改訂 2026-09-27(agora-redesign #829): R5 の記録係を「未決」から決定へ書き換えた — 記録と再生は Program の中の
;;; with-handlers に置く handler で、runner は差し込まない(逐語は :problem の fact)。同じ日に R5b を足した — 記録係より
;;; 内側の handler は決定的でなければならない(記録係に届かない effect は再生で同じ計算が答え直す)。同じ日に R3b を足した —
;;; 宣言の中の Program の値は task と同じく詰めた文字列(encode-program / decode-program)で運ぶ。同じ日に R4b を足した —
;;; Program の宣言が書くのは必要な能力の名前(:needs)だけで、置き場所の名前(kind=k3s・role=agent-exp・機体の名前)は書かない。
;;; 同じ日に R7 を足した — 旧い宣言を受け付ける移行の期間は置かず、旧い形は宣言の時点で理由つきで断る。
;;;
;;; 戻し方: この ADR を足した commit を revert する(ADR の file 1 つと enforcement 台帳の数が消える)。R5 の改訂だけを
;;; 戻すなら、その改訂の commit を revert する(R5 が「未決」の文へ、law runner-inserts-no-recorder が消える)。

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest defk <- val])
(import doeff [run])
(import re)
(import pathlib [Path])


;; ---------------------------------------------------------------------------
;; ratchet 台帳 — 2026-09-27 時点の job_entry.hy で、実行器が Program の外から handler を足している
;; 呼び出しの数(origin/main faf81b97 の実測)。終端は全部 0(R2)。針は「現在数 <= 台帳数」を強制し、
;; 減らした便は同じ便で台帳を削る(削り忘れも赤)。
;;   scheduled       — scheduler の handler を runner が包む(service と task で 1 つずつ)
;;   env-handlers    — env の関数(import path)が組んだ handler の組を runner が包む(service と task で 1 つずつ)
;;   recording-layer — effect の記録係を runner が足す(service だけ・R5 の決定で Program の中の handler へ移して 0 にする)
;; ---------------------------------------------------------------------------

(val RUNNER-HANDLER-ROSTER
  {"scheduled" 0
   "env-handlers" 0
   "recording-layer" 0})  ; 2026-09-27 に全部 0(agora-redesign #833 段 3 — job_entry は (run program) だけ)

(val JOB-ENTRY "packages/doeff-cluster/src/doeff_cluster/job_entry.hy")

(defk count-runner-handler-sites [text]
  {:pre [(: text str)]
   :post [(: % dict)]
   :tags {:context "doeff-cluster-adr" :role "judgment"}}
  "job_entry の本文で、runner が Program の外から handler を足す呼び出しを種類ごとに数える(針と台帳の共通の物差し)。"
  (dfor name RUNNER-HANDLER-ROSTER
        name (len (re.findall (+ r"\(" (re.escape name) r"\s") text))))


(defadr ADR-DOE-CLUSTER-001
  :title "doeff-cluster の job(service も task も同じ API)が受け取るのは Program の値 1 つ(defk の関数を呼んだ結果)だけ。handler は Program の中の with-handlers で与え、実行器 job_entry は既定の handler を 1 つも足さない(scheduled と env の関数の包みも外す)。service の :env・:config・:env-config をやめ、設定は Program の中の Ask と os.environ を読む handler で読む。宣言が process へ渡す環境変数は宣言の値として持つ"
  :status "accepted"
  :scope ["packages/doeff-cluster/src/doeff_cluster/job_entry.hy"
          "packages/doeff-cluster/src/doeff_cluster/service_model.hy"
          "packages/doeff-cluster/src/doeff_cluster/remote_model.hy"
          "docs/adr/defadr_doeff_cluster_001_job_accepts_a_program.hy"]
  :problem
    [(fact
       "operator 裁定 2026-09-27(逐語 3 つ): \"okay such handlers/env is not supposed to be passed as job. what a job should accept is a Program. not explicit program 'and' handler. because handlers are dynamic scoped expressions in doeff\" / \"a doeff job runner should not have any 'default' handler.\" / \"i dont find any reason to have different api for services. if any configuration is needed we can always have Ask effect and a handler reading os.environ\""
       :evidence "Claude Code の会話(2026-09-27・agora-redesign #829)— coordinator 経由")
     (fact
       "今の job_entry は service と task で入口が違う: service は --factory(関数の import path)と --config(本体の引数)と --env(handler の組を組む関数の import path)を受け、task は --blob(Program の値)と --env を受ける。どちらも runner が (scheduled (with-handlers <env の handler> program)) で包み、service はさらに記録係(recording-layer)を足す。"
       :evidence "packages/doeff-cluster/src/doeff_cluster/job_entry.hy(run-service・task-outcome・env-handlers・recording-layer)")
     (fact
       "service の宣言は :env(env の関数の import path)・:config(本体の引数)・:env-config(env だけが読む設定)を持ち、coordinator へは 2 つを重ねた平たい run.config が渡る。"
       :evidence "packages/doeff-cluster/src/doeff_cluster/service_model.hy(ServiceDef の env・config・env-config)")
     (fact
       "operator 裁定 2026-09-27(記録係・逐語 2 つ): \"hmm, it maybe useful, but would it be handler matter, or vm instrument? does algebraic effect handlers support such uses in general?\" / \"then why are we not using handler to do what you want?\""
       :evidence "Claude Code の会話(2026-09-27・agora-redesign #829)— coordinator 経由")
     (fact
       "今の記録係は job_entry の recording-layer が、run.config の record 欄を見て env の handler の一番内側に足す(recording-handler・record_handlers.hy)。記録係は effect を外へ撃ち直して答えを書き留め、継続を再開する『間に入る handler』の形をしている。"
       :evidence "packages/doeff-cluster/src/doeff_cluster/job_entry.hy(recording-layer)・packages/doeff-cluster/src/doeff_cluster/record_handlers.hy")
     (fact
       "operator の問い 2026-09-27(逐語): \"well, but the thing is that if the effect is handled before arrivint to such recorder, the recorder can't have any idea about it. so how do people resolve this with handler?\""
       :evidence "Claude Code の会話(2026-09-27・agora-redesign #829)— coordinator 経由")
     (fact
       "task は Program の値を encode-program で詰めた文字列(--blob の file)で運び、実行先が decode-program で解く。service は関数の参照(module:attr)と本体の引数の JSON(:config)で運び、実行先が関数を呼んで Program を作る — 同じ job なのに運び方が 2 つある。"
       :evidence "packages/doeff-cluster/src/doeff_cluster/remote_model.hy(encode-program・decode-program)・job_entry.hy(run-service・task-outcome)")
     (fact
       "operator 裁定 2026-09-27(置き場所・逐語 2 つ): \"hmm, but specifying such 'kind:k3s' sounds... not right\" / \"declaring what a program require is okay, but it shouldnt be 'k3s' right? we want a program to declare required capability rather than runner location\""
       :evidence "Claude Code の会話(2026-09-27・agora-redesign #829)— coordinator 経由")
     (fact
       "今の置き場所の指定は :requires(鍵と値の組)で、coordinator が worker のラベルと 1 つずつ等しいかを照らす(labels-satisfy)。書かれている値は置き場所の名前で、doeff-cluster の例と検は {\"kind\" \"k3s\"}、agora は {\"role\" \"agent-exp\"}(実験用の namespace の印)。"
       :evidence "packages/doeff-cluster/src/doeff_cluster/coordinator/core/cluster_policy.hy(labels-satisfy)・service_model.hy の頭の註(:requires {\"kind\" \"k3s\"})・packages/doeff-cluster/tests/test_detached.hy・agora-controllers controllers/runtime_env/declare.hy と controllers/agora_sim/tests/test_emulated_runtime_env_sender.hy(Requirement \"role\" \"agent-exp\")")
     (fact
       "operator 裁定 2026-09-27(移行・逐語 2 つ): \"i dont think we want old declarations accepted at all.\" / \"it just makes everything confusing so\""
       :evidence "Claude Code の会話(2026-09-27・agora-redesign #833)— coordinator 経由")
     (fact
       "再生が記録と食い違った時、今の再生の handler は ReplayDiverged を上げて止まる(再生の分岐)。"
       :evidence "packages/doeff-cluster/src/doeff_cluster/record_model.hy(ReplayDiverged)・record_handlers.hy・replay_main.hy")]
  :context
    [(interpretation
       "doeff の handler は動的な scope の式であり、Program の外に「handler の組」を別の値として持つと、同じ Program が実行器ごとに違う意味になる。handler を Program の中の with-handlers に置けば、job の意味は Program の値だけで決まり、手元の run・模擬・cluster の実行が同じ値を同じ意味で走らせる。")
     (interpretation
       "runner の既定の handler(scheduler・env の組)は、Program が自分で並べるべき物を実行器が暗黙に足している形で、どれが効いているかが Program から読めない。既定を 0 にすれば、足りない handler は Program の中で未処理の effect として表に出る。")
     (interpretation
       "設定を宣言の欄(:config・:env-config)で渡す形は、設定を読む専用の口を job API に増やす。設定の読みは Ask の effect と、それに答える os.environ を読む handler で書けるので、service だけ別の API を持つ理由は無い。process へ渡す環境変数そのものは、宣言の値(どの process に何を渡すか)として残す。")
     (interpretation
       "記録係より内側(Program 側)で答えられた effect は記録係に届かない。それでも再生が成り立つのは、内側の handler が決定的な場合だけ — 同じ入力(記録係が境目で記録した答え)から同じ計算で同じ答えを出し直せるからである。これは Temporal の『決定的なワークフロー + activity の結果の記録』、rr の『非決定の入力だけを記録する』と同じ考え方で、非決定(時計・乱数・I/O)をすべて境目の外(土台の handler)へ押し出せば、境目の記録だけで全体を再生できる。")]
  :decision
    [(rule R1 "doeff-cluster の job が受け取るのは Program の値 1 つ(defk の関数を呼んだ結果)だけ。Program と handler の組を別々に受け取らない。handler は Program の中の with-handlers で与える。")
     (rule R2 "実行器 job_entry は既定の handler を 1 つも足さない。今の scheduled の包みと env の関数(--env)の包みも外す。")
     (rule R3 "service と task は同じ API(Program の値 1 つ)で起こす。service だけの --factory・--config の入口はやめる。")
     (rule R3b "宣言の中の Program の値は、task と同じく詰めた文字列(encode-program / decode-program)で運ぶ。service と task で運び方を分けない(operator 逐語 \"i dont find any reason to have different api for services\")。読みやすさは declare の表示で補う — 詰めた Program を解いて、呼んだ関数の名と引数を印字する。handler の値は宣言に入れず、defk の本体の中(with-handlers)で作る。決めた経緯は agora-redesign #829 の決定のコメント。戻し方: 関数の参照(module:attr)+ 引数の JSON を宣言に持ち、runner がその場で呼んで Program を作る形へ戻す(2026-09-27 追加)。")
     (rule R4 "service の宣言の :env・:config・:env-config をやめる。設定は Program の中の Ask と、それに答える os.environ を読む handler(Program の側で並べる)で読む。宣言が process へ渡す環境変数は、宣言の値として持つ。")
     (rule R4b "Program の宣言が書くのは必要な能力の名前(:needs #{\"claude-cli\" \"pg-network\"} など)だけで、置き場所の名前(kind=k3s・role=agent-exp・機体の名前)は書かない。worker が提供する能力はクラスタの設定(コードの外)で名乗り、coordinator は 必要 ⊆ 提供 の worker に置く。土台の handler も自分の要る能力を :needs で名乗り、それが Program の宣言の :needs に含まれていなければ doeff-linter の違反とする。会社の資格を使う土台は \"company-machine\" を名乗り、会社の機体の worker だけがそれを提供する — 会社の資格の境界を置き場所の仕組みが保証する。今の :requires(worker のラベルとの等しさの照合)は :needs に置き換える。戻し方: :needs を :requires(鍵と値の組)へ戻し、labels-satisfy の照合へ戻す(2026-09-27 追加)。")
     (rule R7 "旧い宣言(service の :env・:config・:env-config・:requires、env の関数、declare の --config)を受け付ける移行の期間は置かない。新しい API(Program の値 1 つ・:needs)だけにし、旧い形は宣言の時点で理由つきで断る(黙って読み替えない)。利用者(agora-controllers)の書き直しは同じ切り替えで行う(agora-redesign #833)。理由: 旧い形と新しい形が並んで通ると、どちらが正しい書き方か、どの宣言がどちらの意味で動いているかが、読み手にも linter にも分からなくなる(operator 逐語 \"it just makes everything confusing so\")。戻し方: 旧い欄を受ける読みを足し戻し、新旧を併存させる(この条を足した commit の revert — 旧い宣言は再び断られなくなるが、新しい API の実装は別の便なので残る)(2026-09-27 追加)。")
     (rule R5 "記録と再生は handler で行う(2026-09-27 決定 — 旧文『未決・案 A = Program の側で包む / 案 B = 実行器の観測の口』を置き換える)。形は今の effect-recorder と同じ間に入る handler: 記録は effect を外へ撃ち直して答えを書き留め、継続を再開する。再生は同じ場所で記録から答える。置き場は Program の中の with-handlers で、翻訳の handler と土台の handler の間(外の世界との境目 — 汎用の effect だけを記録する)。記録か再生かは置く handler で選び、どちらを置くかは Ask と os.environ を読む handler で決める。runner は記録係を差し込まない(今の recording-layer と run.config の record 欄をやめる)。WithObserve(見るだけで答えを見ない)はこの用途に使わず、tracing・ログの用途に限る。")
     (rule R5b "記録係より内側(Program 側 — 業務の handler・翻訳の handler)の handler は決定的でなければならない。時計・乱数・I/O を自分で読まず、汎用の effect にして記録係の下の土台の handler で答えさせる。これで記録係に届かない effect は再生でも同じ計算で答え直され、境目の記録だけで再生が成り立つ。守りは doeff-linter の DOEFF106(生の副作用に直に触る定義は土台の層にだけ置く)で、破れは再生の分岐(ReplayDiverged)として出る。scheduler の並行の順番(どの task が先に進むか)は再生で決定的にならないので、live の扱い(順番の突き合わせ)で扱う(2026-09-27 追加)。")
     (rule R6 "移行の間、runner が足している handler の呼び出しは RUNNER-HANDLER-ROSTER の数を超えない(新設は赤)。減らした便は同じ便で台帳を削る。")]
  :laws
    [(law job-entry-adds-no-handler
       :statement "for_all job j (service / task): job_entry が j を走らせる時に足す handler の数 = 0 — j の Program の外で効く handler は無い"
       :counterexamples
         [(counterexample "job_entry が (scheduled (with-handlers (env-handlers …) program)) で包む — Program の外で scheduler と env の handler が効く(2026-09-27 の本線の形)")
          (counterexample "runner が『既定で』時計や記録係を足す — Program を読んでもどの handler が効くか分からない")]
       :enforced-by ["test-adr-doe-cluster-001-runner-handler-ratchet"]
       :wiring "配線済み(2026-09-27・agora-redesign #833 段 3)— 台帳の数は全部 0 で、job_entry が scheduled・env の関数・記録係の包みを 1 つでも書けば針が赤になる")
     (law service-and-task-share-one-entry
       :statement "for_all job j: j の入口の引数は Program の値 1 つ — service と task で入口の形が同じ"
       :counterexamples
         [(counterexample "service は --factory と --config、task は --blob で起こす — 同じ job なのに入口が 2 つある(2026-09-27 の本線の形)")
          (counterexample "service の宣言に :env-config を書いて設定を渡す — 設定は Program の中の Ask と os.environ を読む handler で読める")]
       :enforced-by ["test-adr-doe-cluster-001-one-entry-takes-a-program"]
       :wiring "配線済み(2026-09-27・agora-redesign #833 段 3)— job_entry の service・task・probe の入口が --factory・--env・--config を受けないことを針が検める")
     (law runner-inserts-no-recorder
       :statement "for_all job j: j の effect の記録・再生の handler は j の Program の中の with-handlers(翻訳の handler と土台の handler の間)に在り、runner(job_entry)は記録係を差し込まない — RUNNER-HANDLER-ROSTER の recording-layer は 0"
       :counterexamples
         [(counterexample "job_entry が run.config の record 欄を見て、env の handler の一番内側に recording-handler を足す(2026-09-27 の本線の形)— 記録の有無が Program から読めない")
          (counterexample "記録に WithObserve を使う — 見るだけで答えを見ないので、再生に要る答えが残らない(WithObserve は tracing・ログの用途に限る)")
          (counterexample "記録係を土台の handler の外側に置く — 翻訳の前の業務の effect まで記録し、外の世界との境目の汎用の effect だけを記録する形にならない")]
       :enforced-by ["test-adr-doe-cluster-001-runner-handler-ratchet"]
       :wiring "配線済み(2026-09-27・agora-redesign #833 段 3)— 台帳の recording-layer は 0 で、job_entry が記録係を足せば針が赤になる。記録係を Program の中に置く形(boundary-recorder)は段 4")
     (law programs-declare-capabilities-not-places
       :statement "for_all job j: j の宣言の :needs は能力の名前の集合で、置き場所の名前(kind・role・機体の名前)を含まない ∧ j が置かれた worker w について needs(j) ⊆ provides(w) ∧ for_all j の Program が並べる土台の handler f: needs(f) ⊆ needs(j) ∧ (\"company-machine\" ∈ provides(w) ⇔ w は会社の機体)"
       :counterexamples
         [(counterexample "service の宣言に :requires {\"kind\" \"k3s\"} を書く — Program が要る物ではなく置き場所を名指し、同じ能力を持つ別の置き場所へ動かせない(2026-09-27 の本線の形)")
          (counterexample "agora の宣言に role=agent-exp を書いて実験用の namespace を選ぶ — 置き場所の選び方がコードに入る")
          (counterexample "会社の資格を読む土台の handler を並べた Program が :needs に company-machine を書かない — 会社でない機体の worker に置かれ、会社の資格の境界が破れる(linter の違反)")]
       :enforced-by ["coordinator の置き方 cluster_policy.placeable(packages/doeff-cluster/tests/test_cluster_policy.hy)"
                     "declare の土台の :needs の検め service_model.foundation-needs-refusal(packages/doeff-cluster/tests/test_service_declaration.hy)"
                     "doeff-linter(土台が並べる handler の :needs の照らし — 規則の番号は未定)"]
       :wiring "一部配線(2026-09-28・agora-redesign #833)— :needs の宣言(defk・defhandler・defsystem)・coordinator の needs ⊆ provides ∪ derived の置き方・company-machine を worker の自己申告でなく node の label から導く所・declare の土台の :needs ⊆ job の :needs は実装と検がある。土台が並べる handler の :needs の照らし(linter)は未配線")
     (law old-declarations-are-refused
       :statement "for_all 宣言 d: d が旧い欄(:env・:config・:env-config・:requires)か env の関数か declare の --config を使う ⇒ 宣言の時点で理由の文つきで断られる(新しい API への読み替えも、警告だけで通すことも無い)"
       :counterexamples
         [(counterexample "移行の間だけ :requires を :needs に読み替えて通す — 同じ系に新旧の宣言が並び、どちらの意味で置かれたかが宣言から読めない")
          (counterexample "旧い :env を受けて警告を出すだけにする — 警告は読まれず、旧い形が残り続ける")
          (counterexample "doeff-cluster だけ先に切り替え、agora-controllers の書き直しを後の便に回す — 本線の利用者が壊れた宣言のまま残る(同じ切り替えで行う)")]
       :enforced-by ["packages/doeff-cluster/tests/test_old_declarations.hy ほか — 旧い形を断る入口ごとの反例(宣言の構成子・defsystem・declare の CLI・資源の口・PUT /jobs・保存の読み直し・heartbeat・worker の起動・task の本文・効果の構成子・job_entry・replay_main・probe)"]
       :wiring "配線済み(2026-09-28・agora-redesign #833)— doeff-cluster の旧い形を断る入口の全部に反例の検がある。agora-controllers の宣言の CLI の入口は書き直し(#834)で同じく断る")
     (law handlers-inside-the-recorder-are-deterministic
       :statement "for_all 記録係より内側の handler h: h は時計・乱数・I/O を直に読まない(非決定の入力は汎用の effect として記録係の下の土台の handler が答える)— よって for_all 記録 r: r を再生した計算は、記録係に届かない effect についても記録の時と同じ答えを出し、ReplayDiverged を上げない(scheduler の並行の順番は live の扱いの突き合わせの外)"
       :counterexamples
         [(counterexample "翻訳の handler が time.time() を直に読んで答えを作る — 記録係に届かないので記録に残らず、再生で別の時刻になり分岐する")
          (counterexample "業務の handler が random で抽選する — 再生で別の値になり、以降の effect の列が記録と食い違う(ReplayDiverged)")
          (counterexample "Program 側の handler が file を直に読む — 再生の時の file の中身が記録の時と違えば答えが変わる")]
       :enforced-by ["doeff-linter DOEFF106"]
       :wiring "未配線(2026-09-27)— DOEFF106 は doeff-linter に未着地(wt/hy-lint-visibility ほか)。破れは実行時に再生の分岐(ReplayDiverged)として出るが、それは事後の検出で針ではない")]
  :enforcement
    [(deftest test-adr-doe-cluster-001-runner-handler-ratchet
       ;; 針: job_entry が Program の外から handler を足す呼び出しは台帳を超えない(新設は赤)・台帳の削り忘れも赤。
       (val repo-root (. (Path __file__) parent parent parent))
       (val text (.read-text (/ repo-root JOB-ENTRY) :encoding "utf-8"))
       (val counts (run (count-runner-handler-sites text)))
       (val grown (lfor [name n] (sorted (.items counts))
                         :if (> n (get RUNNER-HANDLER-ROSTER name))
                         f"{name}: {n} > 台帳 {(get RUNNER-HANDLER-ROSTER name)}"))
       (assert (= grown [])
               (+ "job_entry が Program の外から足す handler を増やした(ADR-DOE-CLUSTER-001 R2・R6 — "
                  "handler は Program の中の with-handlers で与える): " (str grown)))
       (val stale (lfor [name n] (sorted (.items counts))
                         :if (< n (get RUNNER-HANDLER-ROSTER name))
                         f"{name}: 実体 {n} < 台帳 {(get RUNNER-HANDLER-ROSTER name)}"))
       (assert (= stale [])
               (+ "台帳の削り忘れ(ADR-DOE-CLUSTER-001 R6 — 減らした便は RUNNER-HANDLER-ROSTER を同じ便で削る): "
                  (str stale))))
     (deftest test-adr-doe-cluster-001-one-entry-takes-a-program
       ;; 針: job_entry の入口は Program の値 1 つだけを受ける — 旧い入口の引数(--factory・--env・--config)が本文に在れば赤(R1・R3)。
       (val repo-root (. (Path __file__) parent parent parent))
       (val text (.read-text (/ repo-root JOB-ENTRY) :encoding "utf-8"))
       (val old (lfor flag ["--factory" "--env" "--config"]
                      :if (re.search (+ r"add-argument\s+\w+\s+\"" (re.escape flag) "\"") text)
                      flag))
       (assert (= old []) (+ "job_entry が旧い入口の引数を受けている(ADR-DOE-CLUSTER-001 R1・R3): " (str old))))
     (deftest test-adr-doe-cluster-001-ratchet-measure
       ;; 物差しの実演: 包みの呼び出しは数え、同じ名の定義や別の名は数えない。
       (val sample (+ "(defn env-handlers [env config ctx] None)\n"
                       "(run (scheduled (with-handlers (env-handlers e {} ctx) program)))\n"
                       "(recording-layers x)\n"))
       (assert (= (run (count-runner-handler-sites sample))
                  {"scheduled" 1 "env-handlers" 1 "recording-layer" 0})))]
  :plans ["agora-redesign #829"])
