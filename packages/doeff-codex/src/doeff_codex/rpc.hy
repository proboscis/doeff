;;; codex の app-server を起こす argv と、stdin へ書く JSON-RPC の 1 行の組み立て(送り出す側の JSON の境目)。
;;;
;;; app-server は stdio で 1 行 1 message の JSON-RPC を話す(codex 0.162.1 で実測 — tests/recorded/codex-0.162.1)。始めに
;;; initialize の要求と initialized の通知を送り、thread/start(か thread/resume)で thread を開き、turn/start でターンを始め、
;;; turn/interrupt で途中で止める。ここは行を作るだけで、いつ何を送るかは持たない(上の層の判断)。
;;;
;;; 要求の params は defwire の型で組み、dump-json で 1 行にする(手で dict を組まない)。既定値と同じ欄(None)は書かない — codex の
;;; 既定に任せる欄は送らない。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "codex" :role "foundation"})
(require doeff-hy.macros [defk <-])
(require doeff-hy.record [defenum defwire])
(import dataclasses [dataclass])
(import enum [StrEnum])
(import doeff_hy.json_value [OpaqueJson])
(import doeff_hy.wire [dump dump-json])

(val JSONRPC-VERSION "2.0")
;; app-server を stdio で待ち受けさせる前置きの後ろの引数(--listen の既定も stdio:// だが、待ち受けの形を argv で名乗る)。
(val APP-SERVER-ARGS #("app-server" "--listen" "stdio://"))

;; 道具を使う前に codex が許可を問う方針(thread/start の approvalPolicy の綴り — codex 0.162.1 の AskForApproval の文字列の値)。
(defenum ApprovalPolicy UNTRUSTED ON-FAILURE ON-REQUEST NEVER)
;; codex が道具を走らせる sandbox(thread/start の sandbox の綴り — codex 0.162.1 の SandboxMode)。
(defenum SandboxMode READ-ONLY WORKSPACE-WRITE DANGER-FULL-ACCESS)


;; --- 送る行の wire の型 -------------------------------------------------------------------------

(defwire RequestWire
  "JSON-RPC の要求 1 つ(id と method と params)。params は method ごとの型を dump した JSON を、中を読まずに運ぶ。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :reject}
  (#^ str jsonrpc)
  (#^ int id)
  (#^ str method)
  (#^ OpaqueJson params))

(defwire ResponseWire
  "codex からの要求(道具の許可の問いなど)への答え 1 つ(要求と同じ id と result)。result は method ごとの形を中を読まずに運ぶ。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :reject}
  (#^ str jsonrpc)
  (#^ (| int str) id)
  (#^ OpaqueJson result))

(defwire NotificationWire
  "JSON-RPC の通知 1 つ(答えを求めない — id が無い)。initialized は params を持たない。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :reject}
  (#^ str jsonrpc)
  (#^ str method))

(defwire ClientInfoWire
  "initialize で名乗る client の名と版(codex が User-Agent に入れる)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :reject}
  (#^ str name)
  (#^ str version))

(defwire InitializeParamsWire
  "initialize の params。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :reject}
  (#^ ClientInfoWire client-info))

(defwire ConfigWire
  "thread を開く要求の config(codex の config.toml の鍵を thread ごとに上書きする — 鍵は snake_case のまま)。
   model-auto-compact-token-limit = 会話を圧縮する context の大きさ(token)。"
  {:tags {:context "codex" :role "type"} :names :snake :unknown :reject}
  (#^ int model-auto-compact-token-limit))

(defwire ThreadStartParamsWire
  "thread/start の params: 作業の dir と、許可の方針・sandbox・model・config(None の欄は送らず codex の既定に任せる)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :reject}
  (#^ str cwd)
  (setv #^ (| ApprovalPolicy None) approval-policy None)
  (setv #^ (| SandboxMode None) sandbox None)
  (setv #^ (| str None) model None)
  (setv #^ (| ConfigWire None) config None))

(defwire ThreadResumeParamsWire
  "thread/resume の params: 続ける thread の id と、作業の dir・許可の方針・sandbox・model・config(新しい process で続ける時に
   thread/start と同じ方針を名乗り直す — 名乗らないと codex の既定へ戻り、答え手の無い許可の問いで止まりうる。None の欄は送らない)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :reject}
  (#^ str thread-id)
  (setv #^ (| str None) cwd None)
  (setv #^ (| ApprovalPolicy None) approval-policy None)
  (setv #^ (| SandboxMode None) sandbox None)
  (setv #^ (| str None) model None)
  (setv #^ (| ConfigWire None) config None))

(defwire TextInputWire
  "ターンの入力 1 つ(文字の入力)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :reject}
  (#^ str type)
  (#^ str text))

(defwire ImageInputWire
  "ターンの入力 1 つ(画像の入力 — url は data URL)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :reject}
  (#^ str type)
  (#^ str url))

(defwire TurnStartParamsWire
  "turn/start の params: thread の id と入力の列と、考えの深さ(None なら送らず codex の既定に任せる)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :reject}
  (#^ str thread-id)
  (#^ (get tuple #((| TextInputWire ImageInputWire) ...)) input)
  (setv #^ (| str None) effort None))

(defwire TurnSteerParamsWire
  "turn/steer の params: 走っているターンに足す入力の列と、そのターンの id(expected-turn-id — 違うターンが走っていれば codex が断る)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :reject}
  (#^ str thread-id)
  (#^ str expected-turn-id)
  (#^ (get tuple #((| TextInputWire ImageInputWire) ...)) input))

(defwire TurnInterruptParamsWire
  "turn/interrupt の params: 止める thread とターンの id。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :reject}
  (#^ str thread-id)
  (#^ str turn-id))


;; --- 組み立て -------------------------------------------------------------------------------

(defk app-server-argv [#^ tuple command]
  {:pre [(: command tuple) (> (len command) 0)] :post [(: % list)] :tags {:context "codex" :role "foundation"}}
  "app-server を stdio の JSON-RPC で起こす argv を作るため。command = 実行ファイルと前置きの引数(例 #(\"codex\")・
   #(\"codex\" \"-c\" \"model=…\"))— 前置きの後ろに app-server と待ち受けの形を足す。"
  (+ (list command) (list APP-SERVER-ARGS)))


;; 要求の params の型(method ごとに 1 つ)。
(val RequestParams (| InitializeParamsWire ThreadStartParamsWire ThreadResumeParamsWire TurnStartParamsWire TurnSteerParamsWire
                      TurnInterruptParamsWire))


(defk request-line [#^ int request-id #^ str method params]
  {:pre [(: request-id int) (: method str) (: params RequestParams)] :post [(: % str)] :tags {:context "codex" :role "foundation"}}
  "method ごとの params の型の値を、JSON-RPC の要求の 1 行(改行なし)にするため。"
  (<- params-json (dump params))
  (<- line (dump-json (RequestWire :jsonrpc JSONRPC-VERSION :id request-id :method method :params (OpaqueJson.of params-json))))
  line)


(defk initialize-request [#^ int request-id #^ str client-name #^ str client-version]
  {:pre [(: request-id int) (: client-name str) (: client-version str)] :post [(: % str)] :tags {:context "codex" :role "foundation"}}
  "app-server に最初に送る initialize の要求の行を作るため(client の名と版を名乗る)。"
  (<- line (request-line request-id "initialize"
                         (InitializeParamsWire :client-info (ClientInfoWire :name client-name :version client-version))))
  line)


(defk initialized-notification []
  {:pre [] :post [(: % str)] :tags {:context "codex" :role "foundation"}}
  "initialize の答えを受けた後に送る initialized の通知の行を作るため(これの後に thread を開ける)。"
  (<- line (dump-json (NotificationWire :jsonrpc JSONRPC-VERSION :method "initialized")))
  line)


(defk config-of [auto-compact-token-limit]
  {:pre [(: auto-compact-token-limit (| int None))] :post [(: % (| ConfigWire None))] :tags {:context "codex" :role "foundation"}}
  "thread を開く要求の config を作るため(上書きする鍵が無ければ None — config の欄を送らない)。"
  (if (is auto-compact-token-limit None)
      None
      (ConfigWire :model-auto-compact-token-limit auto-compact-token-limit)))


(defk thread-start-request [#^ int request-id #^ str cwd [approval-policy None] [sandbox None] [model None]
                            [auto-compact-token-limit None]]
  {:pre [(: request-id int) (: cwd str) (: approval-policy (| ApprovalPolicy None)) (: sandbox (| SandboxMode None))
         (: model (| str None)) (: auto-compact-token-limit (| int None))]
   :post [(: % str)]
   :tags {:context "codex" :role "foundation"}}
  "新しい thread を開く thread/start の要求の行を作るため。None の欄は送らない(codex の既定に任せる)。"
  (<- config (config-of auto-compact-token-limit))
  (<- line (request-line request-id "thread/start"
                         (ThreadStartParamsWire :cwd cwd :approval-policy approval-policy :sandbox sandbox :model model
                                                :config config)))
  line)


(defk thread-resume-request [#^ int request-id #^ str thread-id [cwd None] [approval-policy None] [sandbox None] [model None]
                             [auto-compact-token-limit None]]
  {:pre [(: request-id int) (: thread-id str) (: cwd (| str None)) (: approval-policy (| ApprovalPolicy None))
         (: sandbox (| SandboxMode None)) (: model (| str None)) (: auto-compact-token-limit (| int None))]
   :post [(: % str)]
   :tags {:context "codex" :role "foundation"}}
  "前の thread を続ける thread/resume の要求の行を作るため(新しい process で同じ thread のターンを続ける時 — 方針は thread/start と同じ
   値を名乗り直す)。"
  (<- config (config-of auto-compact-token-limit))
  (<- line (request-line request-id "thread/resume"
                         (ThreadResumeParamsWire :thread-id thread-id :cwd cwd :approval-policy approval-policy :sandbox sandbox
                                                 :model model :config config)))
  line)


(defk input-wires-of [#^ str text #^ tuple image-urls]
  {:pre [(: text str) (: image-urls tuple) (all (gfor url image-urls (isinstance url str)))]
   :post [(: % tuple)]
   :tags {:context "codex" :role "foundation"}}
  "ターンの入力の列を作るため: 文字の入力 1 つ(空の文字列で画像が在れば送らない — 画像だけの入力)と、画像の入力(data URL)を順に。"
  (+ (if (and (not text) image-urls) #() #((TextInputWire :type "text" :text text)))
     (tuple (gfor url image-urls (ImageInputWire :type "image" :url url)))))


(defk turn-start-request [#^ int request-id #^ str thread-id #^ str text #^ tuple image-urls [effort None]]
  {:pre [(: request-id int) (: thread-id str) (: text str) (: image-urls tuple) (: effort (| str None))]
   :post [(: % str)]
   :tags {:context "codex" :role "foundation"}}
  "thread の上でターンを 1 つ始める turn/start の要求の行を作るため(入力は文字と画像の data URL・effort は None なら送らない)。"
  (<- input (input-wires-of text image-urls))
  (<- line (request-line request-id "turn/start" (TurnStartParamsWire :thread-id thread-id :input input :effort effort)))
  line)


(defk turn-steer-request [#^ int request-id #^ str thread-id #^ str turn-id #^ str text #^ tuple image-urls]
  {:pre [(: request-id int) (: thread-id str) (: turn-id str) (: text str) (: image-urls tuple)] :post [(: % str)]
   :tags {:context "codex" :role "foundation"}}
  "走っているターンに入力を足す turn/steer の要求の行を作るため(codex はターンの次の区切りで読む・turn-id のターンが走っていなければ
   codex が断る)。"
  (<- input (input-wires-of text image-urls))
  (<- line (request-line request-id "turn/steer" (TurnSteerParamsWire :thread-id thread-id :expected-turn-id turn-id :input input)))
  line)


(defk turn-interrupt-request [#^ int request-id #^ str thread-id #^ str turn-id]
  {:pre [(: request-id int) (: thread-id str) (: turn-id str)] :post [(: % str)] :tags {:context "codex" :role "foundation"}}
  "走っているターンを途中で止める turn/interrupt の要求の行を作るため(ターンは状態 interrupted で終わる)。"
  (<- line (request-line request-id "turn/interrupt" (TurnInterruptParamsWire :thread-id thread-id :turn-id turn-id)))
  line)


(defk server-response-line [request-id #^ OpaqueJson result]
  {:pre [(: request-id (| int str)) (: result OpaqueJson)] :post [(: % str)] :tags {:context "codex" :role "foundation"}}
  "codex からの要求(ServerRequest)に、同じ id で答える行を作るため(result は上の層が method で選んだ形)。"
  (<- line (dump-json (ResponseWire :jsonrpc JSONRPC-VERSION :id request-id :result result)))
  line)
