# /// script
# requires-python = ">=3.11"
# ///
"""doeff_agents の公開の名ごとの決め(残す effect・残す handler・境界・外す名と移し先)と呼び手の表を names.md に書く。

設計の記録(design.md)の表を、目録(evidence/inventory.json)と外の repo の呼び手(evidence/agora_callers.json)から
機械で組むため。決めは下の DECISIONS が唯一の置き場で、表はそこから作る(手で表を直さない)。
使い方: uv run --script gen_names.py <design の dir> <目録を数えた doeff の commit>
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

DIR = Path(sys.argv[1])
DOEFF_REV = sys.argv[2]


@dataclass(frozen=True)
class Decision:
    """1 つの名の決め: 区分(effect・handler・境界・外す)と、外す名の移し先と段。"""

    category: str  # effect | handler | boundary-value | boundary-handler-arg | remove
    destination: str = ""
    stage: str = ""  # A = card ki-4b53dcee28f3(手続き型の関数と重なり)・B = 後の card(object と内側の module)
    note: str = ""


E = Decision("effect")
H = Decision("handler")
V = Decision("boundary-value")
VH = Decision("boundary-handler-arg")


def rm(destination: str, stage: str, note: str = "") -> Decision:
    """外す名の決めを短く書くため。"""
    return Decision("remove", destination, stage, note)


EFFECTS = (
    "AgentEffect agent AttachAgentSession AttachAgentSessionEffect AwaitResult AwaitResultEffect CancelAgentSession "
    "CancelAgentSessionEffect Capture CaptureEffect CleanupAgentSession CleanupAgentSessionEffect FollowUp FollowUpEffect "
    "GetAgentSession GetAgentSessionEffect Launch LaunchEffect LaunchSession LaunchSessionEffect ListAgentSessions "
    "ListAgentSessionsEffect Monitor MonitorEffect ObserveAgentSession ObserveAgentSessionEffect PutAgentSession "
    "PutAgentSessionEffect Send SendEffect ReleaseSession ReleaseSessionEffect Stop StopEffect StopSession StopSessionEffect "
    "Events EventsEffect ExportContextEffect Interrupt InterruptEffect RedeemTurnCredentialEffect WarmSession WarmSessionEffect "
    "ReplaceSessionCredential ReplaceSessionCredentialEffect"
).split()

HANDLERS = (
    "agent_effectful_handler agent_effectful_handlers codex_agent_handler claude_agent_runtime_handlers "
    "fake_claude_agent_runtime_handlers claude_process_layer_handler fake_claude_process_layer_handler "
    "claude_agent_adapter_handler codex_process_layer_handler fake_codex_process_layer_handler codex_agent_adapter_handler "
    "daemon_agent_handler daemon_agent_handlers mock_agent_handler mock_agent_handlers"
).split()

VALUES = (
    "AgentSessionLifecycle AgentType AgentAttemptExhaustedError AgentDeadlineExceededError AgentError AgentNotAvailableError "
    "AgentSpec AgentSessionQuery AgentSessionSnapshot AgentTask AgentValidationErrorKind AgentValidationFailure AwaitOutcome "
    "AwaitStatus JSONSchema Observation TranscriptRef TurnRef SessionAlreadyExistsError SessionHandle SessionNotFoundError "
    "AgentCapabilityUnsupportedError AgentEvent AgentEventPage AgentInputFateEvent AgentTextDeltaEvent AgentTextEvent "
    "AgentThinkingStartedEvent AgentThinkingDeltaEvent AgentToolCallStartedEvent AgentToolInputDeltaEvent "
    "AgentStopHookFeedbackEvent AgentCompactionStartedEvent AgentCompactionEvent CompactionTrigger AgentCompactionEndedEvent "
    "CompactionOutcome AgentHookEvent AgentCallUsageEvent AgentSubagentCallUsageEvent AgentToolResultEvent AgentToolUseEvent "
    "ModelUsage ModelUsageGap ModelWindow ToolAnswer ToolCall AgentTurnCompleted AgentTurnEnd AgentTurnEndEvent AgentTurnFailed "
    "AgentTurnInterrupted AgentTurnLost AgentTurnUsage AgentAccountLimit AgentAccountRefusal AgentModelRefusal "
    "AgentProviderCapacity InputFateState NoTurnInFlightError ResumeTargetNotFoundError TurnCredential HomeTurnCredential "
    "TurnCredentialUnavailable TurnCredentialUnavailableError TurnInFlightError TurnInputMode ReplaceSessionCredentialOutcome "
    "SessionCredentialReplaced SessionProcessNotLive SessionOnHomeCredential ReplacementCredentialUnavailable "
    "HandlerMadeContextId NamedContextId"
).split()

DECISIONS: dict[str, Decision] = {
    **{n: E for n in EFFECTS},
    **{n: H for n in HANDLERS},
    **{n: V for n in VALUES},
    # 定義元を effect の module へ寄せて残す境界の値(今は手続き型の module に在る)
    "SessionStatus": Decision("boundary-value", note="定義を monitor.py から effects/agent.py へ移す(Observation.status の型)"),
    "AgentLaunchError": Decision("boundary-value", note="top-level の出元を session.py の別の class から effects.agent の class へ替える(handler が投げるのは effects.agent の方)"),
    "AgentReadyTimeoutError": Decision("boundary-value", note="AgentLaunchError と同じく出元を effects.agent へ替える"),
    # handler の引数の値の型(境界 — 区切り 1 の答えに従う)
    "ClaudeRuntimePolicy": VH,
    "MockSessionScript": Decision("boundary-handler-arg", note="mock_agent_handlers(sessions=…) の引数の値にする(session の名と最初の出力の欄を足す)"),
    "ScenarioStep": VH,
    "AgentdSessionClient": VH,
    "SessionBackend": Decision("boundary-handler-arg", note="段 B で handler の中へ(agent_effectful_handler が backend を自分で選ぶ)"),
    "AgentSessionRepository": Decision("boundary-handler-arg", note="段 B で台帳の handler(handlers/session_store.hy)を据える形へ"),
    "InMemoryAgentSessionRepository": Decision("boundary-handler-arg", note="段 B で台帳の handler の factory へ"),
    "JsonlAgentSessionRepository": Decision("boundary-handler-arg", note="段 B で台帳の handler の factory へ(file の I/O を handler の中へ)"),
    # --- 外す: 手続き型の関数(段 A)
    "launch_session": rm("effect Launch", "A"),
    "monitor_session": rm("effect AwaitResult(終わりまで)・Events(出来事)・Monitor(1 度の観測)", "A"),
    "async_monitor_session": rm("effect AwaitResult・Events", "A"),
    "capture_output": rm("effect AwaitResult の答え AwaitOutcome.result(結果)・Capture(端末の画面 — tmux の handler だけ)", "A"),
    "send_message": rm("effect Send・FollowUp", "A"),
    "stop_session": rm("effect Stop・StopSession", "A"),
    "session_scope": rm("effect Launch と try/finally の Stop", "A"),
    "async_session_scope": rm("effect Launch と try/finally の Stop", "A"),
    "attach_session": rm("effect AttachAgentSession", "A"),
    "get_adapter": rm("内側の module(_session)", "A"),
    "register_adapter": rm("内側の module(_session)— 新しい agent の種類は handler の module で足す(ADR-DOE-AGENTS-004 R2)", "A"),
    "AgentSession": rm("内側の module(_session)— 呼び手は SessionHandle だけを持つ", "A"),
    "interactive_session": rm("effect Launch・FollowUp・AwaitResult の組", "A"),
    "monitor_agent_to_completion": rm("effect AwaitResult", "A"),
    "monitor_once": rm("effect Monitor", "A"),
    "monitor_until_terminal": rm("effect AwaitResult", "A"),
    "quick_agent": rm("effect agent(AgentEffect — schema つきの 1 度の頼み)", "A"),
    "run_agent_to_completion": rm("effect Launch・AwaitResult・Stop の組か effect agent", "A"),
    "wait_and_monitor": rm("effect AwaitResult", "A"),
    "wait_agent_session": rm("effect ObserveAgentSession・GetAgentSession", "A"),
    "with_session": rm("effect Launch と try/finally の Stop", "A"),
    "AgentResult": rm("effect の答え AwaitOutcome", "A"),
    "OnStatusChange": rm("effect Events を読む loop(callback を持たない)", "A"),
    "MonitorState": rm("内側の module(_monitor)", "A"),
    "LaunchConfig": rm("effect Launch の平らな欄", "A"),
    "default_session_backend": rm("handler の中へ(agent_effectful_handler(s) が既定の backend を選ぶ)", "A"),
    "configure_mock_session": rm("handler の中へ(mock_agent_handlers(sessions=(MockSessionScript(…), …)))", "A", "module の大域の状態を持たない"),
    "get_mock_agent_state": rm("effect GetAgentSession・ListAgentSessions(mock の handler が答える)", "A"),
    "dispatch_effect": rm("内側の module(handlers/_dispatch)", "A"),
    "deterministic_session_id": rm("内側の module(effects/_ids)— 呼び手は AgentSpec.session_id を読む", "A"),
    "ensure_agentd": rm("内側の module(_agentd_client)— 呼び手は daemon_agent_handlers を据える(CLI の agentd ensure は残る)", "A"),
    "default_agentd_paths": rm("内側の module(_agentd_client)", "A"),
    "agentd_socket_is_supervised": rm("内側の module(_agentd_client)", "A"),
    "load_supervisor_declaration": rm("内側の module(_agentd_client)", "A"),
    # --- 外す: 同じ物の 2 つ目の名(段 A — 残す名へ寄せる)
    "production_handlers": rm("残す名 agent_effectful_handlers", "A", "同じ物の 2 つ目の名"),
    "mock_handlers": rm("残す名 mock_agent_handlers", "A", "同じ物の 2 つ目の名"),
    "headless_claude_agent_handlers": rm("残す名 claude_agent_runtime_handlers", "A", "同じ物の 2 つ目の名"),
    "fake_headless_claude_agent_handlers": rm("残す名 fake_claude_agent_runtime_handlers", "A", "同じ物の 2 つ目の名・agora-controllers が 10 file で使う"),
    "L2SessionHandle": rm("残す名 SessionHandle", "A", "同じ型の 2 つ目の名"),
    "ClaudeLaunchEffect": rm("内側の module(effects/_handler_effects)— handler の間だけの effect", "A"),
    "default_agent_handler": rm("内側の module(handlers/_production)— 返すのは doeff の handler でなく AgentHandler の object。呼び手は agent_effectful_handler を据える", "A", "AGENTS.md の節 Agent Authentication Boundary の 1 文を直す"),
    # --- 外す: handler の実装の class・object(段 A は top-level から消すだけ、module は段 B で内側へ)
    "AgentHandler": rm("内側の module(handlers/_production)", "A"),
    "TmuxAgentHandler": rm("内側の module(handlers/_production)— 呼び手は agent_effectful_handler(s)", "A"),
    "DaemonAgentHandler": rm("内側の module(handlers/_daemon)— 呼び手は daemon_agent_handler(s)", "A"),
    "MockAgentHandler": rm("内側の module(handlers/_testing)— 呼び手は mock_agent_handler(s)", "A"),
    "MockAgentState": rm("内側の module(handlers/_testing)", "A"),
    "ScenarioAgentHandler": rm("handler の factory scenario_agent_handler(steps)(新しく足す)", "A"),
    "AGENT_SESSIONS_KEY": rm("消す(呼び手 0)", "A"),
    "MOCK_AGENT_STATE_KEY": rm("消す(呼び手 0)", "A"),
    "AgentAdapter": rm("内側の module(_adapters)", "A"),
    "InjectionMethod": rm("内側の module(_adapters)", "A"),
    "LaunchParams": rm("内側の module(_adapters)", "A"),
    "SessionConfig": rm("内側の module(_session_backend)", "A"),
    "SessionInfo": rm("内側の module(_session_backend)", "A"),
    "StableTmuxSessionBackend": rm("内側の module(_tmux)", "A"),
    "TmuxSessionBackend": rm("内側の module(_tmux)", "A"),
    "TmuxError": rm("内側の module(_tmux)", "A"),
    "TmuxNotAvailableError": rm("内側の module(_tmux)— handler は AgentNotAvailableError で断る", "A"),
    "AgentSessionEvent": rm("内側の module(_session_store)", "A"),
    "AgentdClient": rm("内側の module(_agentd_client)", "A"),
    "AgentdClientError": rm("内側の module(_agentd_client)", "A"),
    "AgentdPaths": rm("内側の module(_agentd_client)", "A"),
    "AgentdProtocolError": rm("内側の module(_agentd_client)", "A"),
    "AgentdSessionList": rm("内側の module(_agentd_client)", "A"),
    "AgentdSessionParseWarning": rm("内側の module(_agentd_client)", "A"),
    "AgentdSupervisorConfigError": rm("内側の module(_agentd_client)", "A"),
    "AgentdSupervisorDeclaration": rm("内側の module(_agentd_client)", "A"),
    "AgentdUnavailableError": rm("内側の module(_agentd_client)", "A"),
    "LazyAgentdClient": rm("内側の module(_agentd_client)— 呼び手は daemon_agent_handler(s) の socket_path 等の引数", "A"),
}

# submodule の決め: 公開に残す module と、内側へ移す先(段)
MODULES: dict[str, tuple[str, str]] = {
    "doeff_agents": ("公開(残す)", "top-level — effect・handler・境界の値だけを出す"),
    "doeff_agents.effects": ("公開(残す)", "effect と境界の値"),
    "doeff_agents.effects.agent": ("公開(残す)", "effect と値の定義元。関数 deterministic_session_id・refuse_turn_capabilities と ClaudeLaunchEffect・LaunchTaskEffect(互換の別名)を外す"),
    "doeff_agents.handlers": ("公開(残す)", "handler の factory と handler の引数の値だけ。class と関数を外す"),
}
INTERNAL: dict[str, tuple[str, str]] = {
    "doeff_agents.session": ("A", "doeff_agents._session"),
    "doeff_agents.programs": ("A", "消す(呼び手を effect の組へ移す)"),
    "doeff_agents.monitor": ("A", "doeff_agents._monitor(SessionStatus は effects/agent.py へ)"),
    "doeff_agents.v2": ("A", "消す(同じ effect の 2 つ目の語彙・呼び手は自分の test 1 つ)"),
    "doeff_agents.session_backend": ("B", "doeff_agents._session_backend"),
    "doeff_agents.tmux": ("B", "doeff_agents._tmux"),
    "doeff_agents.shell": ("B", "doeff_agents._shell"),
    "doeff_agents.agentd_client": ("B", "doeff_agents._agentd_client"),
    "doeff_agents.adapters": ("B", "doeff_agents._adapters(AgentType・AgentSessionLifecycle は effects/agent.py へ)"),
    "doeff_agents.runtime": ("B", "doeff_agents._runtime(ClaudeRuntimePolicy・CodexRuntimePolicy は handlers から出す)"),
    "doeff_agents.session_store": ("B", "doeff_agents._session_store"),
    "doeff_agents.session_store_sql": ("B", "doeff_agents._session_store_sql"),
    "doeff_agents.io_effects": ("B", "doeff_agents._io_effects"),
    "doeff_agents.io_fake": ("B", "doeff_agents._io_fake"),
    "doeff_agents.io_handlers": ("B", "doeff_agents._io_handlers"),
    "doeff_agents.io_root": ("B", "doeff_agents._io_root"),
    "doeff_agents.agent_env": ("B", "doeff_agents._agent_env"),
    "doeff_agents.claude_home": ("B", "doeff_agents._claude_home"),
    "doeff_agents.ready_physics": ("B", "doeff_agents._ready_physics"),
    "doeff_agents.result_validation": ("B", "doeff_agents._result_validation"),
    "doeff_agents.mcp_server": ("B", "doeff_agents._mcp_server"),
    "doeff_agents.cli": ("B", "doeff_agents._cli(console script doeff-agents の入口を移す)"),
    "doeff_agents.sessionhost": ("B", "doeff_agents._sessionhost(console script doeff-sessionhost の入口を移す・ADR-004/007 の path と .semgrep.yaml の include を同じ便で直す)"),
    "doeff_agents.handlers.claude": ("B", "doeff_agents.handlers._claude"),
    "doeff_agents.handlers.codex": ("B", "doeff_agents.handlers._codex"),
    "doeff_agents.handlers.daemon": ("B", "doeff_agents.handlers._daemon"),
    "doeff_agents.handlers.effectful": ("B", "doeff_agents.handlers._effectful"),
    "doeff_agents.handlers.headless": ("B", "doeff_agents.handlers._headless"),
    "doeff_agents.handlers.headless_codex": ("B", "doeff_agents.handlers._headless_codex"),
    "doeff_agents.handlers.headless_compose": ("B", "doeff_agents.handlers._headless_compose(FakeReply・FakeClaudeWorld・ClaudeLiveLimitExceeded は doeff_agents.handlers から出す)"),
    "doeff_agents.handlers.mcp_server_loop": ("B", "doeff_agents.handlers._mcp_server_loop"),
    "doeff_agents.handlers.production": ("B", "doeff_agents.handlers._production"),
    "doeff_agents.handlers.session_store": ("B", "doeff_agents.handlers._session_store"),
    "doeff_agents.handlers.testing": ("B", "doeff_agents.handlers._testing"),
}

CATEGORY_JA = {
    "effect": "effect(残す)",
    "handler": "handler(残す)",
    "boundary-value": "境界: effect の値の型・例外(残す — 区切り 1 の答えに従う)",
    "boundary-handler-arg": "境界: handler の引数の値の型(残す — 区切り 1 の答えに従う)",
    "remove": "外す",
}


def main() -> None:
    """目録と決めを突き合わせて names.md を書き、決めの無い名・目録に無い名があれば止める。"""
    inv = json.loads((DIR / "evidence" / "inventory.json").read_text())
    agora = json.loads((DIR / "evidence" / "agora_callers.json").read_text())
    top = {e["name"]: e for e in inv["top"]}
    undecided = sorted(set(top) - set(DECISIONS))
    unknown = sorted(set(DECISIONS) - set(top))
    if undecided or unknown:
        raise SystemExit(f"決めの無い名 {undecided} / 目録に無い名 {unknown}")
    sub_mods = [m for m in inv["modules"] if m not in MODULES and m.count(".") >= 1]
    missing_mods = sorted(
        m for m in sub_mods
        if not any(m == k or m.startswith(k + ".") for k in INTERNAL)
    )
    if missing_mods:
        raise SystemExit(f"決めの無い submodule {missing_mods}")

    def count(name: str) -> tuple[int, int, int, int]:
        """表の 1 行に、置き場ごとの呼び手の file の数を並べるため。"""
        c = top[name]["callers"]
        return (len(c["src"]), len(c["tests"]), len(c["other_packages"]), len(c["docs_examples"]))

    by_cat = {cat: sorted(n for n, d in DECISIONS.items() if d.category == cat) for cat in CATEGORY_JA}
    lines = (
        "# doeff_agents の公開の名ごとの決めと呼び手(機械で生成 — tools/gen_names.py)",
        "",
        f"目録: doeff `{DOEFF_REV[:12]}` の作業木から `tools/agents_public_inventory.py` が数えた `evidence/inventory.json`。"
        f"外の repo: agora-controllers `{agora['rev'][:12]}` を `tools/agents_external_callers.py` が数えた `evidence/agora_callers.json`。",
        "呼び手の数 = その名を語として含み doeff_agents を名指す file の数(定義元の file を除く)。"
        "src = doeff_agents の中・tests = doeff-agents の tests と conformance と repo の tests・他 = packages/ の他の package・docs = docs と例。"
        "語の一致で数えるので、`agent`・`Stop`・`Send` のような短い語は多めに出る(残す名なので表の結論に響かない)。",
        "",
        "## 1. 区分ごとの数",
        "",
        "| 区分 | 名の数 |",
        "|---|---|",
        *(f"| {CATEGORY_JA[cat]} | {len(names)} |" for cat, names in by_cat.items()),
        f"| 計(top-level の `_LAZY_EXPORTS`) | {len(top)} |",
        "",
        "## 2. 残す effect",
        "",
        " ".join(f"`{n}`" for n in by_cat["effect"]),
        "",
        "## 3. 残す handler",
        "",
        " ".join(f"`{n}`" for n in by_cat["handler"]),
        "",
        "## 4. 境界(区切り 1 の問いの答えまで残す)",
        "",
        "| 名 | 区分 | 註 |",
        "|---|---|---|",
        *(f"| `{n}` | {CATEGORY_JA[DECISIONS[n].category]} | {DECISIONS[n].note} |"
          for n in by_cat["boundary-value"] + by_cat["boundary-handler-arg"]),
        "",
        "## 5. top-level から外す名 — 移し先・段・呼び手",
        "",
        "`_LAZY_EXPORTS` と `if TYPE_CHECKING:` の節(tests/test_package_root_typed_exports.py が照らす)の両方から消す。互換の別名は残さない。",
        "段 A = card ki-4b53dcee28f3 で外す。",
        "",
        "| 名 | 今の定義元 | 移し先 | 段 | 呼び手 src / tests / 他 / docs | agora-controllers | 註 |",
        "|---|---|---|---|---|---|---|",
        *(
            f"| `{n}` | `{top[n]['module']}` | {DECISIONS[n].destination} | {DECISIONS[n].stage} | "
            f"{' / '.join(str(x) for x in count(n))} | {len(agora['top'].get(n, []))} | {DECISIONS[n].note} |"
            for n in by_cat["remove"]
        ),
        "",
        "## 6. 外す名の呼び手の file(doeff の repo の中)",
        "",
        *(
            line
            for n in by_cat["remove"]
            for line in (
                f"### `{n}`",
                "",
                *(
                    f"- {place}: " + ", ".join(f"`{f}`" for f in files)
                    for place, files in top[n]["callers"].items() if files
                ),
                *(("- 呼び手なし",) if not any(top[n]["callers"].values()) else ()),
                *((f"- agora-controllers: " + ", ".join(f"`{f}`" for f in agora["top"][n]),) if agora["top"].get(n) else ()),
                "",
            )
        ),
        "## 7. submodule の決め",
        "",
        "公開に残す module は 4 つ。ほかは名を `_` で始まる内側の module へ移す(段 A = card ki-4b53dcee28f3・段 B = 後の card)。",
        "",
        "| module | 決め | 公開の名の数 | import する file(doeff の外の package / doeff-agents の中) | agora-controllers |",
        "|---|---|---|---|---|",
        *(
            f"| `{m}` | {MODULES[m][0]} — {MODULES[m][1]} | {len(inv['modules'].get(m, {}).get('names', []))} | "
            f"{len([f for f in inv['module_importers'].get(m, []) if not f.startswith('packages/doeff-agents/src/')])} / "
            f"{len([f for f in inv['module_importers'].get(m, []) if f.startswith('packages/doeff-agents/src/')])} | "
            f"{len(agora['modules'].get(m, []))} |"
            for m in MODULES
        ),
        *(
            f"| `{m}` | 段 {stage} — {dest} | "
            f"{sum(len(info['names']) for mm, info in inv['modules'].items() if mm == m or mm.startswith(m + '.'))} | "
            f"{len({f for mm in inv['module_importers'] if mm == m or mm.startswith(m + '.') for f in inv['module_importers'][mm] if not f.startswith('packages/doeff-agents/src/')})} / "
            f"{len({f for mm in inv['module_importers'] if mm == m or mm.startswith(m + '.') for f in inv['module_importers'][mm] if f.startswith('packages/doeff-agents/src/')})} | "
            f"{len({f for mm in agora['modules'] if mm == m or mm.startswith(m + '.') for f in agora['modules'][mm]})} |"
            for m, (stage, dest) in INTERNAL.items()
        ),
        "",
        "## 8. 内側へ移す submodule を import する file(doeff-agents の src の外)",
        "",
        *(
            line
            for m in INTERNAL
            for line in (
                f"### `{m}`",
                "",
                *(f"- `{f}`" for f in sorted({
                    f for mm in inv["module_importers"] if mm == m or mm.startswith(m + ".")
                    for f in inv["module_importers"][mm] if not f.startswith("packages/doeff-agents/src/")
                })),
                *(f"- agora-controllers: `{f}`" for f in sorted({
                    f for mm in agora["modules"] if mm == m or mm.startswith(m + ".") for f in agora["modules"][mm]
                })),
                "",
            )
        ),
    )
    (DIR / "names.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(" ".join(f"{cat}={len(names)}" for cat, names in by_cat.items()), f"total={len(top)}")


if __name__ == "__main__":
    main()
