;;; 作業場所の volume の effect(型は doeff_cluster._shared.intent.workspace_volume_model)の本番の答え手 workspace-volumes-api — 手順は
;;; protocol/workspace_volumes(Longhorn の物と mount の係)、変わりの知らせの箱は foundation/volume_notices。protocol の層は foundation の
;;; 箱を読めないので、箱を作って手順へ渡す所を入口の層に置く(shared/entry/host_reader と同じ形)。
;;;
;;; 箱は答え手の session に 1 つ(session val — 最初に待つ手順が来た時に作る。Pod の中なら Longhorn の Volume と VolumeAttachment の
;;; 見張りの thread を立てる)。待たない effect(消す・読む)は箱を作らない。session val を使うので、外側に状態の handler(doeff_core_effects の
;;; state)が要る。検は同じ鍵(doeff_hy._session の session-key — 下の NOTICES-KEY)の値を状態に置いて、相手役の箱を渡す(見張りを立てない)。
(require doeff-hy.macros [defhandler <- val])
(val MODULE-TAGS {:context "doeff-cluster" :role "main"})
(import doeff_hy._session [session-key])
(import doeff_cluster._foundation.volume_notices [volume-notices])
(import doeff_cluster._shared.intent.workspace_volume_model [AttachWorkspaceVolume CoolIdleWorkspaceVolumes CoolingSweep CoolWorkspaceVolume
                                                           CreateWorkspaceVolume DetachWorkspaceVolume MountWorkspaceVolume
                                                           PurgeWorkspaceVolume ReadAttachedWorkspaceVolumes ReadWorkspacePlace
                                                           ReadWorkspaceRoom UmountWorkspaceVolume WorkspaceOutcome WorkspacePlace
                                                           WorkspaceRoom WorkspaceVolumeSite])
(import doeff_cluster._shared.protocol.workspace_volumes [attached-volume attached-volumes cooled-volume created-volume detached-volume
                                                        idle-cooling mounted-volume place-read purged-volume unmounted-volume
                                                        workspace-room])

;; 答え手の session の箱の鍵(検が相手役の箱を置く — 頭の註)。
(val NOTICES-KEY (session-key __name__ "workspace-volumes-api" "notices"))


(defhandler workspace-volumes-api [#^ WorkspaceVolumeSite site]
  ;; 引数に残す理由: 場所と待つ秒は本番(WORKSPACE-SITE)と検で違う(検は memory の file の上の頼みの dir と短い秒)。
  ;; 本番: Longhorn の物を worker の身元で読み書きし、mount は係へ頼む(protocol/workspace_volumes の頭の註)。外側に file・時計・環境・
  ;; 状態の答え手と、API server 宛ての要求を区画の CA で検める HTTP の答え手を要る。変わりの知らせの箱は session に 1 つ(頭の註)。
  (session val notices (! (volume-notices site.longhorn-namespace)))
  (CreateWorkspaceVolume [owner]
    (<- outcome WorkspaceOutcome (created-volume site notices owner))
    (resume outcome))
  (AttachWorkspaceVolume [owner]
    (<- outcome WorkspaceOutcome (attached-volume site notices owner))
    (resume outcome))
  (MountWorkspaceVolume [owner path]
    (<- outcome WorkspaceOutcome (mounted-volume site notices owner path))
    (resume outcome))
  (UmountWorkspaceVolume [owner path]
    (<- outcome WorkspaceOutcome (unmounted-volume site notices owner path))
    (resume outcome))
  (DetachWorkspaceVolume [owner]
    (<- outcome WorkspaceOutcome (detached-volume site notices owner))
    (resume outcome))
  (CoolWorkspaceVolume [owner]
    (<- outcome WorkspaceOutcome (cooled-volume site notices owner))
    (resume outcome))
  (PurgeWorkspaceVolume [owner]
    (<- outcome WorkspaceOutcome (purged-volume site owner))
    (resume outcome))
  (CoolIdleWorkspaceVolumes []
    (<- sweep CoolingSweep (idle-cooling site notices))
    (resume sweep))
  (ReadWorkspaceRoom []
    (<- room WorkspaceRoom (workspace-room site))
    (resume room))
  (ReadWorkspacePlace [owner path]
    (<- place WorkspacePlace (place-read site notices owner path))
    (resume place))
  (ReadAttachedWorkspaceVolumes []
    (<- attached tuple (attached-volumes site))
    (resume attached)))
