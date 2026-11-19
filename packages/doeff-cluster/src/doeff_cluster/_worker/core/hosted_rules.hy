;;; 預かりの process(shared/intent/hosted_model — worker が job の process の木の外に起こす process)の判断: 置き場と口の path・id・層の
;;; 選び・持ち主の有無。I/O を持たない — 起こす・止める・覚え直すのは宿 worker/protocol/hosted_host。
;;;
;;; 置き場: 世代をまたいで分け合う dir(--shared-dir — 同じ機体の worker の 2 つの世代が並ぶ間も、次の世代が覚え直せる)の下の
;;; hosted/<id>。中に worker の記録(RECORD-NAME — id・pid・起こした刻の印・持ち主・層)と出力の file を置き、process の cwd にもする
;;; (process は socket や spool をここに置く)。worker が起き直したら、この dir を読んで覚え直す(待ちの子の置き場と同じ向き —
;;; warm_rules の warm-place)。
;;;
;;; 口: worker の世代ごとの unix socket。path は短い根の下(warm_rules の WARM-SOCKET-ROOT と同じ訳 — 分け合う dir の長さに依らず unix
;;; socket の上限の内)。job の子へ環境変数 SOCKET-VAR で渡す(job は自分を起こした世代の口へ頼む)。
;;;
;;; 持ち主の有無(hosted-owners): 持ち主の job の名が、この worker の最後に読めた宣言に在るか、この worker の job の process(退いた
;;; process を含む)が終わらずに居れば、持ち主は居る。どちらも無ければ居ない — 宣言から消え、process も全部終わった(居なくなった
;;; process の client は持ち主の job の process だけなので、client も無い)。時間の上限では判じない。job の process が落ちて起こし直しを
;;; 待つ間・版の入れ替えの間は宣言に名が在るので止めない。
(require doeff-hy.macros [defk val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import hashlib)
(import re)
(import doeff_cluster._shared.intent.hosted_model [HostedRefused])
(import doeff_cluster._worker.core.warm_rules [SOCKET-PATH-LIMIT WARM-SOCKET-ROOT])

;; 分け合う dir の下の置き場の根の名。
(val HOSTED-DIR "hosted")
;; id の形(h- と 16 進 16 字 — 64 bit の乱数)。置き場の dir の名にそのまま入るので、形の外の名は頼みでも走査でも受けない。
(val ID-PREFIX "h-")
(val ID-BYTES 8)
(val ID-PATTERN (re.compile "h-[0-9a-f]{16}"))
;; 置き場の中の worker の記録と、process の標準出力・標準エラーの file の名。
(val RECORD-NAME "hosted.json")
(val OUTPUT-NAME "output.log")
;; process へ渡す環境変数の名(置き場の dir と id)。
(val PLACE-VAR "DOEFF_HOSTED_PLACE")
(val ID-VAR "DOEFF_HOSTED_ID")
;; job の子へ渡す、worker の口の socket の path の環境変数の名。
(val SOCKET-VAR "DOEFF_WORKER_HOSTED_SOCKET")
;; 止めの TERM から KILL までの猶予(ms)。
(val STOP-GRACE-MS 5000)
;; 口の socket の dir の名に入れる、置き場の根の sha256 の頭の桁数(warm_rules の WARM-SOCKET-DIGITS と同じ 96 bit)。
(val SOCKET-DIGITS 24)


(defrecord HostedRecord
  "置き場の中の worker の記録 1 つ(起こした世代が書き、起き直した世代が読んで覚え直す): id・pid・start-ticks = process の起こした刻の印
   (machine の process の表の start-ticks — pid の使い回しを見分け、別の process へ合図を送らない)・owner = 持ち主の job の名・layer =
   置いた層の名(None = 置き分けていない)・place = 置き場の dir・started-ms = 起こした刻(epoch ms)・generation = 起こした worker の世代の印。"
  {:tags {:context "worker" :role "type"}}
  (#^ str id)
  (#^ int pid)
  (#^ int start-ticks)
  (#^ str owner)
  (#^ (| str None) layer)
  (#^ str place)
  (#^ int started-ms)
  (#^ str generation))


(defk hosted-root-of [shared]
  {:pre [(: shared str)] :post [(: % str)] :tags {:context "worker" :role "judgment"}}
  "分け合う dir(--shared-dir)から預かりの process の置き場の根を導くため(頭の註)。"
  (+ (.rstrip shared "/") "/" HOSTED-DIR))


(defk hosted-socket-dir-of [root]
  {:pre [(: root str)] :post [(: % str)] :tags {:context "worker" :role "judgment"}}
  "置き場の根から、口の socket を置く持ち主だけの dir(<短い根>/doeff-hosted-<根の sha256 の頭 24 桁>)を導くため。"
  (.format "{}/doeff-hosted-{}" WARM-SOCKET-ROOT (cut (.hexdigest (hashlib.sha256 (.encode root "utf-8"))) 0 SOCKET-DIGITS)))


(defk hosted-socket-of [root generation]
  {:pre [(: root str) (: generation str) (> (len generation) 0)] :post [(: % str)] :tags {:context "worker" :role "judgment"}}
  "置き場の根と worker の世代の印から、その世代の口の socket の path を導くため(同じ機体に並ぶ 2 つの世代は別の口を持つ)。上限を
   越える組み立ては ValueError で名指して断る(起動を断る — 黙って bind の断りまで運ばない)。"
  (val path (.format "{}/{}.sock" (! (hosted-socket-dir-of root)) generation))
  (val size (len (.encode path "utf-8")))
  (when (> size SOCKET-PATH-LIMIT)
    (raise (ValueError (.format "預かりの口の socket の path が {} byte で、unix socket の上限 {} byte を越える: {}" size SOCKET-PATH-LIMIT path))))
  path)


(defk hosted-id-of [noise]
  {:pre [(: noise bytes) (= (len noise) ID-BYTES)] :post [(: % str)] :tags {:context "worker" :role "judgment"}}
  "乱数の byte から預かりの process の id(h- と 16 進 16 字)を作るため。"
  (+ ID-PREFIX (.hex noise)))


(defk hosted-id? [text]
  {:pre [(: text str)] :post [(: % bool)] :tags {:context "worker" :role "judgment"}}
  "文字列が id の形か(頼みの id と走査の dir の名を、置き場の外を指さない形だけに絞るため)。"
  (is-not (.fullmatch ID-PATTERN text) None))


(defk hosted-place-of [root id]
  {:pre [(: root str) (: id str)] :post [(: % str)] :tags {:context "worker" :role "judgment"}}
  "置き場の根と id から 1 本の置き場の dir を導くため(形の外の id は ValueError — 置き場の外を指さない)。"
  (when (not (! (hosted-id? id)))
    (raise (ValueError (.format "預かりの process の id の形でない: {!r}" id))))
  (+ root "/" id))


(defk hosted-layer-procs [layers layer]
  {:pre [(: layers (| dict None)) (: layer (| str None))] :post [(: % (| str None HostedRefused))] :tags {:context "worker" :role "judgment"}}
  "頼みの層の名から、移る先の cgroup.procs の path を選ぶため。layers = 層の名 → cgroup.procs(None = 層の宣言の無い worker — native の
   機体・cgroup の無い macOS)。層を頼まない頼みと、層の宣言の無い worker では None(今の置き方のまま起こす — 頼み手の今の形
   〔層の dir が無ければ今の cgroup のまま〕と同じ)。宣言の在る worker で知らない層は断る(置けない process を黙って別の層で走らせない)。"
  (cond
    (or (is layer None) (is layers None)) None
    (in layer layers) (get layers layer)
    True (HostedRefused :detail (.format "この worker に cgroup の層 {!r} が無い(在る層: {})" layer (.join "," (sorted layers))))))


(defk hosted-owners [jobs processes]
  {:pre [(: jobs tuple) (: processes tuple)] :post [(: % frozenset)] :tags {:context "worker" :role "judgment"}}
  "持ち主が居る job の名の集まりを作るため(頭の註): jobs = 最後に読めた宣言の job(JobSpec)・processes = この worker の job の
   process の観測(ProcessView — 終わっていない物の名と、退いた process の元の名)。"
  (frozenset (+ (tuple (gfor spec jobs spec.name))
                (tuple (gfor view processes :if (is view.exit-code None) view.name))
                (tuple (gfor view processes :if (and (is view.exit-code None) (is-not view.retired-from None)) view.retired-from)))))


(defk hosted-orphans [records owners]
  {:pre [(: records tuple) (: owners frozenset)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "持ち主の居ない預かりの process の id を並べるため。records = 覚えている記録(HostedRecord)の列・owners = hosted-owners の答え。"
  (tuple (gfor record records :if (not-in record.owner owners) record.id)))
