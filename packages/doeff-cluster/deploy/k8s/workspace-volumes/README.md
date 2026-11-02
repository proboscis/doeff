# 作業場所の volume — 級・名・寿命・backup の決まり

worker は、持ち主(上に載る系が渡す id 1 つ)ごとに Longhorn の RWO volume を 1 つ持ち、持ち主が使う間だけ、使う機体の node に
attach して作業場所の path に mount する。Pod は作らない — attach は Longhorn の VolumeAttachment の ticket(attacherType
longhorn-api)、mkfs と mount は worker が行う(作業場所の path への見せ方と handler は別の card — ki-c1a429da5761・ki-a2d67aa8203d)。
この dir は、その volume の級・名・寿命・backup の決まりと、worker が読む値を持つ。出所 = card ki-e2e594d4ecfe(区切り ms-e60af528a048 の
card 2)・測り ki-00f2d6af6581。

| file | 何か |
|---|---|
| `storageclass.yaml` | 級 `longhorn-workspace`(replica・置く disk・locality・backup の先と群)— 値の定義元 |
| `backup.yaml` | backup の先 `garage-workspace`(BackupTarget)と 1 時間ごとの backup(RecurringJob `workspace-backup`) |
| `workspace-volume.json` | worker が読む値(ConfigMap `workspace-volume`・namespace agent-worker): 級の名・名の頭と長さ・大きさ・寿命の時間 |
| `workspace-mount.sh` | mount の係の script(ConfigMap `workspace-mount`・namespace agent-worker)— 節「mount の係」 |
| `README.md` | この file(決まりと値の訳) |

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
| 大きさ | 10 GiB(`workspace-volume.json` の sizeBytes) | thin — 書いた分だけ disk を使う。足りなければ allowVolumeExpansion で広げる |

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

- 名 = `ws-` + 持ち主の id を小文字にした物。例: id `c-K973ZX4FCYMA7PZ498C0464B7G` → `ws-c-k973zx4fcyma7pz498c0464b7g`(31 字)。
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

- 冷やす: 温かい volume のうち、(1) 使い終わってから warmHours(2 時間)を過ぎた物、または (2) 札 workspace の disk が受けられる 10 GiB の
  volume の残りが spareVolumes(8 本)を割った時に、使い終わりの古い順に。手順は 付ける → fstrim → backup を取る → backup の完了を確かめる
  → 外す → volume を消す。backup の完了を確かめずに消さない。
- 消す: 使い終わってから keepDays(7 日)を過ぎ、上に載る系が「この持ち主はもう使わない」と答えた時に、その名の backup の束(BackupVolume)を消す。答えが無い・
  読めない時は消さない(今の作業場所の dir の決まり — 最後の変更から 7 日・持ち主に問う — と同じ日数)。冷やした volume の中身は 7 日の間
  backup に残るので、「作業場所を消さない・消すなら 1 週間」の決まりは冷やしても守られる。

### なぜ温かいままにしないか(容量の数)

- 作業場所の dir は 1 日に約 900 個増える(2026-11-01 23:13 JST の zeus の作業場所の dir の数え: 最後の変更が 1 時間以内 25・3 時間 50・
  6 時間 111・12 時間 549・24 時間 891・7 日 7,565・全部で 8,799 個・48 GB)。
- Longhorn は volume を名目の大きさで数え、disk ごとに (容量 − 予約) × 200%(設定 storage-over-provisioning-percentage)までしか置かない。
  10 GiB の volume を 7 日温かく置くと約 7,500 本・75 TiB で、どの disk にも収まらない。
- 例: zeus の SATA SSD(503 GB・予約 47 GB・置いた名目 138 GB — 2026-11-01)に置ける 10 GiB の volume は約 72 本。昼の 1 時間に約 70 個の
  作業場所が動くので、2 時間の窓でも数の上限(2)が先に効く。冷えた持ち主を次に使う時は backup から戻す時間(中身 1〜3 GiB で数十秒の見込み
  — 測りは handler の card)が掛かる。
- 冷やす・戻す・消すは handler の仕事(card ki-a2d67aa8203d と、その後の card)。この dir は決まりと値だけを持つ。

## backup

| 項目 | 決まり |
|---|---|
| 先 | BackupTarget `garage-workspace` = cluster の中の S3(garage)の bucket `longhorn-workspace`。資格は Secret `longhorn-backup-garage`(longhorn-system)。bucket・key・Secret は garage を持つ側が作る(中身は repo に書かない) |
| 間隔 | 付いている volume を 1 時間ごと(RecurringJob `workspace-backup`・毎時 17 分)。外れている volume は中身が変わらないので飛ばす。冷やす時に 1 回必ず |
| 何を残すか | volume の block の差分(Longhorn の backup)。1 時間ごとの backup は volume ごとに新しい 6 つ。冷やす時の backup は「消す」まで残す |
| 失うかもしれない物 | 付いている間に node か disk が壊れた時: 最後の backup(多くて 1 時間前)の後の書き。温かい間に壊れた時: 最後に付いていた間の最後の backup の後の書き。今の hostPath(機体の disk 1 つ・backup 無し)より狭い |

### 別の node へ戻す手順

冷えている volume を次に使う時と、volume の在る node か disk が壊れた時に、同じ手順で戻す。

1. 戻す元を選ぶ: `kubectl -n longhorn-system get backups.longhorn.io -l backup-volume=ws-<id>,backup-target=garage-workspace` の
   state Completed のうち、status.backupCreatedAt が一番新しい物(その status.url が戻す元)。
2. 壊れた node の volume が残っていれば消す(replica 1 の volume は、replica の在る node が戻るまで付かない)。
3. 同じ名 `ws-<id>` の Longhorn の volume を、級の値(上の写し方の表)と spec.fromBackup = 選んだ backup の status.url で作る。札 workspace の
   disk の在る node のどれかに置かれ、Longhorn が中身を戻す(status.restoreRequired が false になるまで待つ)。
4. 使う機体の node に attach する。replica が別の node に置かれていれば、dataLocality best-effort がその node へ寄せる。
5. mount して使う。戻した volume は file system を持つので mkfs しない。

## 置く disk(札 workspace)

- 級は札 workspace の disk だけに置く。札を付ける disk は、Longhorn の disk を宣言する側が決めて付ける(この dir は付けない)。
- 付けるのは自宅の機体の SSD だけ。cloud の VPS(k3s-0〜3)の disk には付けない — 網の向こうの replica は書きが遅く、VPS は作業場所を受け
  ない。この repo で VPS の disk の札を宣言している `../disk-share/shares.yaml` に workspace が入らない事を test が確かめる。
- 2026-11-01 の時点で、札 workspace の disk は無い — 級を当てても、札を付けるまで volume は置かれない。zeus で今 Longhorn が使える SSD は
  札 hot の SATA SSD だけで、hot の disk は名簿の volume だけの物という決まりに当たる。作業場所の今の disk(zeus の USB の SSD・1.6 TB)は
  空きが 290 GB で、Longhorn が置く線(空きが容量の 25% — 設定 storage-minimal-available-percentage)を割っている。どちらに付けるかは
  持ち主の決め(card ki-e2e594d4ecfe の発言に答えを書く)。

## mount の係

worker の container は特権を持たないので、mkfs・mount・umount・fstrim は同じ Pod の sidecar `workspace-mount`(root・privileged の
native sidecar — 宣言と訳は `../nodes/zeus/node.yaml` の註)がする。今は zeus の worker にだけ在る(card ki-c1a429da5761)。

| 項目 | 決まり |
|---|---|
| 見せ方 | 係の `/work` は mountPropagation Bidirectional、worker の container の `/work` は HostToContainer(同じ volume work = host の `/mnt/fast_ssd_usb/agent-worker` — host の `/mnt/fast_ssd_usb` は shared の mount)。係が `/work/<path>` に付けた volume は host を通って worker の `/work/<path>` に見える |
| 入れ替え | mount は host の物なので、Pod の入れ替え(2 つの世代が同じ機体に並ぶ)でも残り、どちらの世代の worker にも係にも見える。係が止まっても外れない — 外すのは頼みだけ |
| 頼みの dir | env `WORKER_WORKSPACE_MOUNT_DIR`(= `/run/workspace-mount` — Pod ごとの emptyDir を係と worker が分け合う)。env の無い worker には係が無い |
| 頼み | `<鍵>.tmp` に 1 行 `<命令> <volume の名> <付ける先>` を書き、`<鍵>.req` へ名を替える(鍵は頼みごとに別。`.` で始めない)。命令は mount・umount・fstrim |
| 答え | 係は 0.2 秒ごとに `*.req` を見て script を読み直して走らせ、`<鍵>.out`(1 行目 = code 0 か 1・2 行目から訳と命令の出力)を置いて `<鍵>.req` を消す。頼み手は答えを読んだら消す |
| mount | device は host の `/dev/longhorn/<volume の名>`(Longhorn の ticket で attach した後に在る)。file system が無ければ `mkfs.ext4 -m 0 -E root_owner=1000:1000` を打ってから付ける。同じ volume が既に付いていれば何もしない |
| umount | 付いている物が名指した volume か照らしてから外す。使用中で外れなければ断る(lazy に外さない)。何も付いていなければ何もしない |
| fstrim | 付いている物を照らしてから `fstrim -v`(冷やす前 — 節「寿命」) |
| 断る物 | 3 つの命令の外・volume の名が英小文字・数字・`-` の外・付ける先が `/work` の下の在る dir でない・`.` か `..` か `//` を含む・symlink を通る・attach されていない・付ける先に別の物・volume が別の所に付いている・中身が ext4 でない(作り直さない) |
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
