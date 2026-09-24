"""turn_input.hy の公開面の型(送信待ちの列 — 運搬郵便が運ぶ入力の行の判定)。"""

from re import Pattern

from doeff import Program
from doeff_agents.sessionhost.acp.effects import AcpRow

CONVERSATION_INPUT_KIND: str
INPUT_ID_PATTERN: Pattern[str]
INPUT_STATE_PENDING: str
INPUT_STATE_TAKEN: str
INPUT_STATE_READ: str
INPUT_STATES_SKIPPED: set[str]
SKIP_CARRIED_BY_ANOTHER_MAIL: str
READ_EVIDENCE_HANDED: str
READ_EVIDENCE_INTERJECTED: str
READ_EVIDENCES: set[str]

# Programは現行VMでは非genericなunion。結果の型はHyのpost契約が検査する。
def carried_input_id_of(spec: dict) -> Program: ...
def is_input_ref(ref: str) -> Program: ...
def input_key_of(namespace: str, input_id: str) -> Program: ...
def latest_revision_of(spec: dict) -> Program: ...
def carrier_mail_of(status: dict) -> Program: ...
def input_carry_verdict_of(input_row: AcpRow | None, mail_id: str) -> Program: ...
def taken_status_of(status: dict, job_id: str, mail_id: str, rev: int, now_ms: int) -> Program: ...
def read_status_of(status: dict, mail_id: str, evidence: str, now_ms: int) -> Program: ...
