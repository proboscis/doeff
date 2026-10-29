"""policy.hy の公開面の型(Python の読み手 = acp/runtime・shell・検)。

**部分的な stub**(host.pyi と同じ作り): Python から触る面だけを宣言する。Hy の側から
読む名はここに写さない — 写した日に 2 か所が黙ってずれる。Python の読み手が新しい名を
触る日は、その名をここへ足す(足さないと pyright が「その名は無い」と言う = 気づく形)。
"""

from dataclasses import dataclass

#: agent の境界で禁じる env の語彙(shell.py と検が名指す・写さずに**名指す**)。
PROVIDER_AUTH_ENV_KEYS: set[str]
PROVIDER_ROUTING_ENV_KEYS: set[str]
TURN_AUTH_ENV_KEYS: set[str]
BINDING_OWNED_ENV_KEYS: set[str]

def session_env_admission_error(session_env: dict[str, str], verb: str) -> str | None: ...

#: 席の家へ運ぶ共通の指示の**運び方**の閉語彙(card acp:kanban-issue:ki-62aa1f4e9c9c D11)。
CARRIED_SOURCE_FILE_TEXT: str
CARRIED_SOURCE_DIR_LINK: str
CARRIED_SOURCE_KINDS: set[str]

@dataclass(frozen=True)
class CarriedSource:
    """運ぶ物 1 つの綴りの**単一の定義点**(宣言の鍵・env の名・運び方・家の中の名・名乗りの語)。"""

    key: str
    env: str
    kind: str
    home_name: str
    label: str
    absent_word: str

#: 運ぶ物の名簿 — 宣言 → env → 起動の拍 → 席の家まで、どの点もこれを**回る**(列挙しない)。
CARRIED_INSTRUCTION_SOURCES: tuple[CarriedSource, ...]
#: 起動の拍の params が運ぶ鍵(器はこの名で受ける)。
CARRIED_INSTRUCTION_SOURCES_PARAM: str
CARRIED_SEAT_HOME_PARAM: str
#: 運ばれた 1 項目の dict の鍵。
CARRIED_ITEM_KEY: str
CARRIED_ITEM_KIND: str
CARRIED_ITEM_HOME_NAME: str
CARRIED_ITEM_TEXT: str
CARRIED_ITEM_PATH: str
