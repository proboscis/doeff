# doeff-validation

独立した検査を全部走らせ、落ちた検査の失敗を全部集めて `ValidationException` で知らせる。
doeff-traverse の上の薄い層で、検査の 1 件が traverse の 1 要素になる。逐次で走らせるか、並行か、
最初の失敗で止めるかは、組み立ての根に置く traverse の handler で選ぶ。

Hy の `validate` / `check` マクロは doeff-hy が持つ(`(require doeff-hy.macros [defk validate check])`)。
この package は実行時の部分(`validate`・`check`・失敗の型)を持つ。

設計の記録: [`docs/design/doeff-validation/design.md`](../../docs/design/doeff-validation/design.md)

## 書き直しの例

書き直す前(最初の `raise` で抜けるので、2 つ目以降の食い違いは見えない。理由は 1 つの文):

```hy
(defk bind-pending-job-to-chosen-node [job spec placement now]
  {:pre [(: job AgentJob) (: spec AgentJobSpec) (: placement AgentPlacement) (: now int)]
   :post [(: % WriteStatus)]}
  (when (or (!= job.key placement.request.key)
            (!= spec.subject placement.request.conversation)
            (not-in job.kind #(JobKind.TURN JobKind.SUMMARIZE))
            (!= job.phase Phase.PENDING)
            (is-not job.cancel None))
    (raise (AcpProtocolError "job does not match the placement request")))
  (write-of-agent-job-status job (bound-status placement job now)))
```

書き直した後。関数の入口で確かめる一致は、契約の `:pre` に `check` で書く。check は全部評価され、
落ちたものが全部 `ValidationException` に入る:

```hy
(require doeff-hy.macros [defk check])

(defk bind-pending-job-to-chosen-node [job spec placement now]
  "Pending の job を、配置で選んだノードと profile に結びつける状態の書き込み(CAS)を作る。"
  {:pre [(: job AgentJob) (: spec AgentJobSpec) (: placement AgentPlacement) (: now int)
         (check = job.key placement.request.key :reason PlacementMismatch.KEY)
         (check = spec.subject placement.request.conversation :reason PlacementMismatch.CONVERSATION)
         (check in job.kind #(JobKind.TURN JobKind.SUMMARIZE) :reason PlacementMismatch.KIND)
         (check = job.phase Phase.PENDING :reason PlacementMismatch.PHASE)
         (check is job.cancel None :reason PlacementMismatch.CANCELLED)]
   :post [(: % WriteStatus)]}
  (write-of-agent-job-status job (bound-status placement job now)))
```

落ちた時のメッセージ(式と各引数の評価した値、理由が並ぶ):

```
bind-pending-job-to-chosen-node pre-condition: 2 件の検査が落ちました:
  (= job.key placement.request.key)  job.key = 'k-1'  placement.request.key = 'k-2'  [PlacementMismatch.KEY]
  (= job.phase Phase.PENDING)  job.phase = <Phase.RUNNING: 'running'>  Phase.PENDING = <Phase.PENDING: 'pending'>  [PlacementMismatch.PHASE]
```

`:pre` から見えるのは引数だけなので、生の dict から読むのは呼び手の境界で済ませ、型のある値
(`job`・`spec`)を引数に受ける。

関数の本体の途中で確かめる時は `validate` を使う。効果を使う検査は、引数に `(! …)` を付ける:

```hy
(require doeff-hy.macros [defk validate check])

(defk ensure-conversation-bindable [job request]
  "配置要求の会話がまだ存在し、job がまだ Pending かを確かめる。"
  {:pre [(: job AgentJob) (: request PlacementRequest)] :post [(: % NoneType)]}
  (! (validate
       (check is-not (! (LookupConversation request.conversation)) None
              :reason PlacementMismatch.CONVERSATION)
       (check = job.phase Phase.PENDING :reason PlacementMismatch.PHASE))))
```

## 書き方

### `validate`

- 直下に並べるのは**独立した項目**だけで、`check` と Program(defk の呼び出し)の 2 種類がある。
  項目どうしは互いの結果に依らない(並行に走らせてよい単位)。
- 項目を全部走らせ、落ちた項目の失敗を全部集める。1 つでもあれば `ValidationException` を投げ、
  無ければ `None` を返す。
- `validate` は Program を作る式なので、defk の本体では `(! (validate …))` か `(<- _ (validate …))` で実行する。
- 直下に裸の `(<- …)` や `(! …)` を書くと、展開の時点で誤りになる。前の値に依る処理は、
  `validate` の前で済ませるか、helper の defk の中に自分の `validate` を持たせる。

### `check`

- `(check 演算子 引数 … :reason 理由)` か `(check 式 :reason 理由)`。`(check (= x 0))` は
  `(check = x 0)` と同じに分解し、式と各引数の値を記録する。`and` / `or` のような短絡・制御の形は
  分解せず、式と真偽だけを記録する(平たい形の `(check and a b)` は展開の時点で誤り)。
- 各引数は左から評価する。`(! …)` の印を付けた引数だけを効果として実行し、結果の値で比べる
  (`(check = (! (CountSeats node)) 0)` なら記録は「`(! (CountSeats node))` = 3」)。印の無い引数は、
  Program の値でもそのまま比べる。
- 引数か判定の評価が例外になったら、その検査の失敗(`CheckError`)として集める。他の検査は続く。
- 書ける所は **`validate` の直下と、defk / do! の `:pre` / `:post` の中だけ**。defk の本体・`fn`・
  内包表記の中に書くと展開の時点で誤りになる。検査がすべて `validate` か契約の所に並んで見え、
  helper の中に隠れない。

### 検査のまとまりを使い回す

helper の defk の中に自分の `validate` を持たせ、その呼び出しを外の `validate` に項目として並べる。
内側で落ちた `ValidationException` は、外側では 1 件の失敗(中の失敗の一覧を持つ)として数える。

```hy
(defk job-is-bindable [job]
  "job の種類と状態が、ノードに結びつけられるものかを確かめる。"
  {:pre [(: job AgentJob)] :post [(: % NoneType)]}
  (! (validate
       (check in job.kind #(JobKind.TURN JobKind.SUMMARIZE) :reason PlacementMismatch.KIND)
       (check = job.phase Phase.PENDING :reason PlacementMismatch.PHASE))))

(! (validate
     (job-is-bindable job)
     (check is-not (! (LookupConversation request.conversation)) None
            :reason PlacementMismatch.CONVERSATION)))
```

### Python

```python
import operator
from doeff_validation import check, perform, validate

yield validate(
    check(operator.is_not, perform(LookupConversation(request.conversation)), None,
          reason=PlacementMismatch.CONVERSATION),
    check(operator.eq, job.phase, Phase.PENDING, reason=PlacementMismatch.PHASE),
    job_is_bindable(job),        # 自分の validate を持つ Program
)
```

Python の `check` は `validate` に渡す値(`CheckSpec`)を作るだけで、単独では Program にならない
(`yield` できない)。これで「check は validate の直下にだけ」を型で表す。`perform(...)` が Hy の
`(! …)` に当たる。Python の引数は `validate` を呼ぶ時に評価される(Hy のように項目の中へ遅らせない)。

### 走らせ方 — 逐次・並行・fail-fast

```python
from doeff import run
from doeff_core_effects.scheduler import scheduled
from doeff_traverse import parallel, parallel_fail_fast, sequential

run(sequential()(program))                    # 逐次に全部走らせて集める
run(scheduled(parallel(8)(program)))          # 並行に全部走らせて集める
run(scheduled(parallel_fail_fast(1)(program)))  # 最初の失敗で止める(失敗は 1 件)
```

検査を書く側のコードは同じ。契約(`:pre` / `:post`)の check は関数の中でその場で順に評価するので、
traverse の handler は要らない。

## 失敗を値で受けたい時

`validate` が投げる `ValidationException` は、doeff の `Try` で値に変えられる:

```hy
(<- result (Try (validate …)))   ; Ok(None) か Err(ValidationException)
```

`ValidationException.failures` は落ちた検査の記録の列(`CheckFailure` = 判定が偽・`CheckError` =
評価が例外・入れ子の `ValidationException`)、`reasons` は理由の列。

## 例外の使い分け

- **予想できる業務の失敗**(入力や観測の食い違い): `validate` / 契約の `check` で集めるか、値
  (`Ok` / `Err`)で返す。
- **続けられない異常**(不変条件の破れ・プログラムの誤り): 例外のまま。`validate` の Program の項目の中で
  起きた `ValidationException` 以外の例外は、集めずにそのまま投げる。
- **try-catch は境界でだけ**: 例外を値に変えたい所(呼び手の入口・API の境界)で `Try` を使う。
  検査のたびに捕まえない。

## 型

| 名前 | 何か |
| --- | --- |
| `CheckSpec` | 検査 1 件の指定(`validate` の項目としてだけ意味を持つ) |
| `CheckFailure` | 判定が偽だった検査の記録(式の字面・評価した引数の値・理由) |
| `CheckError` | 引数か判定の評価が例外になった検査の記録 |
| `ValidationException` | 失敗を全部持つ例外(`failures`・`reasons`・`context`) |
| `perform(program)` | Python の check の引数を効果として実行する印 |
