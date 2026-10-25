# doeff-agents の公開 API を effect と handler だけにする形(card ki-ff041676eaa5)

- 取り組み: doeff-subpackages-effects-only の区切り ms-e888ce192f99(doeff-agents の公開 API を effect と handler だけにする)の card 1 / 2。
- 戦略と計画の合意: 利用者の発言 ci-VJFV7TDC7NRK59STYR26GGHZV8(決まり「doeff の subpackage が利用者に見せる公開 API は effect と handler だけ」)。
- 依頼: lt-Y7DNX7WD59YHAVSVDTPJ83EYE6(card scheduler)。実装は card ki-4b53dcee28f3、章 19 と動画の書き直しは取り組み doeff-publish の card ki-04da29e4b673・ki-3aa446791cc8 がこの記録を読む。
- 基にした doeff: main `4368f740fbda`(この記録の branch の基点)。外の呼び手は agora-controllers main `b73b9c61ec14`。

この記録の目的: doeff-agents を使う人に見せる effect と handler の一覧、外す名ごとの移し先と呼び手、章 19 の手順を effect の yield
と handler を据える事だけで書いた例を 1 か所に置き、実装の card と章 19 の書き直しがここだけを読んで進められるようにする。
名ごとの表(全 215 名の区分・外す名の移し先と呼び手の file の path・submodule ごとの決め)は機械で作った [names.md](names.md) に在る。
決めの置き場は `tools/gen_names.py` の `DECISIONS`・`INTERNAL` の 1 つで、names.md はそこから生成する(手で直さない)。

## 1. 決め

1. **公開に残す module は 4 つ**: `doeff_agents`(package の根)・`doeff_agents.effects`・`doeff_agents.effects.agent`(effect と値の
   定義元)・`doeff_agents.handlers`。ほかの submodule(`session`・`programs`・`monitor`・`tmux`・`sessionhost` など 61)は、名を `_` で
   始まる内側の module へ移すか消す(names.md 節 7)。
2. **利用者に見せる effect** は節 2 の 46 名(effect の class と、それを作る PascalCase の constructor)。今の `effects/agent.py` に在る
   物をそのまま使い、新しい effect は足さない。章 19 の 6 つの手順は全部この中の effect で書ける(節 4 — fake の runtime で走らせた)。
3. **利用者に見せる handler** は節 3 の 15 名(handler の factory)。handler の実装の class(`TmuxAgentHandler`・`DaemonAgentHandler`・
   `AgentHandler`・`MockAgentHandler`)と、同じ物の 2 つ目の名(`production_handlers`・`mock_handlers`・`headless_claude_agent_handlers`・
   `fake_headless_claude_agent_handlers`)は外す。
4. **境界の名は残す**: effect の欄と答えの値の型・handler が投げる例外(77 名)と、handler の factory の引数の値の型(8 名)。
   区切り 1(ms-6706ad95fcb0)は「effect の値の型・例外の型などの境界の扱いは、調べの後に依頼書で利用者と決める」としているので、
   この記録は境界の名を外さず、別の区分に分けて置く(names.md 節 4)。答えが「外す」なら、その時に別の card で移す。
5. **外す名は 69**(top-level から)。手続き型の関数(`launch_session`・`monitor_session`・`capture_output`・`stop_session`・
   `session_scope`・`run_agent_to_completion` ほか)は effect に置き換え、module の大域の状態を触る関数(`configure_mock_session`)は
   handler の factory の引数へ入れ、object と内部の名は内側の module へ移す。**互換の別名は残さない**(実装の担当の読みのとおり)。
6. **段を 2 つに分ける**(節 6): 段 A = card ki-4b53dcee28f3(top-level から 69 名を外す・手続き型の module 4 つを内側へ・呼び手を
   effect へ移す)、段 B = 後の card(残りの内部の module 57 を `_` の名へ移す — 機械的な改名だが ADR・`.semgrep.yaml`・console script・
   agora-controllers の import に及ぶ)。

## 2. 利用者に見せる effect

| 用途(章 19 の手順) | effect(constructor → class) | 答え |
|---|---|---|
| 起こす | `Launch(name, agent_type=…, work_dir=…, prompt=…)` → `LaunchEffect` | `SessionHandle` |
| 起こす(結果の形を決めて起こす) | `LaunchSession(AgentSpec(…, result_schema=…))` → `LaunchSessionEffect` | `SessionHandle` |
| 終わりまで見張る・結果を待つ | `AwaitResult(handle, timeout_seconds=…)` → `AwaitResultEffect` | `AwaitOutcome`(`result`・`turn_end`) |
| 状態の変わり目を読む | `Events(handle, after_seq=…, wait_seconds=…)` → `EventsEffect` | `AgentEventPage`(出来事の列と手番の終わり) |
| 今の状態を 1 度読む | `Monitor(handle)` → `MonitorEffect` | `Observation`(`status`) |
| 出力の画面を読む(端末の handler だけ) | `Capture(handle, lines=…)` → `CaptureEffect` | `str` |
| 送る(次の手番) | `Send(handle, message)` → `SendEffect`・`FollowUp(handle, message, mode=…)` → `FollowUpEffect` | `None`・`SessionHandle` |
| 手番だけを止める | `Interrupt(handle)` → `InterruptEffect` | `bool` |
| 止める | `Stop(handle)` → `StopEffect`・`StopSession(handle, reason=…)` → `StopSessionEffect` | `None` |
| 片づける | `ReleaseSession(handle)` → `ReleaseSessionEffect` | `None` |
| 1 度の頼み(schema つきの結果) | `agent(AgentTask(…))` → `AgentEffect` | 確かめた結果の値 |
| 前もって起こす・資格を替える・文脈を写す | `WarmSession`・`ReplaceSessionCredential`・`ExportContextEffect` | `bool`・`ReplaceSessionCredentialOutcome`・`str \| None` |
| 台帳を読む・書く | `GetAgentSession`・`ListAgentSessions`・`PutAgentSession`・`ObserveAgentSession`・`AttachAgentSession`・`CancelAgentSession`・`CleanupAgentSession`(と各 `…Effect`) | `AgentSessionSnapshot` ほか |
| handler が外へ出す(呼び手の handler が答える) | `RedeemTurnCredentialEffect` | `TurnCredential \| HomeTurnCredential \| TurnCredentialUnavailable` |

`Stop`/`StopSession`・`Send`/`FollowUp`・`Launch`/`LaunchSession` は対で残す。端末の handler と headless の handler が答える組が違い(節 3 の表)、
agora-controllers が両方を使う(`StopSessionEffect` 5 file・`FollowUpEffect` 4 file)。1 つに寄せるかは effect の語彙の変更で、
この区切り(公開の面を effect と handler だけにする)の外に置く。

## 3. 利用者に見せる handler

| handler の factory | 土台 | 据え方(外側に要る物) |
|---|---|---|
| `claude_agent_runtime_handlers(config_dir=…, env=…, live_limit=…, credential_floor_seconds=…)` | Claude の print mode(headless) | 時計(doeff-time)・slog・`live_limit` を数で渡す時は `ClaudeLiveLimitExceeded` の答え手・scheduler |
| `fake_claude_agent_runtime_handlers(responder=…, env={}, settings={})` | 同じ adapter の下に fake(process も API も無し) | 時計・scheduler |
| `claude_process_layer_handler`・`fake_claude_process_layer_handler`・`claude_agent_adapter_handler` | 上の組を層 2 と adapter に分けて違う深さに置く時 | 同上 |
| `codex_process_layer_handler`・`fake_codex_process_layer_handler`・`codex_agent_adapter_handler` | codex の app-server(headless) | 時計・scheduler |
| `agent_effectful_handlers(…)`・`agent_effectful_handler(…)` | tmux の端末(TUI の session) | `…_handlers` は時計を同梱 |
| `codex_agent_handler(…)` | tmux の端末の codex | 時計 |
| `daemon_agent_handlers(…)`・`daemon_agent_handler(…)` | session host(agentd)の socket | `…_handlers` は時計を同梱 |
| `mock_agent_handlers(…)`・`mock_agent_handler()` | 筋書きの mock(test 用) | `…_handlers` は止まる時計を同梱 |

handler ごとに答える effect(handler の module が受ける effect の名を読んだ表 — 「断る」は受けて `AgentCapabilityUnsupportedError` を
投げる物。実行で確かめたのは Claude の headless の fake の 5 つだけ — 節 4):

| effect | tmux・daemon・mock(`agent_handler_defhandler` の組) | Claude・codex の headless |
|---|---|---|
| Launch・Monitor・Send・Stop・FollowUp・AwaitResult・StopSession・ReleaseSession | 答える | 答える |
| Capture・AttachAgentSession | 答える | 断る(画面が無い) |
| Events・Interrupt・ReplaceSessionCredential・ExportContextEffect | 受けない(外の handler へ回る) | 答える |
| WarmSession | 断る(起こした時に既に入力を待つ) | 答える |
| LaunchSession・agent・Get/List/Observe/Cancel/Cleanup AgentSession | 答える | 受けない |
| RedeemTurnCredentialEffect | — | handler が外へ出す(呼び手が答える) |

章 19 は **Claude の headless の組**(`claude_agent_runtime_handlers`、例と test は `fake_claude_agent_runtime_handlers`)で書く事を
勧める: 状態の変わり目を polling でなく `Events` の待ちで読め(code-quality の「繰り返す loop は sleep や間隔で起きて確かめない」に合う)、
fake があるので読み手が API も process も無しに例を走らせられ、agora-controllers が本番で使う組と同じだから。端末の組は TUI の session を
人が覗きたい時の別の道として 1 節で触れる(節 4.4)。

## 4. 章 19 の手順を effect と handler だけで書いた例

全文は [example_chapter19.py](example_chapter19.py)。`uv run python docs/design/doeff-agents-effects-only-api-ki-ff041676eaa5/example_chapter19.py`
で、fake の Claude の runtime の上で走る(走らせた結果は `evidence/example_run.txt`)。設計の後は `FakeReply` を `doeff_agents` から読む
(今の main では `doeff_agents.handlers.headless_compose` に在るので、例の file はそこから読む)。

### 4.1 起こす・終わりまで見張る・出力を読む・止める(章 19 の Step 1・2)

```python
@do
def review(name: str, work_dir: Path, diff: str):
    handle = yield Launch(name, agent_type=AgentType.CLAUDE, work_dir=str(work_dir),
                          prompt=f"Review this diff:\n{diff}")          # 起こす
    try:
        outcome = yield AwaitResult(handle, timeout_seconds=600.0)    # 終わりまで見張る(待つのは handler の中)
        return outcome.result                                         # 出力を読む(AwaitOutcome.result)
    finally:
        yield Stop(handle)                                            # 止める
```

### 4.2 状態の変わり目に答える(Step 3 — callback の代わり)・並べて走らせる(Step 4・5 — asyncio の代わり)

```python
@do
def review_with_retry(name: str, work_dir: Path, diff: str):
    handle = yield Launch(name, agent_type=AgentType.CLAUDE, work_dir=str(work_dir),
                          prompt=f"Review this diff:\n{diff}")
    try:
        seq, tool_calls, retried = -1, 0, False
        while True:
            page = yield Events(handle, after_seq=seq, wait_seconds=30.0)  # 次の出来事か手番の終わりまで handler の中で待つ
            seq = page.next_seq
            tool_calls += sum(len(e.tool_calls) for e in page.events if isinstance(e, AgentToolUseEvent))
            match page.end:
                case None:
                    continue                                              # 手番がまだ走っている
                case AgentTurnFailed() if not retried:
                    retried = True
                    yield FollowUp(handle, "The previous turn failed. Please review again.")  # 状態の変わり目に答える
                case AgentTurnCompleted(result_text=text):
                    return (text, tool_calls, retried)
                case other:
                    raise RuntimeError(f"review {name} ended without a review: {other!r}")
    finally:
        yield Stop(handle)


@do
def review_all(work_dir: Path, diffs: tuple[tuple[str, str], ...]):
    tasks = ()
    for name, diff in diffs:
        tasks += ((yield Spawn(review(name, work_dir, diff))),)        # 並べて走らせる
    return (yield Gather(*tasks))
```

### 4.3 handler を据える(入口の 1 か所)

```python
def handlers() -> list:                                   # 例と test: fake の runtime(process も API も無し)
    return [sim_time_handler(clock=SimClock()),
            *fake_claude_agent_runtime_handlers(responder=responder, env={}, settings={})]

def real_handlers(home: Path, env: dict[str, str]) -> list:   # 本物の Claude: 組だけを替える(Program は同じ)
    return [sync_time_handler(), slog_handler,
            *claude_agent_runtime_handlers(config_dir=str(home), env=env,
                                           live_limit=None, credential_floor_seconds=600.0)]

results = run(scheduled(with_handlers(handlers(), review_all(work, (("a", "diff A"), ("b", "diff B"))))))
```

`real_handlers` は走らせていない(本物の Claude の家が要る)。`live_limit=None` は同時に生かす CLI の数を数えない宣言で、その時は
`ClaudeLiveLimitExceeded` の答え手が要らない。数で絞る時は答え手を外側に据える(`handlers/__init__.py` の docstring)。

### 4.4 章 19 の今の書き方との対応

| 章 19 の今(手続き型) | 置き換え(effect と handler) |
|---|---|
| `launch_session(name, LaunchConfig(agent_type=…, …))` | `yield Launch(name, agent_type=…, work_dir=…, prompt=…)` |
| `for`/`while` + `monitor_session` + `time.sleep` | `yield AwaitResult(handle, timeout_seconds=…)`(待ちは handler の中) |
| `capture_output(session, lines=N)` | `outcome.result`(`AwaitOutcome`)か `Events` の `AgentTextEvent`。画面の文字は端末の組の `Capture` だけ |
| `stop_session` / `session_scope` の出口 | `try: … finally: yield Stop(handle)` |
| `on_status_change` / `on_pr_detected` の callback | `Events` を読む loop と `match`、答えは `FollowUp`・`Send`・`Interrupt` |
| `SessionStatus.DONE` / `FAILED` の分岐 | `match outcome.turn_end`: `AgentTurnCompleted`・`AgentTurnFailed`・`AgentTurnInterrupted`・`AgentTurnLost` |
| `async_session_scope` + `asyncio.Semaphore` + `asyncio.gather` | `Spawn` + `Gather`。同時の数の上限は handler の `live_limit`(または doeff の semaphore の effect — docs/21) |
| 端末の session を人が覗く(tmux) | `agent_effectful_handlers()` を据え、`LaunchSession(AgentSpec(…, result_schema=…))` + `AwaitResult` + `StopSession`(結果は schema で確かめた値 — ADR-DOE-AGENTS-001 R2) |

## 5. 外す名と移し先(要約 — 名ごと・呼び手の path は names.md 節 5・6)

| 組 | 名 | 移し先 | 呼び手(doeff の repo) |
|---|---|---|---|
| 手続き型の session の関数(`session.py`) | `launch_session`・`monitor_session`・`async_monitor_session`・`capture_output`・`send_message`・`stop_session`・`session_scope`・`async_session_scope`・`attach_session` | effect に置き換える(節 4.4) | tests・他の package・docs(names.md 節 6) |
| 組み上げた Program(`programs.py`) | `run_agent_to_completion`・`with_session`・`monitor_until_terminal`・`monitor_agent_to_completion`・`wait_and_monitor`・`monitor_once`・`wait_agent_session`・`interactive_session`・`quick_agent`・`AgentResult` | effect の組(`AwaitResult`・`agent`・`ObserveAgentSession`)— module ごと消す | tests 少数・docs |
| 大域の状態を触る関数 | `configure_mock_session`・`get_mock_agent_state` | handler の中へ: `mock_agent_handlers(sessions=(MockSessionScript(session_name=…, …), …))` と台帳の effect | tests・examples・docs |
| backend・daemon の入口の関数 | `default_session_backend`・`ensure_agentd`・`default_agentd_paths`・`agentd_socket_is_supervised`・`load_supervisor_declaration`・`dispatch_effect`・`deterministic_session_id` | handler の中へ(`agent_effectful_handler(s)`・`daemon_agent_handler(s)` が自分で選ぶ)か内側の module | docs・tests |
| 同じ物の 2 つ目の名 | `production_handlers`・`mock_handlers`・`headless_claude_agent_handlers`・`fake_headless_claude_agent_handlers`・`L2SessionHandle` | 残す名へ寄せる | 他の package・agora-controllers |
| handler の実装の class と object | `AgentHandler`・`TmuxAgentHandler`・`DaemonAgentHandler`・`MockAgentHandler`・`MockAgentState`・`ScenarioAgentHandler`・`default_agent_handler`・`AgentSession`・`MonitorState`・`OnStatusChange`・`ClaudeLaunchEffect` | 内側の module(`ScenarioAgentHandler` は新しい factory `scenario_agent_handler(steps)`) | tests・docs |
| 端末・adapter・daemon の部品 | `TmuxSessionBackend`・`StableTmuxSessionBackend`・`TmuxError`・`TmuxNotAvailableError`・`SessionConfig`・`SessionInfo`・`AgentAdapter`・`InjectionMethod`・`LaunchParams`・`LaunchConfig`・`get_adapter`・`register_adapter`・`AgentdClient` ほか `Agentd*` 9 名・`LazyAgentdClient`・`AgentSessionEvent`・定数 2 | 内側の module | tests |

top-level に足す名(handler の引数の値として): `FakeReply`・`FakeClaudeWorld`(今は `handlers.headless_compose` — agora-controllers が
34 file で import)・`CodexRuntimePolicy`(`agent_effectful_handler` の引数なのに top-level に無い)。定義元を移す名: `SessionStatus`
(`monitor.py` → `effects/agent.py`)・`AgentType`・`AgentSessionLifecycle`(`adapters/base.py` → `effects/agent.py`)。

## 6. 段と順(実装の card への申し送り)

**段 A — card ki-4b53dcee28f3**(区切りの通過の条件 2 の「top-level から手続き型の関数が外れ、repo の中の呼び手が effect へ移る」):

1. `effects/agent.py` へ `SessionStatus`・`AgentType`・`AgentSessionLifecycle` の定義を移し(元の module は import する側になる)、
   `doeff_agents.handlers` から `FakeReply`・`FakeClaudeWorld`・`ClaudeLiveLimitExceeded`・`CodexRuntimePolicy` を出す。
2. top-level の `AgentLaunchError`・`AgentReadyTimeoutError` の出元を `effects.agent` へ替える(節 8 の 1)。
3. `mock_agent_handlers` に `sessions` の引数を足し、`configure_mock_session` の呼び手を移す。`scenario_agent_handler(steps)` を足す。
4. 呼び手(names.md 節 6 の path — doeff-agents の tests・examples・conformance、他の package、docs)を effect と残す handler へ移す。
5. top-level の `_LAZY_EXPORTS` と `if TYPE_CHECKING:` の節から 69 名を消す(names.md 節 5)。互換の別名は残さない。
6. `session.py`→`_session.py`・`monitor.py`→`_monitor.py`、`programs.py` と `v2/` は消す。`AGENTS.md` の節 Agent Authentication
   Boundary の 2 文(`doeff_agents.session_backend.default_session_backend()` と `doeff_agents.handlers.default_agent_handler()` を
   勧める文)を「effect を出し handler を据える」に直す。

**agora-controllers への響き**(doeff の作業木の兄弟として同じ checkout を読むので、doeff の main に入った時点で agora の検が赤になる):

- 段 A で響くのは 3 か所: `controllers/runtime_env/core/declaration.hy` が module の名 `doeff_agents.session` を並べる・
  `fake_headless_claude_agent_handlers` を 10 file が使う・`controllers/foundation/tests/test_managed_mark.hy` が handler の名を列で持つ。
  段 A のこの 3 組は、agora-controllers の card(新しく切る — 名を新しい名へ替える)が先に main に入ってから入れる。
- names.md の agora-controllers の列も語の一致で数えたので、`launch_session`(7)と `capture_output`(3)は同じ綴りの別物
  (agora 自身の Program `launch-session` と subprocess の引数 `capture_output=True`)で、agora はこの 2 つを使っていない。
- 段 B で響くのは `doeff_agents.handlers.headless_compose`(34 file)・`doeff_agents.adapters.base`(4 file)と declaration.hy の
  module の名の列。段 B の前に agora 側を `doeff_agents.handlers`・`doeff_agents.effects` からの import へ替える。

**段 B — 後の card**: 残りの内部の module を `_` の名へ移す(names.md 節 7・8)。`sessionhost` の改名は ADR-DOE-AGENTS-004・007 の
`:scope`・`:evidence`・deftest の import、`.semgrep.yaml` の include(`sessionhost/impls/**` ほか)、`docs/adr/enforcement-ledger.json`、
console script `doeff-sessionhost` の入口を同じ commit で直す(ADR-DOE-ENFORCE-001 R5)。`SessionBackend` と台帳の repository の
object(`InMemoryAgentSessionRepository`・`JsonlAgentSessionRepository`)は handler の中へ入れる(台帳は `handlers/session_store.hy` の
handler を据える形)。段 B は機械的な改名が多く、区切りの card の計画(2 枚)の外なので、取り組みの持ち主へ card を足す事を勧める。

## 7. 既に在る ADR との突き合わせ

| ADR | 関わる決め | この形との関係 |
|---|---|---|
| ADR-DOE-AGENTS-001(結果は AwaitResult だけ) | R1・R2: 利用者が受け取る結果は `AwaitResultEffect` の `AwaitOutcome.result` だけ。R3: schema の確かめと試し直しは handler の責務 | 合う。章 19 の「出力を読む」を `capture_output` から `AwaitOutcome.result` へ替える。`Capture` は端末の観測で結果ではない。例の `FollowUp` は失敗した手番への業務の頼み直しで、schema の試し直し(R3)を呼び手へ持ち込まない |
| ADR-DOE-AGENTS-003・004 R9(effect の面は認証を知らない) | 認証の材料は handler の型つきの binding だけ・`session_env` は認証を含まない上書き | 合う。effect の欄を変えない。家(`config_dir`)と env は入口で handler の factory に渡す |
| ADR-DOE-AGENTS-004 R1・R2・R7 | kind を足す = defhandler の module 1 つ・正典の executor は console script `doeff-sessionhost` | 合う。handler の module を `_` の名へ移しても kind の足し方は変わらない。段 B は console script の名を変えず、入口の module の path だけを移す |
| ADR-DOE-AGENTS-007(koine の session の面) | sessionhost の wire の語彙(session.adopt・turn の打刻・by_conversation) | 合う。wire は Python の公開 API でなく、段 B の改名は wire を変えない。ADR の `:scope`・deftest の import の path だけを同じ commit で直す |
| AGENTS.md の節 Agent Authentication Boundary | 応用の package は `doeff_agents.tmux` を import せず、`LaunchEffect` を出して `agent_effectful_handler()` を据える | 合う(強める)。この節の 2 文(`default_session_backend()`・`default_agent_handler()` を勧める文)は段 A で直す |

## 8. 読んでいて見つけた食い違い(段 A で直す)

1. top-level の `doeff_agents.AgentLaunchError`・`AgentReadyTimeoutError` は `session.py` の別の class で、handler(headless・tmux)が投げる
   `effects.agent` の class と別物。`except doeff_agents.AgentLaunchError` は handler の起動の失敗を捕まえない(agora-controllers は
   `doeff_agents.effects.agent` から読むので当たっていない)。
2. `effects/agent.py` の `LaunchTaskEffect = _DeprecatedLaunchTaskEffect` は互換の別名が公開の module に残っている。
3. 章 19 の Step 5 の docstring は `--watch` を名乗るが argparse に無い(書き直しで消える)。

## 9. 決めていない事

- 境界の名(節 1 の 4)を公開に残すか: 区切り 1 の問い(card ki-1c2bd5bea63f が書く依頼書)の答えに従う。
- console script(`doeff-agents`・`doeff-sessionhost`)を公開の面に数えるか: 同じく区切り 1 の境界の問い。
- `Stop`/`StopSession` などの対を 1 つに寄せるか: effect の語彙の変更で、この区切りの外(節 2)。
