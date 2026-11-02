;;; 作業場所の volume(effect の型は doeff_cluster._shared.intent.workspace_volume_model)の純粋な判断 — I/O は呼び手(本番 =
;;; doeff_cluster._shared.protocol.workspace_volumes・模擬 = doeff_cluster._sim.workspace_volumes)が行う。
;;;
;;;   NAME-PREFIX ほか      volume の名の頭と長さの上限(deploy/k8s/workspace-volumes/workspace-volume.json の namePrefix・nameMaxLength と
;;;                         同じ値 — 検が縛る。umount と detach が API server に問わずに名を決めるため、ここに持つ)
;;;   owner-refusal         持ち主の id が名の条件(英字・数字・-・頭と尻は英字か数字・37 字まで)を外れる訳(外れなければ None)
;;;   volume-name           持ち主の id → volume の名(ws- + 小文字)。Longhorn の volume と attach の ticket の印に同じ名を使う
;;;   ticket-id             volume の名 → この答え手が VolumeAttachment に置く ticket の id(longhorn-api-<名>)
;;;   mount-entries         mountinfo の字 → mount の行の組(path の 8 進の escape を戻す)
;;;   mounted-at            mount の行の組と path → その path に一番上に付いている物(無ければ None)
;;;   volume-mount-points   mount の行の組と volume の名 → その volume の device が付いている path の組
;;;   attach-step           Longhorn の様子 → attach の次の一手(ticket を置く・待つ・付いた・別の node に付いている)
;;;   detach-step           Longhorn の様子 → detach の次の一手(ticket を外す・待つ・外れた・別の node に付いている)
;;;
;;; device の見分け方: mount の係は attach した volume を host の /dev/longhorn/<名> の device で付ける(係の /host/dev/longhorn/<名>)。
;;; mountinfo の source はその path なので、dir の名が longhorn で file の名が volume の名の source を、その volume の mount と読む。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defenum defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})
(import dataclasses [dataclass])  ; defrecord の展開が使う
(import enum [StrEnum])  ; defenum の展開が使う
(import posixpath)
(import re)

;; 名の頭と長さの上限(workspace-volume.json と同じ — Longhorn は 40 字を越える名を縮めて別の名にする)。
(val NAME-PREFIX "ws-")
(val NAME-MAX-LENGTH 40)
;; 持ち主の id の字: 英字・数字・-(頭と尻は英字か数字 — 名の尻が - だと k8s の名に成らない)。大文字と小文字は区別しない。
(val OWNER-SPELLING (re.compile r"^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?$"))
;; Longhorn の API の attach の ticket の種類(Longhorn の UI と REST の attach と同じ種類 — CSI の ticket と別に数えられる)。
(val TICKET-TYPE "longhorn-api")
;; attach した volume の device の dir の名(host の /dev/longhorn)。
(val DEVICE-DIR-NAME "longhorn")
;; mountinfo の path の 8 進の escape(空白・tab・改行・\)。
(val MOUNTINFO-ESCAPE (re.compile r"\\([0-7]{3})"))


(defenum AttachStep PUT WAIT READY ELSEWHERE)
(defenum DetachStep REMOVE WAIT GONE ELSEWHERE)


(defrecord MountEntry
  "mountinfo の行 1 つ: mount-point = 付いている path・source = 付けた物(block device なら device の path)・fs-type = file system の型。"
  (#^ str mount-point)
  (#^ str source)
  (#^ str fs-type))


(defrecord AttachView
  "Longhorn の volume 1 つの attach の様子(VolumeAttachment と Volume の読み): attachment = VolumeAttachment が在るか(volume を作った
   直後は Longhorn がまだ作っていない)・ticket-node = この答え手の ticket の node(None = ticket が無い)・satisfied と message = その
   ticket の status(満たされたか・Longhorn の訳)・state と current-node = Volume の status の state と currentNodeID(読めなければ空)・
   other-tickets = ほかの持ち主の ticket の id(backup・CSI など — 名の順)。"
  (#^ bool attachment)
  (#^ (| str None) ticket-node)
  (#^ bool satisfied)
  (#^ str message)
  (#^ str state)
  (#^ str current-node)
  (#^ (get tuple #(str ...)) other-tickets))


(defk owner-refusal [owner]
  {:pre [(: owner str)] :post [(: % (| str None))]}
  "持ち主の id owner が volume の名の条件を外れる訳を得るため(外れなければ None — 条件は頭の註。縮めた名や重なった名を作らない)。"
  (val limit (- NAME-MAX-LENGTH (len NAME-PREFIX)))
  (cond
    (is (.match OWNER-SPELLING owner) None)
    (+ "持ち主の id '" owner "' が英字・数字・- の外か、頭か尻が -")
    (> (len owner) limit)
    (.format "持ち主の id '{}' が {} 字({} 字まで — volume の名は {} 字まで)" owner (len owner) limit NAME-MAX-LENGTH)
    True None))


(defk volume-name [owner]
  {:pre [(: owner str)] :post [(: % str) (<= (len %) NAME-MAX-LENGTH)]}
  "持ち主の id owner(条件を満たす物)の volume の名を得るため(頭 NAME-PREFIX + 小文字)。"
  (+ NAME-PREFIX (.lower owner)))


(defk ticket-id [name]
  {:pre [(: name str) (.startswith name NAME-PREFIX)] :post [(: % str)]}
  "volume の名 name の VolumeAttachment に、この答え手が置く ticket の id を得るため(種類 TICKET-TYPE と名 — Longhorn の REST の attach と
   同じ綴り)。"
  (+ TICKET-TYPE "-" name))


(defk _unescaped [text]
  {:pre [(: text str)] :post [(: % str)]}
  "mountinfo の path の 8 進の escape(\\040 = 空白ほか)を戻すため。"
  (.sub MOUNTINFO-ESCAPE (fn [found] (chr (int (.group found 1) 8))) text))


(defk mount-entries [text]
  {:pre [(: text str)] :post [(: % (get tuple #(MountEntry ...)))]}
  "mountinfo の字 text を mount の行の組へ読むため(書かれた順 — 後の行ほど上に重なる)。形の違う行(区切りの - が無い・欄が足りない)は
   読み飛ばす。"
  (var entries #())
  (for [line (.splitlines text)]
    (val fields (.split line " "))
    (when (and (in "-" fields) (>= (len fields) 7))
      (val dash (.index fields "-"))
      (when (>= (len fields) (+ dash 3))
        (:= entries (+ entries #((MountEntry :mount-point (! (_unescaped (get fields 4)))
                                             :source (! (_unescaped (get fields (+ dash 2))))
                                             :fs-type (get fields (+ dash 1)))))))))
  entries)


(defk mounted-at [entries path]
  {:pre [(: entries (get tuple #(MountEntry ...))) (: path str)] :post [(: % (| MountEntry None))]}
  "mount の行の組 entries のうち、path に一番上に付いている物を得るため(付いていなければ None)。"
  (val at (lfor entry entries :if (= entry.mount-point path) entry))
  (if at (get at -1) None))


(defk volume-of-entry [entry]
  {:pre [(: entry MountEntry)] :post [(: % (| str None))]}
  "mount の行 entry が Longhorn の volume の device の物なら、その volume の名を得るため(頭の註の見分け方 — 違えば None)。"
  (if (= (posixpath.basename (posixpath.dirname entry.source)) DEVICE-DIR-NAME)
      (posixpath.basename entry.source)
      None))


(defk volume-mount-points [entries name]
  {:pre [(: entries (get tuple #(MountEntry ...))) (: name str)] :post [(: % (get tuple #(str ...)))]}
  "mount の行の組 entries のうち、volume name の device が付いている path を、書かれた順に重ねずに並べるため。"
  (var points #())
  (for [entry entries]
    (<- found (| str None) (volume-of-entry entry))
    (when (and (= found name) (not-in entry.mount-point points))
      (:= points (+ points #(entry.mount-point)))))
  points)


(defk attach-step [view node]
  {:pre [(: view AttachView) (: node str)] :post [(: % AttachStep)]}
  "volume の attach の様子 view から、node へ attach する次の一手を決めるため: VolumeAttachment がまだ無い → WAIT・ticket が無い → PUT・
   ticket が別の node → ELSEWHERE(外さずに断る — 別の機体で使っているかもしれない)・ticket が満たされ volume が node に付いた → READY・
   ほか → WAIT。"
  (cond
    (not view.attachment) AttachStep.WAIT
    (is view.ticket-node None) AttachStep.PUT
    (!= view.ticket-node node) AttachStep.ELSEWHERE
    (and view.satisfied (= view.state "attached") (= view.current-node node)) AttachStep.READY
    True AttachStep.WAIT))


(defk detach-step [view node]
  {:pre [(: view AttachView) (: node str)] :post [(: % DetachStep)]}
  "volume の attach の様子 view から、node から detach する次の一手を決めるため: ticket が別の node → ELSEWHERE(外さずに断る)・ticket が
   node → REMOVE・ticket が無く、ほかの ticket も無いのに volume がまだ node に付いている(付け・外しの途中)→ WAIT・ほか → GONE(この
   答え手の分は外れた — ほかの持ち主の ticket が付けている間は Longhorn が付けたまま置く)。"
  (cond
    (and (is-not view.ticket-node None) (!= view.ticket-node node)) DetachStep.ELSEWHERE
    (= view.ticket-node node) DetachStep.REMOVE
    (and (not view.other-tickets) (in view.state #("attaching" "attached" "detaching")) (in view.current-node #(node "")))
    DetachStep.WAIT
    True DetachStep.GONE))
