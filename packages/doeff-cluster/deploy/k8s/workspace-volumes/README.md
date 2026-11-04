# 作業場所の volume — 級・名・寿命・backup の決まり

worker は、持ち主(上に載る系が渡す id 1 つ)ごとに Longhorn の RWO volume を 1 つ持ち、持ち主が使う間だけ、使う機体の node に
attach して作業場所の path に mount する。Pod は作らない — attach は Longhorn の VolumeAttachment の ticket(attacherType
longhorn-api)、mkfs と mount は worker が行う(作業場所の path への見せ方は節「mount の係」・handler は節「handler」)。
この dir は、その volume の級・名・寿命・backup の決まりと、worker が読む値を持つ。

| file | 何か |
|---|---|
| `storageclass.yaml` | 級 `longhorn-workspace`(replica・置く disk・locality・backup の先と群)— 値の定義元 |
| `backup.yaml` | backup の先 `garage-workspace`(BackupTarget)と 1 時間ごとの backup(RecurringJob `workspace-backup`) |
| `workspace-volume.json` | worker が読む値(ConfigMap `workspace-volume`・namespace agent-worker): 級の名・名の頭と長さ・大きさ・寿命の時間・広げる線と上限 |
| `workspace-mount.sh` | mount の係の script(ConfigMap `workspace-mount`・namespace agent-worker)— 節「mount の係」 |
| `README.md` | この file(決まりと値の訳) |

## handler(作る・attach・mount・umount・detach・冷やす・消す)

持ち主の volume を扱う effect は 11 で、入口 `doeff_cluster` から出す: `CreateWorkspaceVolume`・`AttachWorkspaceVolume`・
`MountWorkspaceVolume`・`UmountWorkspaceVolume`・`DetachWorkspaceVolume`・`CoolWorkspaceVolume`・`PurgeWorkspaceVolume`・
`CoolIdleWorkspaceVolumes`・`ReadWorkspaceRoom`・`ReadWorkspacePlace`・`ReadAttachedWorkspaceVolumes`(答えは `WorkspaceOutcome` の DONE = 今した・ALREADY = 既にそうだった・RESTORED =
backup から戻した(作る時だけ)、まとめて冷やすは `CoolingSweep`、残りの読みは `WorkspaceRoom`、付ける先の読みは `WorkspacePlace`。できなければ `WorkspaceVolumeRefused`・
置き場が足りなければその子の `WorkspaceRoomShort`)。

| effect | 何をするか |
|---|---|
| `CreateWorkspaceVolume` | volume が無ければ作る。その名の完了した backup が在れば(冷えている)一番新しい物から戻して RESTORED(戻し終わるまで待つ)、無ければ空で DONE。在れば ALREADY — 戻す途中なら戻し終わりを、消す途中なら消え終わりを待ってから(同じ持ち主の次の頼みは先の戻しに相乗りする)。backup は在るが完了した物が無い名は空で作らずに断る。作る前に札の disk の残りを数え(節「置き場の数え」)、受けられなければ POST せずに `WorkspaceRoomShort` で断る。戻す volume は backup を取った時の大きさ(status.volumeSize)と値の大きさの大きい方で作る(Longhorn は backup より小さい volume への戻しを断る) |
| `MountWorkspaceVolume` | 前と同じ。何も付いていない付ける先の dir に中身が在れば(volume の外に書かれた物)、付けずに断る — 付けると中身が隠れる |
| `AttachWorkspaceVolume` | 前と同じ。ticket を置いた後に Volume へ使った刻の印(annotation `doeff-cluster/used-at` = epoch 秒)を書く。付くのを待つ間に Longhorn が replica を置けないと答え、replica がどこにも置かれていなければ(初めて付ける空の volume)、上限の秒まで待たずに volume を消して `WorkspaceRoomShort`(fresh)で断る |
| `DetachWorkspaceVolume` | 前と同じ。外した後に使い終わりの刻の印を書く(冷やす順が読む) |
| `CoolWorkspaceVolume` | 外れている volume を冷やす(節「寿命」の手順)。使っている・戻す途中・ほかの冷やす手順の途中の volume は触らずに断る |
| `PurgeWorkspaceVolume` | 冷えた名の backup を消す(最後の backup から keepDays を過ぎた物だけ)。Longhorn の volume が在る名・途中の backup の在る名は断る |
| `CoolIdleWorkspaceVolumes` | 外れている volume を、冷やす順(節「寿命」)に 1 つずつ冷やす。その後に、答え手の機体に付いている volume のうち file system の空きが線を割った物を付けたまま広げる(節「広げる」— 答えの grown)。冷やせなかった・広げられなかった物は訳を残して次へ進む |
| `ReadWorkspaceRoom` | 札の disk が受けられる、値の大きさの volume の残りの本数(節「置き場の数え」)と値の spareVolumes を答える(何も変えない) |
| `ReadWorkspacePlace` | 持ち主の volume を付ける先の様子を答える(何も変えない): MOUNTED = 持ち主の volume が付いている・READY = 何も付いておらず dir が無いか空・OUTSIDE = 何も付いておらず dir に中身が在り、持ち主は volume も backup も持たない(持ち主の作業場所は volume の外 — 置き場の不足で volume の外の場所で始めた持ち主)・CLASH = 別の物が付いている、か、dir に中身が在るのに持ち主が volume か backup を持つ |
| `ReadAttachedWorkspaceVolumes` | 答え手の機体の node に、この答え手の ticket で付いた持ち主の volume を、持ち主の id(小文字)と使った刻で答える(何も変えない)。冷やす手順の snapshot と backup の ticket だけで付いた volume は答えない。呼び手は、自分の覚えの外で付いたままの volume(前の process が付けて残した物)をこれで見つける |

- 置き場の不足 `WorkspaceRoomShort` の `fresh`: True = 持ち主が中身を持たない(作るのは空の volume だった)。呼び手は、その持ち主を volume の
  外の場所で始めてよい(その場所に書いた物は、後で volume を付けると隠れる — 呼び手は次から `ReadWorkspacePlace` の OUTSIDE で見分け、
  volume を作らずに同じ場所で続ける)。False = 冷えた中身を backup から戻す所だった(中身は backup に残る)。呼び手は空の場所に落とさず、冷やして場所を
  空けてから出し直す。

- 使う側が勝つ: 冷やす途中に attach が来たら、冷やす側は volume を消さずに退く(attach は待たされない)。冷やす側は Volume を読み(版 R)、
  次に VolumeAttachment に使う ticket(冷やす手順の snapshot と backup の ticket の外)が無い事を確かめ、版 R の上でだけ消す(DELETE の
  preconditions)。attach は ticket を置いてから印を書くので、消せたなら ticket は確かめの後に置かれていて、attach の印の書きが 404 になる —
  attach は「消された — 作る(戻す)から出し直す」と断る(上に載る系は作るから出し直す)。印が先なら版 R が古いか ticket が見えて消さない。
- 呼び手(上に載る系)の仕事: 使い始めに 作る → attach → mount、使い終わりに umount → detach。数分ごとに `CoolIdleWorkspaceVolumes` を出す。
  もう使わない持ち主には、keepDays の後に `PurgeWorkspaceVolume` を出す。handler は「いつ使うか」を知らない — 使った刻の印と Longhorn の
  様子だけで決める。

| 答え手 | 在りか | 何をするか |
|---|---|---|
| 本番 `workspace-volumes-api`(引数 `WORKSPACE-SITE`) | `src/doeff_cluster/_shared/protocol/workspace_volumes.hy` | worker の ServiceAccount で API server の Longhorn の Volume を作り(級の値を写す — 下の写し方の表)、VolumeAttachment に ticket `longhorn-api-<名>`(種類 longhorn-api)を置いて満たされるまで待つ。mount と umount は節「mount の係」の頼み。detach は mount が残っていれば断る |
| 模擬 `held-workspace-volumes`(引数 `WorkspaceVolumeWorld`) | `src/doeff_cluster/_sim/workspace_volumes.hy` | 同じ契約を memory の世界に当てる(使い手の検のため) |
| 純粋な判断 | `src/doeff_cluster/_shared/core/workspace_volume_rules.hy` | 名の条件と名・mountinfo の読み・attach と detach の次の一手 |

- 機体の node は機体の事実 NODE_NAME。係の頼みの dir は `WORKSPACE-SITE` の固定の path(job の子は env `WORKER_WORKSPACE_MOUNT_DIR` を
  継がない — 宣言と同じ path である事は `tests/test_workspace_mount.py` が縛る)。dir の無い worker では mount と umount を頼む前に断る。
- 外側に要る答え手: file(os)・環境(os.environ)・時計(doeff-time)・HTTP と、API server 宛ての要求を区画の CA で検める
  `in-cluster-api-routed`(job の本番の土台が並べる)。
- 検: `tests/test_workspace_volumes_handler.hy`(本番の答え手を偽の Longhorn と偽の係の上で回し、模擬の答え手と同じ筋書きで結末と様子が
  揃う事・失敗ケース・Spawn で並べた「戻す途中・冷やす途中・消す途中に次の頼みが来る」)と `tests/test_workspace_volume_rules.hy`。

## 級(StorageClass `longhorn-workspace`)

| 欄 | 値 | 訳 |
|---|---|---|
| numberOfReplicas | 1 | 測り(2026-11-01・zeus・中央値・hostPath に対する倍率): replica 1 は pytest 31 本 1.06・git clone 1.18・1 file ごとに fsync する小さい file 300 個 0.79。replica 2 は書くたびに別の機体への網を往復し、同じ fsync つきで 3.07・遅い回 9.3 秒。1 本の守りは garage への backup(節「backup」) |
| diskSelector | workspace | 札 workspace の disk だけに置く(節「置く disk」)。hot を名指さない |
| dataLocality | best-effort | attach した node に replica を寄せる。持ち主を別の機体へ移すと、Longhorn がその node に写しを作ってから元を消す。strict-local は replica の在る node にしか付かず、移せない |
| fsType | ext4 | 測りと同じ。worker は初めて付けた空の volume に mkfs.ext4 を打つ(backup から戻した volume は file system を持つ) |
| backupTargetName | garage-workspace | `backup.yaml`。Longhorn は無い BackupTarget を名指す volume の作成を断る(2026-11-01 の dry-run で確かめた) |
| recurringJobSelector | 群 workspace-backup と default | 1 時間ごとの backup と、ほかの volume と同じ snapshot の片付けと trim |
| reclaimPolicy | Retain | 中身を消すのは寿命の「冷やす」と「消す」だけ(PVC から使った時も、PVC を消しただけで消えない) |
| volumeBindingMode | Immediate | Pod が無いので、WaitForFirstConsumer では結ばれない |
| 大きさ | 2 GiB(`workspace-volume.json` の sizeBytes — 訳は節「大きさ」) | thin — 書いた分だけ disk を使う。付いている間に空きが線を割れば、worker が付けたまま広げる(節「広げる」) |

worker は CSI を通らずに Longhorn の volume を名指しで作るので、級の値を volume の欄へ自分で写す(Longhorn の CSI が PVC の時にする写しと
同じ)。写し方:

| 級の parameters | Longhorn の volume の欄 |
|---|---|
| numberOfReplicas "1" | spec.numberOfReplicas 1 |
| diskSelector "workspace" | spec.diskSelector ["workspace"](`,` で分ける) |
| dataLocality・staleReplicaTimeout・backupTargetName | spec の同じ名の欄 |
| recurringJobSelector の群 g | label `recurring-job-group.longhorn.io/<g>: enabled` |
| fsType | mkfs の型(volume の欄ではない) |
| (`workspace-volume.json`)sizeBytes | spec.size |

## 名

- 名 = `ws-` + 持ち主の id を小文字にした物。例: id `c-EXAMPLEOWNERID000000000001` → `ws-c-exampleownerid000000000001`(31 字)。
- 同じ名を、Longhorn の volume と volume の attach の ticket の印に使う。backup は Longhorn が label `backup-volume=<volume の名>`・
  `backup-target=garage-workspace` を付けて束ねる(束 BackupVolume の名は `<volume の名>-<hash>`・spec.volumeName が volume の名)。持ち主が
  冷えて戻っても名は替わらない — 戻す先も backup も名 1 つで引ける。
- 持ち主の id の条件(上に載る系が守る): 英字・数字・`-` だけ・大文字と小文字を区別しない(Crockford の base32 の ULID のように — 小文字に
  しても 2 つの id が 1 つの名に重ならない)・37 字まで。名は 40 字まで(`workspace-volume.json` の nameMaxLength)— Longhorn は 40 字を越える
  volume の名を、頭の 31 字と hash に縮めて別の名にする(2026-11-01 の dry-run: 63 字の名が `<頭 31 字>-40682b07` になった)。worker は条件を
  外れた id を、volume を作る前に断る(縮めた名や重なった名を作らない)。
- 1 つの持ち主に volume は 1 つ。2 つの持ち主が 1 つの volume を分けない(RWO — 同時に付くのは 1 つの node だけ)。

## 寿命

持ち主の volume は、次の 5 つの様子のどれか 1 つに在る。時間の値は `workspace-volume.json`。

| 様子 | どこに在るか | 入る時 | 出る時 |
|---|---|---|---|
| 無い | どこにも無い | — | 持ち主が初めて使う前に、worker が作る(空 → mkfs) |
| 付いている | 使う機体の node に attach・作業場所の path に mount | 持ち主が使い始める時 | 使い終わった時(使い終わりに持ち主が置いた命令が終わるまで待つ)に umount・detach |
| 温かい | Longhorn の disk(外れている) | 使い終わった時 | 次に使う時はそのまま付ける。下の「冷やす」の条件で冷やす |
| 冷えている | garage の backup だけ(Longhorn の volume は無い) | 冷やした時 | 次に使う時は backup から同じ名で戻してから付ける(節「backup」の戻し方) |
| 消えた | どこにも無い | 消した時 | — |

- 冷やす: 温かい volume のうち、(1) 使い終わってから warmHours(2 時間)を過ぎた物は全部、(2) それでも札 workspace の disk が受けられる
  volume の残り(空き)と冷やす本数の和が spareVolumes(4 本)に届かなければ、届くまで残りを、使い終わりの古い順に(判断 = `cooling-order`・
  空き = Longhorn の置く線 `free-slots`)。手順(`CoolWorkspaceVolume`)は Snapshot `<名>-cool-<epoch 秒>` を作る → 使えるまで待つ →
  同じ名の Backup(label backup-volume・backup-target)を作る → Completed まで待つ → 使う ticket が無い事を確かめて読んだ版の上で volume を
  消す → 消え終わるまで待つ。外れている volume は、Longhorn が snapshot と backup の ticket(frontend を問わない)で自分で付けて取る —
  worker は付けない・mount しない。backup の完了を確かめずに消さない(backup が Error なら volume を残して断る)。
- fstrim は冷やす時にしない(付けて mount すると、その間に来た使う頼みの mount と RWO で争う)。消したファイルの block は、使っている間の
  群 default の trim(毎時)で返る。
- 消す: 使い終わってから keepDays(7 日)を過ぎ、上に載る系が「この持ち主はもう使わない」と答えた時に、その名の backup の束(BackupVolume)を消す。答えが無い・
  読めない時は消さない(今の作業場所の dir の決まり — 最後の変更から 7 日・持ち主に問う — と同じ日数)。冷やした volume の中身は 7 日の間
  backup に残るので、「作業場所を消さない・消すなら 1 週間」の決まりは冷やしても守られる。handler は最後の backup から keepDays の内の名を
  消さずに断る(上に載る系の答えの取り違えの守り)。

### なぜ温かいままにしないか(容量の数)

- 作業場所の dir は 1 日に約 900 個増える(2026-11-01 23:13 JST の zeus の作業場所の dir の数え: 最後の変更が 1 時間以内 25・3 時間 50・
  6 時間 111・12 時間 549・24 時間 891・7 日 7,565・全部で 8,799 個・48 GB)。
- Longhorn は volume を名目の大きさで数え、disk ごとに (容量 − 予約) × 200%(設定 storage-over-provisioning-percentage)までしか置かない。
  10 GiB の volume を 7 日温かく置くと約 7,500 本・75 TiB で、どの disk にも収まらない。
- 今の札 workspace の disk(zeus の USB の SSD の上の 100 GiB の image — 2026-11-03 の読み: storageMaximum 105 GB・予約 0)に置ける
  volume は名目で、10 GiB なら 19 本・2 GiB なら 97 本(付いている物と温かい物の和)。昼の 1 時間に約 25 個の作業場所が動くので、10 GiB
  では 2 時間の窓でも数の線(2)が先に効いた。大きさの訳は節「大きさ」。
- spareVolumes = 4 の訳: 付いている・温かい物の外に 4 本を、次のまとめて冷やす回(数分ごと)までに始まる新しい持ち主のために空けておく
  (昼の頃合いで 5 分に 2 本ほど)。前の 8 本は、disk が 72 本を受ける見込みの頃の値。

### 大きさ(sizeBytes = 2 GiB)

名目の大きさは Longhorn が disk に予約する量で、実の使用ではない(thin)。だから作る大きさは、実の使用の分布の多くを広げずに受け、
重い少数は付けたまま広げる(節「広げる」)幅にする。

| 時(2026-11-03 JST・zeus の作業場所の volume の Longhorn の actualSize) | 本数 | <64 MiB | 64–512 MiB | 512 MiB–1 GiB | 1–2 GiB | 2–4 GiB | ≥4 GiB | p50 | p90 | 最大 |
|---|---|---|---|---|---|---|---|---|---|---|
| 17:28 | 56 | 5 | 36 | 0 | 13 | 2 | 0 | — | — | 3,611 MiB |
| 17:55 | 38 | 5 | 23 | 0 | 8 | 2 | 0 | 268 MiB | 1,248 MiB | 3,611 MiB |

- 読み方: 前の 10 GiB の volume は、空でも mkfs が journal と inode の表を 0 で埋めて約 228 MiB を使っていた(今は
  `assume_storage_prezeroed` で埋めない — 新しい 1 GiB の volume の mkfs の直後は 680 KB)。64–512 MiB の多くはその上乗せだけの volume。
- 2 GiB の訳: 名目の線で札の disk に 97 本(200% の時 — 昼の 2 時間の約 50 本と空けておく 4 本を受ける。10 GiB では 19 本)。
  growBelowPercent 50 なら 1 GiB を使った所で広げ始め、上の分布では 1 度広げるのが約 4 本に 1 本、2 度広げるのが約 20 本に 1 本。
- 戻す volume は backup を取った時の大きさで作る(Longhorn は backup より小さい volume への戻しを断る)。前に 10 GiB で作った持ち主は、
  戻すたびに名目 10 GiB を持ち続ける。
- 冷えている volume を次に使う時は、backup から戻す時間が掛かる(2026-11-03 の zeus の測り: 中身 50 MB で戻す 20.8 秒・attach 6.0 秒・
  mount 0.5 秒)。温かい volume でも attach と mount で約 6 秒掛かる。
- 冷やす・戻す・消すは handler の仕事(節「handler」)。この dir は決まりと値だけを持つ。

## backup

| 項目 | 決まり |
|---|---|
| 先 | BackupTarget `garage-workspace` = cluster の中の S3(garage)の bucket `longhorn-workspace`。資格は Secret `longhorn-backup-garage`(longhorn-system)。bucket・key・Secret は garage を持つ側が作る(中身は repo に書かない) |
| 間隔 | 付いている volume を 1 時間ごと(RecurringJob `workspace-backup`・毎時 17 分)。外れている volume は中身が変わらないので飛ばす。冷やす時に 1 回必ず |
| 何を残すか | volume の block の差分(Longhorn の backup)。1 時間ごとの backup は volume ごとに新しい 6 つ。冷やす時の backup は「消す」まで残す |
| 失うかもしれない物 | 付いている間に node か disk が壊れた時: 最後の backup(多くて 1 時間前)の後の書き。温かい間に壊れた時: 最後に付いていた間の最後の backup の後の書き。今の hostPath(機体の disk 1 つ・backup 無し)より狭い |

### 別の node へ戻す手順

冷えている volume を次に使う時と、volume の在る node か disk が壊れた時に、同じ手順で戻す。冷えている volume は `CreateWorkspaceVolume`
が 1・3 を、`AttachWorkspaceVolume`・`MountWorkspaceVolume` が 4・5 をする。壊れた時は 2(残った volume を消す)を人が決めてから同じ effect を
出す。

1. 戻す元を選ぶ: `kubectl -n longhorn-system get backups.longhorn.io -l backup-volume=ws-<id>,backup-target=garage-workspace` の
   state Completed のうち、status.backupCreatedAt が一番新しい物(その status.url が戻す元)。
2. 壊れた node の volume が残っていれば消す(replica 1 の volume は、replica の在る node が戻るまで付かない)。
3. 同じ名 `ws-<id>` の Longhorn の volume を、級の値(上の写し方の表)と spec.fromBackup = 選んだ backup の status.url で作る。札 workspace の
   disk の在る node のどれかに置かれ、Longhorn が中身を戻す(status.restoreRequired が false になるまで待つ)。
4. 使う機体の node に attach する。replica が別の node に置かれていれば、dataLocality best-effort がその node へ寄せる。
5. mount して使う。戻した volume は file system を持つので mkfs しない。

## 置き場の数え

Longhorn は replica を disk に置く時、線を 2 つ照らす(v1.13 の replica の置き方): 名目の予約の線 = 置いた replica の名目の大きさの和
(storageScheduled)+ 新しい volume の大きさ ≤ (storageMaximum − storageReserved)× 設定 storage-over-provisioning-percentage、実の空きの
線 = storageAvailable − その volume の実の使用 > storageMaximum × 設定 storage-minimal-available-percentage。どちらかを外れる disk には
置かず、置き先の無い volume を condition Scheduled False(reason ReplicaSchedulingFailure)・robustness faulted にする。

- 置き先を決める時: Longhorn v1.13 は、外れている volume を作った時には replica の物を作るだけで disk に置かない(condition Scheduled は
  True・replica の nodeID は空)。置き先は初めて付ける時か、backup から戻す時(Longhorn が付けて戻す)に決める。2026-11-03 16:2x JST の
  本番の試し: 札 workspace の disk に置ける 1 GiB の volume は 30 秒経っても replica の nodeID が空のまま、札の無い disk を名指す volume は
  1 秒の内に Scheduled False(`precheck new replica failed: tags not fulfilled`)・faulted になった。だから置き場の不足は作った時でなく
  付ける時に現れ、付けの ticket は満たされないまま待つ(11-03 12:19 JST からの attach の 180 秒待ちの形)。
- 作る前の数え(`room-slots`・`free-slots`): 札の disk ごとに、名目の予約の線の残りを大きさで割った本数と、実の空きの線の上の量を
  大きさで割った本数(新しい volume が名目の大きさまで書いても線を割らない本数 — thin の volume の実の使用は名目より小さいので少なめに
  数える側)の少ない方を足す。0 本なら作らない(Volume を POST しない — faulted の volume を作らない)。
- 数えと付けの間の競り: 同じ disk に別の持ち主の作りが並ぶと(Pod の入れ替えで 2 つの世代の worker が並ぶ時も)、数えた後に残りが
  尽きる。負けた側は付ける時に Longhorn の「置けない」を読み、replica がどこにも置かれていない(中身の無い)volume をその場で消して
  `WorkspaceRoomShort` で断る(上限の秒まで待たない・faulted を残さない)。replica の置かれた volume(中身を持ちうる)は消さない。
- 冷やす回の空きも同じ数え(2 つの線の少ない方)で読む — 実の空きが線に近づいた時も、空きが spareVolumes を割る分を冷やす。
- 広げる時も同じ数えで、名目の増え(新しい大きさ − 今の大きさ)を札の disk が受けられるかを確かめ、受けられなければ広げない(節「広げる」)。

## 広げる

名目の大きさは、Longhorn が disk に予約する量(節「置き場の数え」の名目の予約の線)で、実の使用ではない。だから小さく作るほど 1 つの disk に
多くの volume を置ける。使う間に大きな作業場所になった持ち主の volume は、付けたまま広げる(Longhorn v1.13 は付いている volume の
spec.size の書きを online で広げる — 下の本番の試し)。

| 値(`workspace-volume.json`) | 今 | 訳 |
|---|---|---|
| growBelowPercent | 50 | 付いている volume の file system の空きがこの % を割ったら広げる。冷やす回の間(数分)に、その時の空きを書き切らない幅 |
| maxSizeBytes | 16 GiB | 広げる上限。1 つの volume が札の disk の名目を食い尽くさない幅(2026-11-03 の zeus の 56 本の実の使用は最大 3.6 GiB) |

- いつ: `CoolIdleWorkspaceVolumes`(上に載る系が数分ごとに出す program の回)が、冷やした後に、答え手の機体の mount の表に載る作業場所の
  volume の空きを 1 つずつ読む(`mounted-growth`)。file system の空きの減りは知らせを出さないので、付けている間は回ごとに読む。外れている
  volume は広げない(付けている間の回が読む)。
- 手順(`grown-volume`): 付ける先の file system の空きを読む(statvfs)→ 線の上なら何もしない → 名目を倍(上限 maxSizeBytes)にした増えを
  数える(受けられなければ広げずに訳を残す)→ Volume の spec.size を merge patch で書く → Engine(label `longhornvolume=<名>`)の
  status.currentSize が新しい大きさになるまで待つ(上限 attach-seconds)→ mount の係へ grow を頼む(`resize2fs` — 付けたまま)。
- 前の広げの続き: 名目が file system の 1.5 倍以上なら(名目を書いた後、file system を広げる前に止まった)、名目を書き直さず(倍にしない)、
  engine を待って file system だけを広げる。
- 戻す時: 冷やした volume の backup は、取った時の大きさを覚える。戻す volume はその大きさと sizeBytes の大きい方で作る(Longhorn は
  backup より小さい volume への戻しを断る)。
- 模擬の答え手 `held-workspace-volumes` は file system の空きを持たないので広げない(答えの grown は空)。
- 本番の試し(2026-11-03 17:45 JST・zeus-usb-workspace の試しの volume を付けて mount し、600 MiB を書いた後に handler と同じ手順を手で
  打った — spec.size の merge patch → Engine の currentSize の読み → 係への grow の頼み):

  | 回 | 名目 | spec.size を書いてから engine が広がるまで | resize2fs | file system の総量 |
  |---|---|---|---|---|
  | 1 | 1 GiB → 2 GiB | 16 秒 | 1 秒未満 | 1,020,702,720 → 2,077,073,408 byte |
  | 2 | 2 GiB → 4 GiB | 246 秒(engine は要求を誤り無しに 4 分ほど持った) | 0.4 秒 | 2,077,073,408 → 4,189,814,784 byte |

  中身(600 MiB の file の hash)は前後で同じ。2 回目のように engine の待ちが上限(attach-seconds 180 秒)を越えた回は断って訳を残し、次の
  回が「前の広げの続き」として file system だけを広げる。

## 置く disk(札 workspace)

- 級は札 workspace の disk だけに置く。札を付ける disk は、Longhorn の disk を宣言する側が決めて付ける(この dir は付けない)。
- 付けるのは自宅の機体の SSD だけ。cloud の VPS(k3s-0〜3)の disk には付けない — 網の向こうの replica は書きが遅く、VPS は作業場所を受け
  ない。この repo で VPS の disk の札を宣言している `../disk-share/shares.yaml` に workspace が入らない事を test が確かめる。
- 2026-11-01 の時点で、札 workspace の disk は無い — 級を当てても、札を付けるまで volume は置かれない。zeus で今 Longhorn が使える SSD は
  札 hot の SATA SSD だけで、hot の disk は名簿の volume だけの物という決まりに当たる。作業場所の今の disk(zeus の USB の SSD・1.6 TB)は
  空きが 290 GB で、Longhorn が置く線(空きが容量の 25% — 設定 storage-minimal-available-percentage)を割っている。どちらに付けるかは
  持ち主の決め。

## mount の係

worker の container は特権を持たないので、mkfs・mount・umount・fstrim・resize2fs は同じ Pod の sidecar `workspace-mount`(root・privileged の
native sidecar — 宣言と訳は `../nodes/zeus/node.yaml` の註)がする。今は zeus の worker にだけ在る。

| 項目 | 決まり |
|---|---|
| 見せ方 | 係の `/work` は mountPropagation Bidirectional、worker の container の `/work` は HostToContainer(同じ volume work = host の `/mnt/fast_ssd_usb/agent-worker` — host の `/mnt/fast_ssd_usb` は shared の mount)。係が `/work/<path>` に付けた volume は host を通って worker の `/work/<path>` に見える |
| 入れ替え | mount は host の物なので、Pod の入れ替え(2 つの世代が同じ機体に並ぶ)でも残り、どちらの世代の worker にも係にも見える。係が止まっても外れない — 外すのは頼みだけ |
| 頼みの dir | env `WORKER_WORKSPACE_MOUNT_DIR`(= `/run/workspace-mount` — Pod ごとの emptyDir を係と worker が分け合う)。env の無い worker には係が無い |
| 頼み | `<鍵>.tmp` に 1 行 `<命令> <volume の名> <付ける先>`(overlay と unoverlay は `<命令> <下の dir> <付ける先>`)を書き、`<鍵>.req` へ名を替える(鍵は頼みごとに別。`.` で始めない)。命令は mount・umount・fstrim・grow・overlay・unoverlay |
| 答え | 係は 0.2 秒ごとに `*.req` を見て script を読み直して走らせ、`<鍵>.out`(1 行目 = code 0 か 1・2 行目から訳と命令の出力)を置いて `<鍵>.req` を消す。頼み手は答えを読んだら消す |
| mount | device は host の `/dev/longhorn/<volume の名>`(Longhorn の ticket で attach した後に在る)。file system が無ければ `mkfs.ext4 -m 0 -E root_owner=1000:1000,assume_storage_prezeroed=1` を打ってから付ける(新しい volume は書いていない所を 0 で読むので、journal と inode の表を 0 で埋めない — 埋めると 10 GiB ごとに約 224 MiB を SSD へ書く)。同じ volume が既に付いていれば何もしない |
| umount | 付いている物が名指した volume か照らしてから外す。使用中で外れなければ断る(lazy に外さない)。何も付いていなければ何もしない |
| fstrim | 付いている物を照らしてから `fstrim -v`(冷やす前 — 節「寿命」) |
| grow | 付いている物を照らしてから `resize2fs <device>`(付けたまま device の今の大きさまで — 頼み手が Longhorn の volume を広げた後。device が広がっていなければ何もしない) |
| overlay | 付ける先に、下の dir(`/work/base/` の下の共有の checkout)を下の層・`<付ける先>.up` を上の層・`<付ける先>.wk` を overlayfs の作業の dir にした overlay を付ける。仕事ごとに repo の作業の木を丸ごと書き出さず、変えた file だけを上の層に書く(USB の SSD の書きを減らす)。上の層と作業の dir は頼み手が作る。付ける先が空でなければ断る(中身が隠れる)。同じ下の dir の overlay が既に付いていれば何もしない |
| unoverlay | 付いている overlay の下の dir を照らしてから外す。使用中で外れなければ断る。上の層は消さない(頼み手が消す)。何も付いていなければ何もしない |
| 下の dir | `/work/base/` の下に頼み手が置き、overlay が付いている間は書き換えない(overlayfs は下の層の書き換えを想定しない)。係は下の dir を作らない・消さない |
| 断る物 | 6 つの命令の外・volume の名が英小文字・数字・`-` の外・付ける先が `/work` の下の在る dir でない・`.` か `..` か `//` を含む・symlink を通る・attach されていない・付ける先に別の物・volume が別の所に付いている・中身が ext4 でない(作り直さない)。overlay と unoverlay では、ほかに path が英数字・`. _ / -` の外の字を持つ(overlayfs の option を区切る `,` と `:` を通さない)・下の dir が `/work/base/` の下でない・付ける先が `/work/base/` の下・上の層か作業の dir が無い・付ける先に別の下の dir の overlay か overlay でない物 |
| 付ける先の dir | 頼み手(worker の uid)が作り、外した後も残す(係は dir を作らない・消さない) |

- script を替える時: `workspace-mount.sh` を直して main に入れる。ConfigMap の名が固定なので Pod は作り直されず、kubelet が file を入れ替えた後
  (1 分前後)の頼みから効く。Pod の宣言(係の container・mount propagation・頼みの dir)を替えた時だけ Pod が作り直される。
- 検: `../../../tests/test_workspace_mount.py`(宣言と、偽の mount の命令で script を回す検)。

## 当て方と戻し方

- 当て方: main に入ると、Flux が worker の組(Kustomization doeff-cluster-workers — `../workers` がこの dir を resources に並べる)と
  一緒に当てる。
- 戻し方: `../workers/kustomization.yaml` の `../workspace-volumes` の行を外す。級と BackupTarget は prune: disabled なので Flux は消さない
  — 名指す volume が 0 本になってから手で消す。RecurringJob と ConfigMap は Flux が消す。
- mount の係の戻し方: 付いている volume を全部頼みで外してから、`../nodes/zeus/node.yaml` の係の宣言(initContainers の `workspace-mount`・
  volumes の 3 つ・worker の `/work` の mountPropagation と頼みの dir の mount)と `../nodes/zeus/workspace-mount.yaml` を外す(zeus の
  Pod が 1 度作り直される)。外さずに係を消すと、付いた volume は host に残り、外す者が居なくなる。
- 級の parameters は作った後に替えられない(k8s が断る)。替える時は別の名の級を足し、`workspace-volume.json` の storageClass を移す。
