;;; 作業場所の volume(effect の型は doeff_cluster._shared.intent.workspace_volume_model)の純粋な判断 — I/O は呼び手(本番 =
;;; doeff_cluster._shared.protocol.workspace_volumes・模擬 = doeff_cluster._sim.workspace_volumes)が行う。
;;;
;;;   NAME-PREFIX ほか      volume の名の頭と長さの上限(上に載る系の宣言の workspace-volumes/workspace-volume.json の namePrefix・nameMaxLength と
;;;                         同じ値 — 検が縛る。umount と detach が API server に問わずに名を決めるため、ここに持つ)
;;;   owner-refusal         持ち主の id が名の条件(英字・数字・-・頭と尻は英字か数字・37 字まで)を外れる訳(外れなければ None)
;;;   volume-name           持ち主の id → volume の名(ws- + 小文字)。Longhorn の volume と attach の ticket の印に同じ名を使う
;;;   ticket-id             volume の名 → この答え手が VolumeAttachment に置く ticket の id(longhorn-api-<名>)
;;;   mount-entries         mountinfo の字 → mount の行の組(path の 8 進の escape を戻す)
;;;   mounted-at            mount の行の組と path → その path に一番上に付いている物(無ければ None)
;;;   mount-hold            mount の行 1 つ・volume の名・その volume の今の device の major:minor → 付いている(HELD)・古い mount(STALE)・
;;;                         別の物(OTHER)(下の註の「付いている」の定義)
;;;   device-read           mount の係の頼み device の答えの訳 → volume の今の device の major:minor(device が無ければ None・形が違えば None
;;;                         の答え)
;;;   volume-holds          mount の行の組と volume の名と今の device → その volume が付いている(HELD)か古い mount(STALE)の path の組
;;;   attach-step           Longhorn の様子 → attach の次の一手(ticket を置く・待つ・付いた・別の node に付いている)
;;;   detach-step           Longhorn の様子 → detach の次の一手(ticket を外す・待つ・外れた・別の node に付いている)
;;;   using-tickets         VolumeAttachment の ticket の id の組 → Longhorn の手順の外の ticket(使っている印 — 冷やす側の snapshot と backup の
;;;                         ticket と戻しの ticket を除く)
;;;   cooling-name          volume の名と刻 → 冷やす時の snapshot と backup の名(<名>-cool-<epoch 秒>)
;;;   growing-name          volume の名と刻 → 広げた直後に取って消す snapshot の名(<名>-grow-<epoch 秒>)
;;;   first-name            volume の名と刻 → 1 度も backup の無い付いた volume に取る最初の snapshot と backup の名(<名>-first-<epoch 秒>)
;;;   first-backup-due?     束の lastBackupName の組と Backup の組 → 最初の backup を取るか(束が何も指さず、失敗した物の外に Backup が無い)
;;;   newest-backup         backup の組 → 戻す元(完了した物のうち作られたのが一番新しい物 — 無ければ None)
;;;   backup-volume-name    volume の名と backup の先の名 → その束 BackupVolume の名(Longhorn v1.13 と同じ <名>-<hash> — 手で作る束が
;;;                         Longhorn の作る束と同じ名に重なり、2 本にならない)
;;;   room-bytes            札の disk の組と過剰割り当ての % と最小の空きの % と disk ごとの空きの床 → 線ごとの残りの byte(Longhorn の置く
;;;                         線 2 つ — 名目の予約の線と実の空きの線。床は実の空きの線を disk ごとに上げる)
;;;   room-covers?          線ごとの残りの byte と需要の byte → 両方の線が需要を覆うか
;;;   cooling-order         外れている volume の組・今・温かく置く秒・線ごとの残り・需要の byte → 冷やす volume の名(使い終わりの古い
;;;                         順)と、冷やした後に両方の線が需要を覆うか
;;;   restore-node          札の disk の組と過剰割り当ての % と最小の空きの % と disk ごとの空きの床と需要の byte → backup から戻す volume を
;;;                         付ける node(需要を覆う
;;;                         disk のうち名目の残りが一番大きい物の node — 無ければ空)
;;;
;;; device の見分け方: mount の係は attach した volume を host の /dev/longhorn/<名> の device で付ける(係の /host/dev/longhorn/<名>)。
;;; mountinfo の source はその path なので、dir の名が longhorn で file の名が volume の名の source を、その volume の mount の候補と読む
;;; (volume-of-entry)。
;;;
;;; 「volume X が path P に付いている」の定義(1 つ — 上に載る系の mount の係の script も同じ定義で判じる・2026-11-12):
;;; P の一番上の mount の major:minor(mountinfo の 3 つ目の欄)が、今の /dev/longhorn/<X> の major:minor と等しい時だけ HELD。mount 元の
;;; 名が X で番号が違う(device が無い時も)物は STALE — Longhorn の engine が付いたまま起こし直されると /dev/longhorn/<X> は新しい番号で
;;; 作り直され、古い mount は古い device を指したまま残る(ext4 は shutdown して読み書きが I/O error になる・mount 元の字は同じ)。
;;; 名だけで判じると、壊れた mount を付いていると読み、その上で使い始める。答え手の container は host の /dev を持たないので、今の
;;; device の番号は mount の係に読ませる(係の頼み device)。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defenum defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})
(import dataclasses [dataclass])  ; defrecord の展開が使う
(import enum [StrEnum])  ; defenum の展開が使う
(import hashlib)
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
;; device の番号の字(major:minor — mountinfo の 3 つ目の欄と stat の %Hr:%Lr と同じ綴り)。
(val DEVICE-NUMBER (re.compile r"[0-9]+:[0-9]+"))
;; mountinfo の path の 8 進の escape(空白・tab・改行・\)。
(val MOUNTINFO-ESCAPE (re.compile r"\\([0-7]{3})"))
;; 使い手が volume に置く刻の印(annotation・epoch 秒の字)。attach は ticket を置いた後に、detach は ticket を外した後に書く。冷やす側は
;; これを使い終わりの刻と読み、volume を消す時はこの書きの後の版でなければ消さない(冷やす途中の attach に負ける — README の節「寿命」)。
(val USED-AT-KEY "doeff-cluster/used-at")
;; 冷やす手順の snapshot と backup のために Longhorn が置く ticket の id の頭(外れている volume を、frontend を問わずに付ける)。
(val COOLING-TICKET-PREFIXES #("snapshot-controller-" "backup-controller-"))
;; backup から戻す間(restoreRequired の間 — 失敗した戻しにも残る)に Longhorn が置く ticket の id の頭(Longhorn v1.13 の volume restore controller)。
(val RESTORE-TICKET-PREFIX "volume-restore-controller-")
;; 冷やす時の snapshot と backup の名の中の印。
(val COOLING-MARK "-cool-")
;; 広げた直後に取って消す snapshot の名の中の印(README の節「広げる」の「expand の snapshot を畳む」)。
(val GROWING-MARK "-grow-")
;; 1 度も backup の無い付いた volume に取る最初の snapshot と backup の名の中の印(README の節「backup」の「束を中身の無いまま置かない」)。
(val FIRST-MARK "-first-")
;; backup の様子のうち、戻す元にできる物。
(val BACKUP-COMPLETED "Completed")
;; backup の様子のうち、失敗して backup の先に何も足さない物。
(val BACKUP-ERROR "Error")
;; 束 BackupVolume の名の尻の hash の字数(Longhorn v1.13 の util.RandomIDLength — types.GetBackupVolumeNameFromVolumeName が切る長さ)。
(val BUNDLE-HASH-LENGTH 8)


(defenum AttachStep PUT WAIT READY ELSEWHERE)
(defenum DetachStep REMOVE WAIT GONE ELSEWHERE)
;; mount の行 1 つと volume の関わり(頭の註の「付いている」の定義): HELD = 今の device が付いている・STALE = 名は volume の device だが番号が
;; 今の device と違う古い mount・OTHER = 別の物。
(defenum MountHold HELD STALE OTHER)


(defrecord MountEntry
  "mountinfo の行 1 つ: mount-point = 付いている path・source = 付けた物(block device なら device の path)・fs-type = file system の型・
   device = file system の device の major:minor(mountinfo の 3 つ目の欄 — block device の file system なら付けた device の番号)。"
  (#^ str mount-point)
  (#^ str source)
  (#^ str fs-type)
  (setv #^ str device ""))


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


(defrecord BackupView
  "Longhorn の Backup 1 つの読み: name = Backup の名・state = 様子(Completed・InProgress・Error ほか)・created-at = status.backupCreatedAt
   (ISO 8601 の UTC の字 — 字の順が刻の順。まだ無ければ空)・url = 戻す元の印(Volume の spec.fromBackup に書く字)・volume-size =
   status.volumeSize(backup を取った時の volume の名目の大きさ byte — 戻す volume はこれより小さく作れない。読めなければ 0)。"
  (#^ str name)
  (#^ str state)
  (#^ str created-at)
  (#^ str url)
  (setv #^ int volume-size 0))


(defrecord DiskRoom
  "札の disk 1 つの置き場の数(byte): maximum = storageMaximum・reserved = storageReserved・scheduled = storageScheduled(置いた volume の
   名目の大きさの和)・available = storageAvailable(file system の実の空き)・node = disk の在る node の名・disk = Longhorn の Node の
   disk の名(空きの床を引く鍵)。"
  (#^ int maximum)
  (#^ int reserved)
  (#^ int scheduled)
  (#^ int available)
  (setv #^ str node "")
  (setv #^ str disk ""))


(defrecord RoomBytes
  "札の disk の組の置き場の残り(byte — 負 = 線を越えて置かれている): nominal = 名目の予約の線の残り・actual = 実の空きの線の上の量。"
  (#^ int nominal)
  (#^ int actual))


(defrecord WarmVolume
  "外れている(温かい)volume 1 つ: name = volume の名・used-at = 使い終わりの刻(epoch 秒 — 印 USED-AT-KEY が無ければ作られた刻)・
   size = 名目の大きさ(spec.size の byte — 冷やせば名目の予約の線がこれだけ空く)・actual = 実の使用(status.actualSize の byte —
   冷やせば実の空きの線がこれだけ空く)。"
  (#^ str name)
  (#^ float used-at)
  (setv #^ int size 0)
  (setv #^ int actual 0))


(defrecord CoolingPlan
  "冷やす順の判断(cooling-order)の答え: names = 冷やす volume の名(冷やす順)・covered = 冷やした後に両方の線が需要を覆うか。"
  (#^ (get tuple #(str ...)) names)
  (#^ bool covered))


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
                                             :fs-type (get fields (+ dash 1))
                                             :device (get fields 2))))))))
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


(defk mount-hold [entry name device]
  {:pre [(: entry MountEntry) (: name str) (: device (| str None))] :post [(: % MountHold)]}
  "mount の行 entry と volume name の関わりを、頭の註の「付いている」の定義で得るため: entry の major:minor が name の今の device の番号
   device(None = device が無い — attach されていない)と等しければ HELD・mount 元の名が name なら STALE・ほかは OTHER。"
  (<- found (| str None) (volume-of-entry entry))
  (cond
    (and (is-not device None) (= entry.device device)) MountHold.HELD
    (= found name) MountHold.STALE
    True MountHold.OTHER))


(defrecord DeviceRead
  "mount の係の頼み device の答え: number = volume の今の device の major:minor(None = device が無い — この node に attach されていない)。"
  (#^ (| str None) number))


(defk device-read [notes]
  {:pre [(: notes str)] :post [(: % (| DeviceRead None))]}
  "mount の係の頼み device の答えの訳 notes から、device の番号を得るため(行 `device <major>:<minor>` か、device が無い時の `device -` —
   その行が無いか形が違えば None)。"
  (val found (lfor line (.splitlines notes) :if (.startswith line "device ") (.strip (cut line (len "device ") None))))
  (cond
    (not found) None
    (= (get found 0) "-") (DeviceRead :number None)
    (is-not (re.fullmatch DEVICE-NUMBER (get found 0)) None) (DeviceRead :number (get found 0))
    True None))


(defk volume-holds [entries name device]
  {:pre [(: entries (get tuple #(MountEntry ...))) (: name str) (: device (| str None))]
   :post [(: % (get tuple #((get tuple #(str MountHold)) ...)))]}
  "mount の行の組 entries のうち、mount 元の名が volume name の物(volume-of-entry — 係が付けた mount の候補)を、path と関わり(HELD か
   STALE — mount-hold・device は name の今の device の番号)の組にして、書かれた順に重ねずに並べるため。"
  (var holds #())
  (for [entry entries]
    (<- found (| str None) (volume-of-entry entry))
    (<- hold MountHold (mount-hold entry name device))
    (when (and (= found name) (not-in #(entry.mount-point hold) holds))
      (:= holds (+ holds #(#(entry.mount-point hold))))))
  holds)


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


(defk using-tickets [ids]
  {:pre [(: ids (get tuple #(str ...)))] :post [(: % (get tuple #(str ...)))]}
  "VolumeAttachment の ticket の id の組 ids のうち、Longhorn が冷やす手順の snapshot と backup・戻しのために置く ticket でない物(使っている印 —
   この答え手の ticket・CSI・ほかの持ち主)を、名の順に得るため。"
  (val own (+ COOLING-TICKET-PREFIXES #(RESTORE-TICKET-PREFIX)))
  (tuple (sorted (gfor id ids :if (not (any (gfor prefix own (.startswith id prefix)))) id))))


(defk cooling-name [name stamp]
  {:pre [(: name str) (.startswith name NAME-PREFIX) (: stamp int) (>= stamp 0)] :post [(: % str) (<= (len %) 63)]}
  "volume name を刻 stamp(epoch 秒)に冷やす時の snapshot と backup の名を得るため(<名>-cool-<刻> — 名は 40 字までなので k8s の名の 63 字に
   収まる)。"
  (+ name COOLING-MARK (str stamp)))


(defk growing-name [name stamp]
  {:pre [(: name str) (.startswith name NAME-PREFIX) (: stamp int) (>= stamp 0)] :post [(: % str) (<= (len %) 63)]}
  "volume name を刻 stamp(epoch 秒)に広げた直後に取って消す snapshot の名を得るため(<名>-grow-<刻> — 名は 40 字までなので k8s の名の
   63 字に収まる)。"
  (+ name GROWING-MARK (str stamp)))


(defk first-name [name stamp]
  {:pre [(: name str) (.startswith name NAME-PREFIX) (: stamp int) (>= stamp 0)] :post [(: % str) (<= (len %) 63)]}
  "1 度も backup の無い付いた volume name に刻 stamp(epoch 秒)に取る最初の snapshot と backup の名を得るため(<名>-first-<刻> — 名は 40 字
   までなので k8s の名の 63 字に収まる)。"
  (+ name FIRST-MARK (str stamp)))


(defk first-backup-due? [last-backups backups]
  {:pre [(: last-backups (get tuple #(str ...))) (: backups (get tuple #(BackupView ...)))] :post [(: % bool)]}
  "volume の束の lastBackupName の組 last-backups と、その volume の Backup の組 backups から、最初の backup を取るかを得るため: 束がどれも
   backup を指さず、Backup が失敗した物(BACKUP-ERROR)の外に無い時だけ取る。途中・完了の Backup が在れば、backup の先に volume の dir が
   もう在る(Longhorn の backupstore は backup の初めに lock と volume.cfg を書く)か、じき在る — 重ねて取らない。"
  (and (not (any last-backups))
       (all (gfor backup backups (= backup.state BACKUP-ERROR)))))


(defk newest-backup [backups]
  {:pre [(: backups (get tuple #(BackupView ...)))] :post [(: % (| BackupView None))]}
  "backup の組 backups のうち、戻す元にする物を得るため: 完了した物(BACKUP-COMPLETED・url が在る)のうち created-at が一番新しい物(同じ刻は
   名の大きい物)。無ければ None。"
  (val done (lfor backup backups :if (and (= backup.state BACKUP-COMPLETED) backup.url backup.created-at) backup))
  (if done (max done :key (fn [backup] #(backup.created-at backup.name))) None))


(defk backup-volume-name [name target]
  {:pre [(: name str) (: target str) name target] :post [(: % str) (.startswith % (+ name "-"))]}
  "volume name の backup の先 target の束 BackupVolume の名を得るため(Longhorn v1.13 の types.GetBackupVolumeNameFromVolumeName と同じ:
   <名>-<字「<名>-<先>」の SHA-256 の 16 進の頭 8 字> — split-brain で束が作り直されないように Longhorn が名を決めている)。同じ名で作れば、
   Longhorn の backup の先の同期が後から同じ束を作ろうとしても AlreadyExists で退き、同じ label の束が 2 本にならない(2 本在ると Longhorn は
   戻しも backup も断る)。"
  (+ name "-" (cut (.hexdigest (hashlib.sha256 (.encode (.strip (.format "{}-{}" name target)) "utf-8"))) 0 BUNDLE-HASH-LENGTH)))


(defk floor-bytes [room minimal floors]
  {:pre [(: room DiskRoom) (: minimal int) (<= 0 minimal 100) (: floors (get dict #(str int)))] :post [(: % int) (>= % 0)]}
  "札の disk 1 枚 room に残す実の空き(byte)を得るため: 最小の空きの %(Longhorn の設定)の量と、その disk の空きの床 floors(値の
   ConfigMap の freeFloorBytes — disk の名 → byte)の大きい方。床は上に載る系の宣言の disk-share/shares.yaml が、disk の image を置く SSD の分け前から導く
   (image の中で Longhorn が使ってよい量を、SSD の分け前に収める — Longhorn の予約 storageReserved は名目の線にだけ効き、実の使用を
   縛らない)。床の無い disk は % の量だけ。"
  (max (// (* room.maximum minimal) 100) (.get floors room.disk 0)))


(defk room-bytes [rooms percentage minimal floors]
  {:pre [(: rooms (get tuple #(DiskRoom ...))) (: percentage int) (>= percentage 0) (: minimal int) (<= 0 minimal 100)
         (: floors (get dict #(str int)))]
   :post [(: % RoomBytes)]}
  "札の disk の組 rooms の置き場の残りを、Longhorn が replica を置く線 2 つごとに byte で得るため(disk ごとの残りを、負も含めて足す —
   級の volume を置く disk が 1 枚の時に正確。複数の disk では、1 本が 1 枚に収まるかを見ない近い数え):
   - 名目の予約の線(設定 storage-over-provisioning-percentage = percentage): (maximum − reserved) × percentage% − scheduled。
   - 実の空きの線(設定 storage-minimal-available-percentage = minimal と disk ごとの空きの床 floors — floor-bytes): available − 残す空き。
     Longhorn は空きが % の線を越える disk にだけ置き、床は worker が冷やす回と作る手の数えで守る。"
  (var actual 0)
  (for [room rooms]
    (<- kept int (floor-bytes room minimal floors))
    (:= actual (+ actual (- room.available kept))))
  (RoomBytes :nominal (sum (gfor room rooms (- (// (* (- room.maximum room.reserved) percentage) 100) room.scheduled)))
             :actual actual))


(defk room-covers? [room demand]
  {:pre [(: room RoomBytes) (: demand int) (>= demand 0)] :post [(: % bool)]}
  "置き場の残り room が、需要 demand(入れたい volume の名目の byte)を両方の線で覆うかを得るため。実の空きの線にも名目の大きさを当てる
   (新しい volume が名目の大きさまで書いても線を割らない — thin の volume の実の使用は名目より小さいので、少なめに数える側)。"
  (and (>= room.nominal demand) (>= room.actual demand)))


(defk cooling-order [warm now warm-seconds room demand]
  {:pre [(: warm (get tuple #(WarmVolume ...))) (: now float) (: warm-seconds float) (>= warm-seconds 0) (: room RoomBytes) (: demand int)
         (>= demand 0)]
   :post [(: % CoolingPlan)]}
  "外れている volume の組 warm から、今 now に冷やす物を、使い終わりの古い順に決めるため: (1) 使い終わりから warm-seconds を過ぎた物は
   全部、(2) 置き場の残り room に (1) の物が空ける byte を足しても線のどちらかが需要 demand に届かなければ、両方の線が届くまで残りを
   古い順に足す(冷やした volume は名目の線に size・実の線に actual を空ける — 1 本を空き 1 本と数えない)。(2) を全部足しても届かなければ
   (1) だけを答える(温かい volume を冷やしても入らない — covered は偽)。同じ刻は名の順。"
  (val ordered (sorted warm :key (fn [volume] #(volume.used-at volume.name))))
  (val stale (lfor volume ordered :if (>= (- now volume.used-at) warm-seconds) volume))
  (val rest (lfor volume ordered :if (< (- now volume.used-at) warm-seconds) volume))
  (var freed (RoomBytes :nominal (+ room.nominal (sum (gfor volume stale volume.size)))
                        :actual (+ room.actual (sum (gfor volume stale volume.actual)))))
  (var taken #())
  (for [volume rest]
    (<- enough bool (room-covers? freed demand))
    (when (not enough)
      (:= freed (RoomBytes :nominal (+ freed.nominal volume.size) :actual (+ freed.actual volume.actual)))
      (:= taken (+ taken #(volume.name)))))
  (<- covered bool (room-covers? freed demand))
  (CoolingPlan :names (+ (tuple (gfor volume stale volume.name)) (if covered taken #())) :covered covered))


(defk restore-node [rooms percentage minimal floors demand]
  {:pre [(: rooms (get tuple #(DiskRoom ...))) (: percentage int) (>= percentage 0) (: minimal int) (<= 0 minimal 100)
         (: floors (get dict #(str int))) (: demand int) (>= demand 0)]
   :post [(: % str)]}
  "札の disk の組 rooms から、backup から戻す volume の engine を置く node を決めるため(replica と同じ node に付け、engine が網越しに replica の
   状態を読まない — README の節「戻しの失敗」): 需要 demand を両方の線で覆う disk のうち、名目の予約の線の残りが一番大きい disk の node(同じなら
   node の名の順の先頭)。覆う disk が無ければ空(spec.nodeID を書かない — Longhorn が選ぶ)。"
  (var chosen "")
  (var most -1)
  (for [room (sorted rooms :key (fn [room] room.node))]
    (<- lines RoomBytes (room-bytes #(room) percentage minimal floors))
    (<- fits bool (room-covers? lines demand))
    (when (and fits (> lines.nominal most))
      (:= chosen room.node)
      (:= most lines.nominal)))
  chosen)
