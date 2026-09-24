"""届いた 1 通の出どころと返し方の判定(reply_channel の 1 点)— 設計 herdr-hud docs/design-checks/direct-chat-2026-09-24 §3・段 1。

operator の要件(2026-09-24 逐語): "using yuubin system internally for impl is okay but from the agent the difference must be
clear, to reply with ai tell or just reply" / "iraisho answer are messages"。

実弾(設計 §9): 前置きは「返事は ai tell --to <見出しの from>」と書き、見出しは operator の chat を他の会話の郵便と同じ
`[郵便 …・from=operator…]` で見せていた。agent は ai tell --to operator を断られ、ai reply --kind note と chat の出力の
両方で同じ中身を返した(c-6C7PK397… 09-21・c-42KW1PR2… 09-18・画面の e2e 9 件)。
"""

from __future__ import annotations

import hy  # noqa: F401  # registers the .hy importer
from doeff_agents.sessionhost.acp import judgment, reply_channel
from doeff_agents.sessionhost.acp.effects import AGORA_KINDS_NAMESPACE, MESSAGE_KIND, AcpRow
from doeff_agents.sessionhost.acp.input_source import (
    Mail,
    Notice,
    OperatorAnswer,
    OperatorChat,
    Request,
    TurnInputText,
    Verdict,
)
from test_sessionhost_acp import HeadlessWorld, World, bound_job, row

from doeff import run

AT = 1789446082000  # 2026-09-15 13:21:22 JST
AT_TEXT = "2026-09-15 13:21:22 JST"
OPERATOR_HEAD = reply_channel.SECTION_HEADINGS["operator"]
MAIL_HEAD = reply_channel.SECTION_HEADINGS["mail"]
REQUEST_HEAD = reply_channel.SECTION_HEADINGS["request"]
NOTICE_HEAD = reply_channel.SECTION_HEADINGS["notice"]


def source(spec: dict, status: dict | None = None, message_id: str = "lt-1") -> object:
    return run(reply_channel.input_source_of(message_id, spec, status))


def test_the_source_is_not_decided_by_the_sender_alone() -> None:
    """判定の表(規則 1〜6)。差出人だけで決めない — operator 発の依頼は依頼(反例 1)、報せの欄は差出人に勝つ(反例 8)。"""
    assert source({"kind": "note", "from": "operator", "at": AT}) == OperatorChat("lt-1", AT, None, ())
    # 画面の担い手の付け替えの報せ・「送り直す」は inReplyTo / refs 付きの operator の note — それでも operator の発言
    # (Mail に落とすと、返し方が ai tell --to operator = 断られる、の元の欠陥に戻る)
    assert source({"kind": "note", "from": "operator", "inReplyTo": "lt-A", "refs": ["lt-B"]}) == OperatorChat(
        "lt-1", None, "lt-A", ("lt-B",)
    )
    assert source({"kind": "answer", "from": "operator", "inReplyTo": "lt-Q", "refs": ["lt-Q.html"]}) == OperatorAnswer(
        "lt-1", None, "lt-Q", ("lt-Q.html",)
    )
    # 受付経由の依頼(TellAgora: from=operator・kind=ask)は依頼 — 担い手は郵便 id を要る
    assert source({"kind": "ask", "from": "operator", "class": "dev"}) == Request("lt-1", None, "operator", "dev", None)
    assert source({"kind": "ask", "from": "c-X", "parent": "lt-P"}) == Request("lt-1", None, "c-X", None, "lt-P")
    assert source({"kind": "accept", "from": "operator", "inReplyTo": "lt-R"}) == Verdict(
        "lt-1", None, "accept", "lt-R", "operator", True
    )
    assert source({"kind": "send-back", "from": "c-X", "inReplyTo": "lt-R"}) == Verdict(
        "lt-1", None, "send-back", "lt-R", "c-X", False
    )
    assert source({"kind": "withdraw", "from": "operator", "inReplyTo": "lt-Q"}) == Verdict(
        "lt-1", None, "withdraw", "lt-Q", "operator", True
    )
    assert source({"kind": "note", "from": "c-X", "inReplyTo": "lt-Q"}) == Mail("lt-1", None, "c-X", "note", "lt-Q")
    assert source({"kind": "withdraw", "from": "c-X"}) == Mail("lt-1", None, "c-X", "withdraw", None)
    # 機械の報せ: ACP の Messaging が spec.notice に理由を書く(差出人は受付の会話 id — 本物の依頼と見分けられない)
    assert source({"kind": "report", "from": "c-RECEPTION", "notice": "delivery-failed"}) == Notice(
        "lt-1", None, "c-RECEPTION", "delivery-failed"
    )
    assert source({"kind": "note", "from": "system"}) == Notice("lt-1", None, "system", "system")
    # 欄の無い検体は他の会話の郵便(発明しない)
    assert source({}) == Mail("lt-1", None, None, None, None)


def test_each_source_is_shown_with_its_own_way_to_reply() -> None:
    """見せ方の表(設計 §3 の replyChannelOf): 区切り + 項の見出し。"""

    def item(spec: dict, status: dict | None = None) -> TurnInputText:
        return run(reply_channel.turn_input_text_of("lt-1", {"at": AT, **spec}, "本文", status))

    assert item({"kind": "note", "from": "operator"}) == TurnInputText("operator", f"(lt-1・at={AT_TEXT})\n本文")
    assert item({"kind": "answer", "from": "operator", "inReplyTo": "lt-Q", "refs": ["lt-Q.html"]}) == TurnInputText(
        "operator", f"(依頼書 lt-Q.html の問い lt-Q への答え・lt-1・at={AT_TEXT})\n本文"
    )
    assert item({"kind": "ask", "from": "operator", "class": "dev"}) == TurnInputText(
        "request",
        f"[依頼 lt-1・class=dev・from=operator・parent=無し・at={AT_TEXT}・完了は ai reply lt-1 --kind report]\n本文",
    )
    assert item({"kind": "note", "from": "c-X"}) == TurnInputText(
        "mail",
        f"[郵便 lt-1・kind=note・from=c-X・inReplyTo=無し・at={AT_TEXT}"
        "・返事は ai tell --to c-X --in-reply-to lt-1 --kind note]\n本文",
    )
    assert item({"kind": "report", "from": "c-R", "notice": "delivery-failed"}) == TurnInputText(
        "notice", f"[報せ lt-1・理由=delivery-failed・from=c-R・at={AT_TEXT}・返事は要りません]\n本文"
    )
    send_back = item({"kind": "send-back", "from": "operator", "inReplyTo": "lt-R"})
    assert send_back.section == "operator"
    assert send_back.text.startswith("(報告 lt-R の差し戻し〔send-back〕")
    assert "ai reply <元の依頼の郵便 id> --kind report" in send_back.text
    # 扱う class が名乗りと違う拍は両方を出す(card ki-fa719b70d37c)
    rewritten = item({"kind": "ask", "from": "operator", "class": "dev"}, {"routing": {"servedClass": "kanban"}})
    assert "・class=kanban(名乗り dev)・" in rewritten.text
    # operator の発言の見出しは ai tell では返さないことを名指す
    assert "chat の出力でそのまま答えて" in OPERATOR_HEAD
    assert "`ai tell --to operator` は断られ" in OPERATOR_HEAD
    assert "chat の出力は差出人に届きません" in MAIL_HEAD


def test_a_turn_with_both_operator_chat_and_mail_is_not_ambiguous() -> None:
    """1 つの手番に operator の文と他の会話の郵便が混ざっても、区切りで分かれる(operator を先・郵便を後・区切りの見出しは 1 回)。"""
    items = (
        TurnInputText("mail", "[郵便 lt-2]\nb"),
        TurnInputText("operator", "(lt-1)\na"),
        TurnInputText("notice", "[報せ lt-4]\nd"),
        TurnInputText("operator", "(lt-3)\nc"),
    )
    assert run(reply_channel.inputs_text_of(items)) == (
        f"{OPERATOR_HEAD}\n\n(lt-1)\na\n\n(lt-3)\nc\n\n{MAIL_HEAD}\n\n[郵便 lt-2]\nb\n\n{NOTICE_HEAD}\n\n[報せ lt-4]\nd"
    )
    assert run(reply_channel.inputs_text_of(())) == ""
    # 前置きも区切りで囲む・その後に続きの案内と項
    assert run(judgment.first_turn_prompt_of("【前置き】\nstart", "", items[:2])) == (
        f"【前置き】\nstart\n\n{OPERATOR_HEAD}\n\n(lt-1)\na\n\n{MAIL_HEAD}\n\n[郵便 lt-2]\nb"
    )


def _mail_row(message_id: str, spec: dict, body: str) -> AcpRow:
    return row(AGORA_KINDS_NAMESPACE, MESSAGE_KIND, message_id, {"id": message_id, "at": AT, "body": body, **spec},
               {"state": "inbox"})


def test_the_launched_prompt_shows_the_operator_chat_without_the_mail_heading() -> None:
    """headless の launch: operator の chat は【operator の発言】の下に郵便の見出しなしで、他の会話の郵便は【他の会話からの郵便】の
    下に ai tell の返し方つきで、1 手番目の prompt に畳まれる。前置きは【前置き】で囲む。"""
    world = HeadlessWorld()
    world.acp.put_row(_mail_row("lt-op", {"kind": "note", "from": "operator"}, "了解とだけ答えて"))
    world.acp.put_row(_mail_row("lt-cx", {"kind": "note", "from": "c-OTHER"}, "進み具合は?"))
    world.acp.put_row(bound_job("j-1", inputs=["lt-cx", "lt-op"], created_at_ms=world.local.now_ms - 400))
    world.tick()
    prompt = world.sessions.launches[-1]["prompt"]
    assert isinstance(prompt, str)
    assert prompt == (
        f"【前置き】\nstart\n\n{OPERATOR_HEAD}\n\n(lt-op・at={AT_TEXT})\n了解とだけ答えて\n\n{MAIL_HEAD}\n\n"
        f"[郵便 lt-cx・kind=note・from=c-OTHER・inReplyTo=無し・at={AT_TEXT}"
        "・返事は ai tell --to c-OTHER --in-reply-to lt-cx --kind note]\n進み具合は?"
    )
    assert "from=operator" not in prompt


def test_the_tui_send_carries_the_section_heading_per_mail() -> None:
    """畳まない器(tui)は 1 通 1 送り — 各送りも区切りの見出しを持つ(温かい手番に前置きが無くても返し方が見える)。"""
    world = World()
    world.acp.put_row(_mail_row("lt-op", {"kind": "note", "from": "operator"}, "a"))
    world.acp.put_row(bound_job("j-1", inputs=["lt-op"], created_at_ms=world.local.now_ms - 400))
    world.tick()
    sent = [text for _sid, text, _awaiting in world.sessions.sends]
    assert sent == [f"{OPERATOR_HEAD}\n\n(lt-op・at={AT_TEXT})\na"]
