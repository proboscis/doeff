;; 土台だけの image の約束(設計 worker-runtime-env.md 節 3.5・E13)の検。
;;
;;   deploy/base/Dockerfile が約束を守ること(作り直す理由の 2 種類・表に載せた OS の package だけ・Python の package も venv も無い)と、
;;   約束を破る変更(反例)を doeff_cluster.shared.entry.image_contract(判断は shared.core.image_rules)が赤にすることを確かめる。反例は本物の Dockerfile を 1 か所だけ変えて作る
;;   (fixture を別に持つと本物とずれるため)。
(require doeff-hy.macros [deftest defk <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import pathlib [Path])
(import doeff_cluster.shared.core.image_rules [image-contract-violations])

(val ROOT (. (Path __file__) (resolve) parent parent))
(val BASE-DOCKERFILE (/ ROOT "deploy" "base" "Dockerfile"))
(val APT-LINE "      ca-certificates git openssh-client tini build-essential pkg-config libssl-dev libpq5 \\")
(val LIBPQ-ROW "#   libpq5            (2) PostgreSQL の driver psycopg(binary の wheel なし)が実行時に読む libpq(PostgreSQL に繋ぐ job・#3692)\n")


(defk rules-of [text]
  {:pre [(: text str)] :post [(: % tuple)]}
  "違反の約束の名の列(反例が狙った約束で赤になることを比べるため)。"
  (<- found tuple (image-contract-violations text))
  (tuple (sorted (set (gfor v found v.rule)))))


(deftest test-the-base-dockerfile-keeps-the-contract
  (val text (.read-text BASE-DOCKERFILE :encoding "utf-8"))
  (assert (in APT-LINE text) "反例の差し替え先の行が本物に在る")
  (<- found tuple (image-contract-violations text))
  (assert (= found #()) found))


(deftest test-changes-other-than-the-two-reasons-are-red
  (val text (.read-text BASE-DOCKERFILE :encoding "utf-8"))
  (val cases
    #(#("pip で Python の package を足す" (+ text "RUN pip install requests\n") #("python-install"))
      #("uv sync で venv を焼く" (+ text "RUN uv sync --locked\n") #("python-install"))
      #("python の OS の package を足す" (.replace text APT-LINE (.replace APT-LINE "libssl-dev" "libssl-dev python3-yaml"))
        #("python-os-package"))
      #("表に無い OS の package を足す" (.replace text APT-LINE (.replace APT-LINE "libssl-dev" "libssl-dev curl"))
        #("os-package-undeclared"))
      #("venv を写す" (+ text "COPY --from=builder /opt/app/.venv /opt/app/.venv\n") #("python-env-copied"))
      #("PYTHONPATH を置く" (+ text "ENV PYTHONPATH=/opt/app\n") #("python-env-variable"))
      #("npm の道具の版を固定しない" (+ text "RUN npm install -g @anthropic-ai/claude-code\n") #("npm-unpinned"))
      #("作り直す理由の (2) を消す" (.replace text "#   (2) 業務の Python の package が新しい OS の library を要する時\n" "")
        #("reason-library-missing"))))
  (for [#(name changed expected) cases]
    (assert (!= changed text) name)
    (<- rules tuple (rules-of changed))
    (assert (= rules expected) (.format "{}: {}" name rules))))


(deftest test-a-declared-os-library-for-reason-two-is-allowed
  ;; 理由 (2): 業務の package が新しい OS の library を要する時は、表に理由 (2) で載せて入れる — 赤にならない。本物の Dockerfile の実例 =
  ;; libpq5(psycopg が実行時に読む・#3692 — 無い image では PostgreSQL に繋ぐ job が import psycopg で落ちた)。本物が赤に
  ;; ならないことは test-the-base-dockerfile-keeps-the-contract が確かめる。反例 = 表の行だけ消すと、apt の行の libpq5 が表に無い package で赤。
  (val text (.read-text BASE-DOCKERFILE :encoding "utf-8"))
  (assert (in LIBPQ-ROW text) "表に libpq5 の行が理由 (2) で在る")
  (assert (in APT-LINE text) "apt の行が libpq5 を入れる")
  (val without-row (.replace text LIBPQ-ROW ""))
  (assert (!= without-row text))
  (<- rules tuple (rules-of without-row))
  (assert (= rules #("os-package-undeclared")) rules))
