;;; 持ち主(上に載る系が渡す id 1 つ — 例えば会話)ごとの作業場所の volume を、作る・機体の node に attach する・作業場所の path に mount
;;; する・外す・冷やす・消す effect の型(関数は持たない — 本番の答え手は doeff_cluster._shared.entry.workspace_volume_api の
;;; workspace-volumes-api・模擬の答え手は doeff_cluster._sim.workspace_volumes の held-workspace-volumes)。決まり(級・名・寿命・mount の係)は
;;; 上に載る系の宣言の workspace-volumes/README.md。
;;;
;;;   WorkspaceOutcome        effect の結末: DONE = 今した・ALREADY = 既にそうだった(何もしていない)・RESTORED = backup から戻した(作る時だけ)
;;;   WorkspaceVolumeSite     本番の答え手が読む場所と待つ秒: Longhorn の namespace・値の ConfigMap・mount の係の頼みの dir・mountinfo の
;;;                           file・attach と detach と係の答えと backup と戻しを待つ上限の秒・読み直す間の秒・変わりの知らせが来ない時に
;;;                           読み直す間の秒
;;;   WorkspaceVolumeRefused  できなかった(持ち主の id と volume の名・何をしようとしたか・理由を文に持つ — token は載せない)
;;;   CoolingSweep            CoolIdleWorkspaceVolumes の答え: 冷やす前の線ごとの残りの byte・冷やした volume の名・冷やせなかった(広げられ
;;;                           なかった)volume と訳・広げた volume の名・空けておく量を覆えたか
;;;   CreateWorkspaceVolume   持ち主の volume が無ければ作る。冷えている(backup だけが在る)なら一番新しい backup から戻す(在れば ALREADY —
;;;                           戻している途中なら戻し終わるまで待つ・消している途中なら消え終わってから戻す)。置き場が入る volume の大きさに
;;;                           足りなければ、外れた volume をその大きさの分だけ古い順に冷やしてから作る(冷やしても足りない時だけ断る)
;;;                           戻しが失敗した volume は待たず、戻す元の backup が完了していれば消して戻し直す(上限まで失敗すれば断る)
;;;   AttachWorkspaceVolume   持ち主の volume を、答え手の機体の node に attach し、Longhorn が付け終わるまで待つ
;;;   MountWorkspaceVolume    attach した volume を path に mount する(path の dir は答え手が作る。空の volume には ext4 を作る。中身の在る
;;;                           dir には付けない — 中身を隠さない)。path に持ち主の volume の古い mount(STALE — device が作り直された)が
;;;                           在れば、使い手が無い時だけ外してから今の device を付ける(使い手が在れば断る — 古い mount の上で使わせない)
;;;   UmountWorkspaceVolume   path に付いた持ち主の volume を外す(何も付いていなければ ALREADY。使っている process が在れば断る)。古い
;;;                           mount も同じく外す
;;;   DetachWorkspaceVolume   持ち主の volume の attach を外し、外れるまで待つ(まだどこかに mount されていれば断る — 古い mount は使い手が
;;;                           無ければ外してから進む)
;;;   CoolWorkspaceVolume     外れている持ち主の volume の backup を取り、完了を確かめてから volume を消す(無ければ ALREADY。使っている・
;;;                           冷やす途中に使い始めた volume は消さずに断る)
;;;   PurgeWorkspaceVolume    冷えた持ち主の backup を消す(最後の backup から値の keepDays を過ぎた物だけ。backup が無ければ ALREADY・
;;;                           volume が在れば断る)— 上に載る系が「この持ち主はもう使わない」と決めた時に出す
;;;   CoolIdleWorkspaceVolumes 外れている volume のうち、使い終わりから値の warmHours を過ぎた物と、置き場の残りが値の spareBytes を
;;;                           割る分(線ごとの byte)を、使い終わりの古い順に冷やす。答え手の機体に付いている volume のうち、file system の
;;;                           空きが値の growBelowPercent を割った物は、名目の大きさを倍(上限は値の maxSizeBytes)にして付けたまま広げる
;;;   WorkspaceClusterUnreachable 区画の API server に届かない・5xx か 429 で答えた(WorkspaceVolumeRefused の子 — 基盤の止まりで、時間で晴れる)
;;;   WorkspaceRoomShort      置き場が足りずに作らなかった(WorkspaceVolumeRefused の子 — fresh = 持ち主が中身を持たないか・kept = 置けなかった
;;;                           volume を消せずに残したか)
;;;   WorkspaceRoom           ReadWorkspaceRoom の答え: 札の disk の線ごとの残りの byte と値の spareBytes
;;;   ReadWorkspaceRoom       札の disk の線ごとの残りの byte を読む(何も変えない)
;;;   WorkspacePlace          ReadWorkspacePlace の答え: 付ける先の様子(MOUNTED・STALE・READY・OUTSIDE・CLASH)
;;;   ReadWorkspacePlace      持ち主の volume を付ける先の様子を読む(何も変えない)— 呼び手が、volume の外に中身を持つ持ち主を見分けるため
;;;   AttachedWorkspaceVolume ReadAttachedWorkspaceVolumes の答えの 1 つ: 持ち主の id と使った刻
;;;   ReadAttachedWorkspaceVolumes 答え手の機体の node に付いた持ち主の volume を読む(何も変えない)— 呼び手が、自分の覚えの外で付いたままの
;;;                           volume(前の process が付けて残した物)を見つけるため。冷やす手順の snapshot と backup の ticket だけで付いた volume は数えない
;;;
;;; 順: 作る(か戻す)→ attach → mount → (使う)→ umount → detach →(しばらく後に)冷やす → 次に使う時に作る(戻す)…→ 消す。
;;; どの effect も何度出しても同じ様子に揃う(2 度目は ALREADY)。1 つの volume は同時に 1 つの node・1 つの path にだけ付く(RWO)。
;;; 使う側が勝つ: 冷やす途中に attach が来れば、冷やす側が volume を消さずに退く(attach は待たされない)。
(require doeff-hy.macros [val])
(require doeff-hy.record [defenum defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(import dataclasses [dataclass])  ; defrecord の展開が使う
(import enum [StrEnum])  ; defenum の展開が使う
(import typing [ClassVar])
(import doeff [EffectBase])
(import doeff_cluster._shared.intent.record_spec [RecordSpec RecordMode])


(defenum WorkspaceOutcome DONE ALREADY RESTORED)

;; 付ける先の様子(ReadWorkspacePlace の答え): MOUNTED = 持ち主の volume の今の device が付いている・STALE = 持ち主の volume の古い mount
;; が付いている(Longhorn が device を作り直し、mount が前の device を指したまま — 読み書きは I/O error。mount は付け直しで今の device に
;; 替わる — MountWorkspaceVolume)・READY = 何も付いておらず dir が無いか空(付けて
;; よい)・OUTSIDE = 何も付いておらず dir に中身が在り、持ち主は volume も backup も持たない(持ち主の作業場所は volume の外に在る —
;; 付けると中身を隠す)・CLASH = 別の物が付いている、か、dir に中身が在るのに持ち主が volume か backup を持つ(付けても付けなくても、
;; どちらかの中身が見えなくなる)。
(defenum WorkspacePlace MOUNTED STALE READY OUTSIDE CLASH)


(defrecord WorkspaceVolumeSite
  "本番の答え手が読む場所と待つ秒(頭の註): longhorn-namespace = Longhorn の volume と VolumeAttachment の namespace・settings-namespace と
   settings-name = 値の ConfigMap(workspace-volume.json — 級の名と大きさ)・mount-dir = mount の係の頼みの dir(無い worker には係が
   無い)・mountinfo = 答え手の process から見た mount の表の file・attach-seconds と detach-seconds = Longhorn が付け終わる・外し終わる
   (消え終わる)のを待つ上限・mount-seconds = 係の答えを待つ上限(空の volume の mkfs を含む)・backup-seconds = 冷やす時の snapshot と
   backup の 1 つずつを待つ上限・restore-seconds = backup から戻し終わるのを待つ上限・poll-seconds = 読み直す間(変わりの知らせを
   待たない待ち — snapshot・backup・engine・置き場の残り)・recheck-seconds = 変わりの知らせを待つ待ち(付け・外し・戻し・消え終わり・係の
   答え)が、知らせが来なくても読み直す間(取りこぼした知らせと、知らせの見張りが止まった間の網)。"
  {:check [(> (len longhorn-namespace) 0) (> (len settings-namespace) 0) (> (len settings-name) 0)
           (.startswith mount-dir "/") (.startswith mountinfo "/")
           (> attach-seconds 0) (> detach-seconds 0) (> mount-seconds 0) (> backup-seconds 0) (> restore-seconds 0)
           (> poll-seconds 0) (> recheck-seconds 0)]}
  (#^ str longhorn-namespace)
  (#^ str settings-namespace)
  (#^ str settings-name)
  (#^ str mount-dir)
  (#^ str mountinfo)
  (#^ float attach-seconds)
  (#^ float detach-seconds)
  (#^ float mount-seconds)
  (#^ float backup-seconds)
  (#^ float restore-seconds)
  (#^ float poll-seconds)
  (#^ float recheck-seconds))


(defclass WorkspaceVolumeRefused [RuntimeError]
  "作業場所の volume の操作ができなかった(持ち主の id と volume の名・何をしようとしたか・理由を文に持つ — token は載せない・既定の値で
   埋めない)。")


(defclass WorkspaceClusterUnreachable [WorkspaceVolumeRefused]
  "区画の API server に届かなかった・API server が 5xx か 429(混んでいる)で答えた(区画の基盤の止まり — 時間で晴れる。Longhorn・mount の係の
   断りや置き場の不足と型で分ける — 呼び手は間を置いて出し直す。文は WorkspaceVolumeRefused と同じ形)。")


(defclass WorkspaceRoomShort [WorkspaceVolumeRefused]
  "札の disk に volume を置く場所が足りず、作らなかった(作る前の数えで、外れた volume を冷やしても入る大きさの分を空けられなかったか、
   作った後に Longhorn が replica を置けなかった — 置けなかった volume は消してから断る)。fresh = 持ち主が中身を持たない(作るのは空の
   volume だった)か。False = 冷えた中身を backup から戻す所だった — 呼び手は空の場所に落とさない(中身は backup に残る)。kept = 置けなかった
   volume を消せずに残した(消す前の読みの後の書きが上限の回数まで続いた — 中身は無いが、持ち主の volume はまだ在る)か。呼び手は fresh でも、
   kept なら持ち主が volume を持つと読む(次の作るが ALREADY で答え、付ける先の様子が volume の外の中身と並ぶ)。"
  (defn __init__ [self #^ str message #^ bool fresh #^ bool [kept False]]
    ;; cannot be a defk: 例外の作り手(raise が呼ぶ口)
    "断りの文 message と、中身を持たない持ち主か fresh と、置けなかった volume を残したか kept を持つため(作る前の数えの断りは volume を
     作っていないので kept False)。"
    (.__init__ (super) message)
    (setv self.fresh fresh
          self.kept kept)))


(defrecord WorkspaceRoom
  "ReadWorkspaceRoom の答え(byte — 負 = 線を越えて置かれている): nominal と actual = 札の disk の残り(Longhorn の置く線 2 つ — 名目の予約の
   線と実の空きの線 — ごと)・spare = 値の spareBytes(冷やす回が空けておく量 — 線のどちらかがこれを割れば冷やす回が冷やす)。"
  (#^ int nominal)
  (#^ int actual)
  (#^ int spare))


(defrecord AttachedWorkspaceVolume
  "ReadAttachedWorkspaceVolumes の答えの 1 つ: owner = 持ち主の id(volume の名から頭を除いた物 — 名は小文字なので、id も小文字で答える。id の
   条件は大文字と小文字を区別しない)・used-at = 使った刻の印(epoch 秒 — 無ければ作られた刻)。"
  {:check [(!= owner "") (>= used-at 0.0)]}
  (#^ str owner)
  (#^ float used-at))


(defrecord CoolingSweep
  "CoolIdleWorkspaceVolumes の答え: nominal と actual = 冷やす前の札の disk の残り(byte — 名目の予約の線と実の空きの線)・cooled = 冷やした
   volume の名(冷やした順)・failed = 冷やせなかった(広げられなかった)volume の「<名>: <訳>」(使い始めた・backup が終わらない・置き場が
   足りないなど — 様子は前のまま)・grown = 付いている volume のうち、file system の空きが値の growBelowPercent を割って広げた物の名・
   covered = 冷やす順が、冷やした後に両方の線が値の spareBytes を覆うと数えたか(偽 = 外れた volume を全部冷やしても届かないので、
   warmHours を過ぎた物だけを冷やした)。"
  (#^ int nominal)
  (#^ int actual)
  (#^ (get tuple #(str ...)) cooled)
  (#^ (get tuple #(str ...)) failed)
  (setv #^ (get tuple #(str ...)) grown #())
  (setv #^ bool covered True))


(defclass [(dataclass :frozen True)] CreateWorkspaceVolume [(get EffectBase WorkspaceOutcome)]
  "持ち主 owner の volume が無ければ作る・冷えていれば backup から戻す(頭の註)。"
  ;; 記録: 判断の外への報告(output — cluster の物を作る)。
  (setv #^ (get ClassVar RecordSpec) __record-spec__ (RecordSpec :mode RecordMode.OUTPUT))
  (#^ str owner))


(defclass [(dataclass :frozen True)] AttachWorkspaceVolume [(get EffectBase WorkspaceOutcome)]
  "持ち主 owner の volume を答え手の機体の node に attach する(頭の註)。"
  ;; 記録: 判断の外への報告(output — volume を node に付ける)。
  (setv #^ (get ClassVar RecordSpec) __record-spec__ (RecordSpec :mode RecordMode.OUTPUT))
  (#^ str owner))


(defclass [(dataclass :frozen True)] MountWorkspaceVolume [(get EffectBase WorkspaceOutcome)]
  "持ち主 owner の volume を path(作業の根の下の絶対 path)に mount する(頭の註)。"
  ;; 記録: 判断の外への報告(output — 機体の mount を足す)。
  (setv #^ (get ClassVar RecordSpec) __record-spec__ (RecordSpec :mode RecordMode.OUTPUT))
  (#^ str owner)
  (#^ str path))


(defclass [(dataclass :frozen True)] UmountWorkspaceVolume [(get EffectBase WorkspaceOutcome)]
  "path に付いた持ち主 owner の volume を外す(頭の註)。"
  ;; 記録: 判断の外への報告(output — 機体の mount を外す)。
  (setv #^ (get ClassVar RecordSpec) __record-spec__ (RecordSpec :mode RecordMode.OUTPUT))
  (#^ str owner)
  (#^ str path))


(defclass [(dataclass :frozen True)] DetachWorkspaceVolume [(get EffectBase WorkspaceOutcome)]
  "持ち主 owner の volume の attach を外す(頭の註)。"
  ;; 記録: 判断の外への報告(output — volume を node から外す)。
  (setv #^ (get ClassVar RecordSpec) __record-spec__ (RecordSpec :mode RecordMode.OUTPUT))
  (#^ str owner))


(defclass [(dataclass :frozen True)] CoolWorkspaceVolume [(get EffectBase WorkspaceOutcome)]
  "外れている持ち主 owner の volume を backup してから消す(頭の註)。"
  ;; 記録: 判断の外への報告(output — backup を作り、cluster の物を消す)。
  (setv #^ (get ClassVar RecordSpec) __record-spec__ (RecordSpec :mode RecordMode.OUTPUT))
  (#^ str owner))


(defclass [(dataclass :frozen True)] PurgeWorkspaceVolume [(get EffectBase WorkspaceOutcome)]
  "冷えた持ち主 owner の backup を消す(頭の註)。"
  ;; 記録: 判断の外への報告(output — backup を消す)。
  (setv #^ (get ClassVar RecordSpec) __record-spec__ (RecordSpec :mode RecordMode.OUTPUT))
  (#^ str owner))


(defclass [(dataclass :frozen True)] ReadWorkspacePlace [(get EffectBase WorkspacePlace)]
  "持ち主 owner の volume を付ける先 path の様子を読む(頭の註)。"
  ;; 記録: 外の世界の読み(read — mount の表・dir・Longhorn の物を読む・再生では記録の答えを返す)。
  (setv #^ (get ClassVar RecordSpec) __record-spec__ (RecordSpec :mode RecordMode.READ))
  (#^ str owner)
  (#^ str path))


(defclass [(dataclass :frozen True)] ReadWorkspaceRoom [(get EffectBase WorkspaceRoom)]
  "札の disk の線ごとの残りの byte を読む(頭の註)。"
  ;; 記録: 外の世界の読み(read — 置き場の様子を読む・再生では記録の答えを返す)。
  (setv #^ (get ClassVar RecordSpec) __record-spec__ (RecordSpec :mode RecordMode.READ)))


(defclass [(dataclass :frozen True)] ReadAttachedWorkspaceVolumes [(get EffectBase (get tuple #(AttachedWorkspaceVolume ...)))]
  "答え手の機体の node に付いた持ち主の volume を読む(頭の註)。"
  ;; 記録: 外の世界の読み(read — volume の様子を読む・再生では記録の答えを返す)。
  (setv #^ (get ClassVar RecordSpec) __record-spec__ (RecordSpec :mode RecordMode.READ)))


(defclass [(dataclass :frozen True)] CoolIdleWorkspaceVolumes [(get EffectBase CoolingSweep)]
  "使い終わりから時間の経った volume と、置き場の空きを割る分を冷やす(頭の註)。"
  ;; 記録: 判断の外への報告(output — backup を作り、cluster の物を消す)。
  (setv #^ (get ClassVar RecordSpec) __record-spec__ (RecordSpec :mode RecordMode.OUTPUT)))
