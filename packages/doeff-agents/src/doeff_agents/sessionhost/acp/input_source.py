"""手番に届いた 1 通の「出どころ」の型(判別可能な union)と、それを agent に見せる文の型。

設計の正本 = herdr-hud docs/design-checks/direct-chat-2026-09-24/design.md §3(段 1 = 見せ方と前置きの指示の直し)。
operator の要件(2026-09-24 逐語): "using yuubin system internally for impl is okay but from the agent the difference
must be clear, to reply with ai tell or just reply" / "iraisho answer are messages"。

判定(郵便の行 → 出どころ)と、出どころ → 見せる形・返し方は ``reply_channel.hy`` の 1 点だけが持つ。ここは型だけ。
⚠ 判定の外で ``from == "operator"`` のような比較を増やさない(返し方が 2 か所で割れる — 設計 §3「強制の方法」)。
"""

from dataclasses import dataclass
from typing import Literal

#: 返事の種類の閉語彙(検収・取り下げ)。
VerdictWord = Literal["accept", "send-back", "withdraw"]


@dataclass(frozen=True)
class OperatorChat:
    """operator が画面から直接打った文(今は from=operator・kind=note の郵便)。返し方 = この手番の chat の出力。

    ``in_reply_to`` / ``refs`` は画面が添えた参照(「送り直す」の元の郵便・担い手の付け替えの報せが指す依頼)で、
    返し方は変えない。"""

    message_id: str
    at: int | None
    in_reply_to: str | None
    refs: tuple[str, ...]


@dataclass(frozen=True)
class OperatorAnswer:
    """operator の依頼書への答え(kind=answer・from=operator)。どの問いへの答えかを見せる。返し方 = chat の出力。"""

    message_id: str
    at: int | None
    #: 問いの郵便 id(spec.inReplyTo)。
    question: str | None
    #: 依頼書(成果物)の参照(spec.refs)。
    refs: tuple[str, ...]


@dataclass(frozen=True)
class Verdict:
    """依頼者の検収(accept)・差し戻し(send-back)・取り下げ(withdraw)。依頼者が operator なら operator の発言の区切りに、
    会話なら他の会話からの郵便の区切りに置く。"""

    message_id: str
    at: int | None
    verdict: VerdictWord
    #: 元の郵便 id(検収 = 報告の id・取り下げ = 依頼の id — spec.inReplyTo)。
    target: str | None
    #: 依頼者(spec.from の逐語 — operator か会話 id)。
    requester: str
    from_operator: bool


@dataclass(frozen=True)
class Request:
    """依頼(kind=ask — operator が受付を経て出した依頼も含む)。完了は ``ai reply <id> --kind report``(反例 1: 差出人が
    operator でも、担い手には郵便 id が要る)。"""

    message_id: str
    at: int | None
    requester: str
    #: 見出しに出す class(扱う class が名乗りと違えば ``扱う(名乗り …)`` の形)。欄が無ければ None。
    served_class: str | None
    parent: str | None


@dataclass(frozen=True)
class Mail:
    """他の会話(または機械の名)からの郵便。返し方 = ``ai tell --to <from> --in-reply-to <id>``。chat の出力は届かない。"""

    message_id: str
    at: int | None
    sender: str | None
    kind: str | None
    in_reply_to: str | None


@dataclass(frozen=True)
class Notice:
    """機械からの報せ(ACP の Messaging が作った郵便 — spec.notice に理由の語・または差出人 system)。返事不要。"""

    message_id: str
    at: int | None
    sender: str | None
    reason: str


InputSource = OperatorChat | OperatorAnswer | Verdict | Request | Mail | Notice

#: 見せる区切りの閉語彙(並べる順もこの順 — operator の発言を先、郵便を後、報せを最後)。
Section = Literal["operator", "request", "mail", "notice"]
SECTION_ORDER: tuple[Section, ...] = ("operator", "request", "mail", "notice")


@dataclass(frozen=True)
class TurnInputText:
    """手番へ渡す 1 通の文(項の見出し + 本文)と、それを置く区切り。区切りの見出しは束ねる側(reply_channel の
    ``inputs-text-of``)が 1 回だけ付ける — 同じ区切りの項が何通あっても見出しは 1 つ。"""

    section: Section
    text: str


# ---------------------------------------------------------------------------
# 送信待ちの列(kind conversation-input)— 運搬郵便が運ぶ入力の行を運ぶかどうかの判定の型
# (設計 herdr-hud docs/design-checks/direct-chat-2026-09-24/design.md 段 2〜3・card acp:kanban-issue:ki-0bb4104cd8c2)。
# 判定は ``turn_input.hy`` の 1 点だけが持つ。ここは型だけ。
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NoInputRow:
    """郵便が入力の行を名指さない・名指した行が無い・行の形が読めない — 郵便の本文をそのまま運ぶ(旧い経路と競合の窓)。"""


@dataclass(frozen=True)
class CarryInput:
    """入力を運ぶ: 行が pending、またはこの郵便が既に取った(taken / read で carrier.mail がこの郵便)。本文は最新の版。"""

    text: str
    rev: int


#: 運ばない理由の閉語彙: 行の終わりの状態(withdrawn / answered / failed)か、別の郵便が取った(carried-by-another-mail)。
SkipReason = Literal["withdrawn", "answered", "failed", "carried-by-another-mail"]


@dataclass(frozen=True)
class SkipInput:
    """入力を運ばない(本文から外す)。郵便は「扱い済み」として配達報告に載せる(ACP の Messaging が運び直さないため)。"""

    reason: SkipReason


InputCarryVerdict = NoInputRow | CarryInput | SkipInput


@dataclass(frozen=True)
class TakenInput:
    """この手番が取った(taken を書けた)入力の行 1 つ — 器へ渡せた拍に read を書く材料。"""

    mail_id: str
    input_key: str
