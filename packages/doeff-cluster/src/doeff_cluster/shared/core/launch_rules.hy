;;; worker と coordinator の起動の値(WorkerLaunch・CoordinatorLaunch — shared/intent/launch_model.hy)と、boot.sh が読む環境変数の
;;; 行の対応(#3366)。対応の表は WORKER-LAUNCH-FIELDS・COORDINATOR-LAUNCH-FIELDS の 1 か所 — 値から行への写し(配備する側の宣言を
;;; 書く handler が使う)と、行から値への読み(模擬の Flux が作り直す worker の欄を読む — 本番では boot.sh がする読み)と、行の名の
;;; 集合(配備する側の宣言の file で書き換える行)を、どれもこの表から引く。名の一覧を 2 つ目に書かない(#3366 単位 2 の条件)。
;;; 手元の機体で boot.sh を起こす模擬(sim/machine.hy の worker-boot-env)は模擬の worker(SimWorker)から自分の行を組む — 寄せるのは
;;; 別の変更。
(require doeff-hy.macros [val var defk <-])
(require doeff-hy.record [defenum defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})
(import dataclasses [dataclass])
(import enum [StrEnum])
(import doeff_core_effects.process_effects [EnvEntry])
(import doeff_cluster.shared.intent.launch_model [WorkerLaunch CoordinatorLaunch])


;; 欄の値の綴り方: text = そのまま・names = 名の列を "," で繋ぐ(書いた順のまま)・count = 10 進の整数。
(defenum LaunchFieldKind TEXT NAMES COUNT)


(defrecord LaunchField
  "起動の値の欄 1 つと、boot.sh が読む環境変数の名の対。field = 値の型の欄の名(dataclass の名)・optional = 値が空の時に行を
   持たない(boot.sh は無い名を空として読む)。"
  (#^ str env-name)
  (#^ str field)
  (#^ LaunchFieldKind kind)
  (#^ bool optional))


(val WORKER-LAUNCH-FIELDS
  #((LaunchField :env-name "WORKER_DOEFF_COMMIT" :field "doeff_commit" :kind LaunchFieldKind.TEXT :optional False)
    (LaunchField :env-name "WORKER_NAME" :field "name" :kind LaunchFieldKind.TEXT :optional False)
    (LaunchField :env-name "WORKER_PROVIDES" :field "provides" :kind LaunchFieldKind.NAMES :optional False)
    (LaunchField :env-name "WORKER_EXCLUSIVE" :field "exclusive" :kind LaunchFieldKind.NAMES :optional True)
    (LaunchField :env-name "WORKER_CAPACITY" :field "capacity" :kind LaunchFieldKind.COUNT :optional False)
    (LaunchField :env-name "WORKER_TASK_RESERVE" :field "task_reserve" :kind LaunchFieldKind.COUNT :optional False)))

(val COORDINATOR-LAUNCH-FIELDS
  #((LaunchField :env-name "WORKER_DOEFF_COMMIT" :field "doeff_commit" :kind LaunchFieldKind.TEXT :optional False)))

(defk worker-launch-names []
  {:pre [] :post [(: % (get frozenset str))] :tags {:context "doeff-cluster" :role "judgment"}}
  "配備する側の宣言の file の worker の Deployment で、値から書き換える行の名の集合を表から引くため。"
  (frozenset (gfor f WORKER-LAUNCH-FIELDS f.env-name)))


(defk coordinator-launch-names []
  {:pre [] :post [(: % (get frozenset str))] :tags {:context "doeff-cluster" :role "judgment"}}
  "配備する側の宣言の file の coordinator の Deployment で、値から書き換える行の名の集合を表から引くため。"
  (frozenset (gfor f COORDINATOR-LAUNCH-FIELDS f.env-name)))


(defclass LaunchEnvMissing [ValueError]
  "行から値を読む時に、省けない行(optional でない欄の名)が無い。黙って既定の値で埋めない。")


(defk shown-value [field value]
  {:pre [(: field LaunchField) (: value (| str int tuple))] :post [(: % str)]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "欄の値を環境変数の値の綴りにするため(欄の綴り方 kind ごと)。"
  (match field.kind
    LaunchFieldKind.TEXT (str value)
    LaunchFieldKind.NAMES (.join "," value)
    LaunchFieldKind.COUNT (str value)))


(defk read-value [field text]
  {:pre [(: field LaunchField) (: text str)] :post [(: % (| str int tuple))]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "環境変数の値の綴りを欄の値に戻すため(shown-value の逆)。names の空の綴りは空の列。"
  (match field.kind
    LaunchFieldKind.TEXT text
    LaunchFieldKind.NAMES (if text (tuple (.split text ",")) #())
    LaunchFieldKind.COUNT (int text)))


(defk launch-env [table launch]
  {:pre [(: table (get tuple #(LaunchField ...))) (: launch (| WorkerLaunch CoordinatorLaunch))]
   :post [(: % (get tuple #(EnvEntry ...)))] :tags {:context "doeff-cluster" :role "judgment"}}
  "起動の値を、表 table の順に boot.sh の名の行にするため。optional の欄は値が空なら行を持たない。"
  (var lines #())
  (for [f table]
    (val value (getattr launch f.field))
    (when (not (and f.optional (not value)))
      (<- shown str (shown-value f value))
      (:= lines (+ lines #((EnvEntry :name f.env-name :value shown))))))
  lines)


(defk launch-values [table env]
  {:pre [(: table (get tuple #(LaunchField ...))) (: env (get tuple #(EnvEntry ...)))] :post [(: % dict)]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "行の列 env を、表 table の欄の名 → 値に読むため。答えが dict なのは、値の型の構成子へ keyword として渡す境界だから(欄の名は
   表が持つ — 名の一覧を 2 つ目に書かない)。表に無い名の行は読まない(ROLE・COORDINATOR_URL など、起動の値の外の行)。省けない
   欄の行が無ければ LaunchEnvMissing。"
  (val missing (tuple (gfor f table :if (and (not f.optional) (not (any (gfor e env (= e.name f.env-name))))) f.env-name)))
  (when missing
    (raise (LaunchEnvMissing (.format "起動の行 {} が無い" (sorted missing)))))
  (var values {})
  (for [f table]
    (val text (next (gfor e env :if (= e.name f.env-name) e.value) ""))
    (<- value (| str int tuple) (read-value f text))
    (:= values (| values {f.field value})))
  values)


(defk worker-launch-env [launch]
  {:pre [(: launch WorkerLaunch)] :post [(: % (get tuple #(EnvEntry ...)))]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "worker 1 台の名乗りと版を、boot.sh が読む名の環境変数の行にするため(宣言の行を書く handler がこの値で行を作る)。"
  (<- env (get tuple #(EnvEntry ...)) (launch-env WORKER-LAUNCH-FIELDS launch))
  env)


(defk worker-launch-of-env [env]
  {:pre [(: env (get tuple #(EnvEntry ...)))] :post [(: % WorkerLaunch)]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "Deployment の env の行から worker の起動の値を読むため(模擬の Flux が作り直す worker の欄 — 本番では boot.sh がする読み)。
   値の検めは WorkerLaunch の構成子がする(起動の時に断られる値は、読む時に断る)。"
  (<- values dict (launch-values WORKER-LAUNCH-FIELDS env))
  (WorkerLaunch #** values))


(defk coordinator-launch-env [launch]
  {:pre [(: launch CoordinatorLaunch)] :post [(: % (get tuple #(EnvEntry ...)))]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "coordinator の版を、boot.sh が読む名の環境変数の行にするため。"
  (<- env (get tuple #(EnvEntry ...)) (launch-env COORDINATOR-LAUNCH-FIELDS launch))
  env)


(defk coordinator-launch-of-env [env]
  {:pre [(: env (get tuple #(EnvEntry ...)))] :post [(: % CoordinatorLaunch)]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "Deployment の env の行から coordinator の起動の値を読むため(模擬の Flux が作り直す coordinator の版)。"
  (<- values dict (launch-values COORDINATOR-LAUNCH-FIELDS env))
  (CoordinatorLaunch #** values))
