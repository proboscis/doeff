"""手番の入力に付く添付(画像)の型 — 器に依らない値ちょうど。

段 10 lane 10o(agora-redesign #96・依頼者の追補 2026-09-14・法 012 R21): agentd は添付を**型つき**で
器へ渡すだけで、CLI の綴り(claude の content の block・codex の input の項)は kind ごとの Dialogue
(``headless_protocol``)が組む。だからこの module には CLI の綴りが 1 語も無い — ``mime`` は郵便の
見出しの逐語を運ぶだけで、受ける種類の判断は ACP の契約(``message.spec.attachments`` の mime の閉語彙)と
画面の糊が持つ(agentd は種類を判断しない)。

受けられない器(器そのものが添付の段を持たない tmux / herdr・注入の段の無い Dialogue)は
``AttachmentRefused`` を返し、agentd がそれを条件 ``AttachmentIgnored`` に写す(黙って落とさない)。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TurnAttachment:
    """手番の入力に付く添付 1 つ。``data`` = base64 の逐語(記録の service の出来事の ``data`` の
    そのまま — agentd は解かない)・``mime`` = 郵便の見出しの逐語・``bytes`` / ``sha256`` = 見出しの
    検めの材料(器へは運ばない — 読んだ物が見出しと同じかを agentd が確かめる材料)・``name`` =
    元の file 名(見出しに無ければ空)。"""

    mime: str
    data: str
    bytes: int = 0
    sha256: str = ""
    name: str = ""


@dataclass(frozen=True)
class AttachmentRefused:
    """器が添付を受けなかった(理由つき)。呼び手(agentd)は条件 ``AttachmentIgnored`` に写す。
    本文そのものは届いている — 断りは添付だけの話。"""

    reason: str


@dataclass(frozen=True)
class TurnContent:
    """器へ渡す手番 1 回分の入力: 本文と添付の列。Dialogue はこの形だけを受け取り、綴りは自分で組む。"""

    text: str
    attachments: tuple[TurnAttachment, ...] = field(default_factory=tuple)

    def then(self, other: "TurnContent") -> "TurnContent":
        """継ぎ足し(codex の割り込みの本文が turn/start に積まれる前に次が来た時): 本文は空行で
        繋ぎ、添付は順に並べる。片方が空の本文なら空行を作らない。"""
        parts = [part for part in (self.text, other.text) if part]
        return TurnContent(
            text="\n\n".join(parts), attachments=self.attachments + other.attachments
        )
