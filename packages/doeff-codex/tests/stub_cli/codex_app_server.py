"""替え玉の codex app-server(本番の handler の検 — 本物の codex も上流も要らない)。

stdin の JSON-RPC の要求に、録った実物の行(tests/recorded/codex-0.162.1)の形で答える。thread とターンの id は、録った id を
この process が振った id へ差し替える。

- initialize → 録った答え / initialized → 何も返さない。
- thread/start → 新しい thread の id で録った答えと thread/started / thread/resume → 名指された id で同じ形の答え。
- turn/start → 新しいターンの id で答え、入力の文字で決めた筋書きを流す:
  - SLOW が在れば、答えの文字の途中(item/agentMessage/delta)を 0.05 秒ごとに出し続け、turn/interrupt を受けたら録った止めの終わり
    (状態 interrupted の turn/completed)を出す。
  - IMAGES_PHRASE が在れば、入力の画像の数を ``IMAGES <数>`` と答える(画像が turn/start の入力に載ったかを見る)。
  - SETTINGS_PHRASE が在れば、thread を開いた要求の model と config の圧縮の閾値と、turn/start の effort と、この process を起こした
    argv の --disable の機能の名を ``SETTINGS effort=<値> compact=<値> model=<値> disabled=<名,…>`` と答える(宣言が要求の行と argv に
    載ったかを見る — 名乗らない欄は None・切る機能が無ければ disabled=)。
  - HOME_PHRASE が在れば、この process の CODEX_HOME の形を ``HOME dir=<権限> auth=<権限> account=<auth.json の口座の id>
    config=<link|file|none> sessions=<link|dir|none>`` と答える(借りた資格の家の形を子の process から見る — 資格の値は答えない)。
  - SPANNING の目印(BG-COMMAND・BG-WAIT)が在れば、2 つのターンにまたがる録り(後ろで走る命令の始まりのターンと、その命令が終わる
    次のターン)のうち、目印のターンの通知を流す。前のターンを名乗る遅れた行(命令の終わり)は、この process が前のターンに振った id へ
    差し替える。
  - REPLAYED の目印(USAGE-LIMIT・RATE-LIMIT・TOOL-CALLS・OTHER-TOOLS・CODE-MODE)が在れば、その目印で録ったターンの通知
    (turn/start の答えの後 〜 そのターンの turn/completed)をそのまま流す(利用枠切れ・429 の断りで失敗したターン・命令の成功と失敗と
    apply_patch と web 検索の道具の item を持つターン・MCP と動的な道具と画像を見る道具と子の agent の道具の item を持つターン・code
    mode の host を使えない codex の、文だけを返すターン — 子の agent の thread の行(子のターンの始まりと終わりを含む)も録った順の
    まま混ぜて流す)。
  - PREFACED の目印(CODE-MODE)が在れば、その目印で録った 1 つ目のターンの前の警告(thread を開いた答えと turn/start の答えの間の
    warning の通知 — code mode が使えない警告を含む)を、turn/start の答えより先に書く(録った順)。
  - どれでもなければ、録った 1 つ目のターンの通知(turn/started 〜 turn/completed)をそのまま流す。
- turn/steer → SLOW のターンが走っていて expectedTurnId がそのターンなら受けて、足した文字を ``steered:<文字> `` の答えの文字の
  途中として出す(fake の handler と同じ規則)。走っていなければ JSON-RPC の誤りで断る。STEER_REFUSALS の目印が在るターンは SLOW と
  同じに走り、turn/steer を目印の断り方で断る(本物の codex 0.162.1 を、偽の上流でターンの終わりと steer を競わせて測った断り方 —
  終わりの通知が断りより先に届く順も、断りが先で終わりの通知が後の順も起きた。断りの文は binary の文字列で確かめた)。
- 引数は --disable の機能の名(SETTINGS の答え)と --resume-history-bytes の数と --ignore-exclude-turns だけを読む(app-server
  --listen stdio:// は読まない)。
  --resume-history-bytes <数> が在れば、thread/resume の答えの thread の turns(録った答えでは thread の最後の欄)に、その数の
  bytes ほどの履歴(1 MB ほどの文字を持つターンの列)を載せ、1 行のまま少しずつ書く(本物の codex が再開する session の履歴全体を
  答え 1 行で運ぶ形)。ただし要求が excludeTurns: true を名乗った時は載せない(本物の codex 0.162.1 の v2/ThreadResumeParams の規則 —
  履歴を空にして thread の欄だけを返す)。--ignore-exclude-turns が在れば excludeTurns を無視して載せる(この欄を知らない版の codex の
  模型 — 読み手の 1 行の上限の検は、要求の側の工夫に依らず上限が守る事を見る)。
- 状態の DB(本物の codex 0.162.1 で測った形 — doeff_codex.credential_home の頭の註): 起動の時、置き場($CODEX_SQLITE_HOME、
  無ければ $CODEX_HOME)の state_5.sqlite が無ければ、sessions の rollout を全部読んで DB を作る(backfill — 読んだ bytes を数える。
  環境変数 STUB_BACKFILL_SECONDS が在れば、作る間その秒だけ待つ)。ほかの起動が DB を作っている最中(作りの目印の file が在る)なら、
  本物と同じ文を stderr に書いて code 1 で降りる。thread/start は sessions に rollout を 1 本書き、DB の行に家を通る path
  ($CODEX_HOME/sessions/…)を書く。thread/resume は、DB の行の path が無ければ本物と同じ誤り(no rollout found for thread id)で断り、
  行が無ければ sessions の file を名で探して行を作る(見つからなければ今までどおり答える)。STATE_PHRASE が在れば
  ``STATE backfilled=<この起動が読んだ bytes> rows=<DB の行の数>`` と答える。
- 環境変数 STUB_ID_PREFIX が在れば、thread とターンの id の頭に足す(1 つの host に開いた複数の会話が同じ id に衝突しないように —
  この process は id を 1 から数えるので、無ければ会話ごとに stub-thread-1 になる)。
"""

import json
import os
import sqlite3
import sys
import threading
from pathlib import Path

RECORDED = Path(__file__).resolve().parent.parent / "recorded" / "codex-0.162.1"
DELTA_PAUSE_SECONDS = 0.05
MAX_SLOW_PIECES = 400
IMAGES_PHRASE = "Count the attached images."
SETTINGS_PHRASE = "Tell the settings."
HOME_PHRASE = "Tell the home."
STATE_PHRASE = "Tell the state."
STATE_DB = "state_5.sqlite"
# 本物の codex 0.162.1 が、DB を作る最中のほかの起動と重なった時に stderr に書いて降りた文の頭。
STATE_INIT_FAILED = "Error: failed to initialize sqlite state runtime"
# 本物の codex 0.162.1 が、DB の行の rollout の path が無い thread の thread/resume を断った誤りの文の頭。
NO_ROLLOUT = "no rollout found for thread id"
# DB を作る間の長さの模型(本物の codex は記録の全部を読む間 DB を作りかけのまま持つ — 条件の待ちではなく、答えの文字の間合いと同じ
# 外の時間の模型)。鳴らさない Event の wait で、STUB_BACKFILL_SECONDS の秒だけ作りかけを保つ。
BACKFILL_PACE = threading.Event()
STEERED_PREFIX = "steered:"
# 入力の目印 → 録った筋書きの名(その筋書きのターンの通知をそのまま流す)。
REPLAYED = {"USAGE-LIMIT": "usage-limit", "RATE-LIMIT": "rate-limit", "TOOL-CALLS": "tool-calls", "OTHER-TOOLS": "other-tools",
            "CODE-MODE": "code-mode-unavailable"}
# 入力の目印 → 2 つのターンにまたがる録りの名と、そのターンを始めた録りの要求の id(turn/start)。
SPANNING = {"BG-COMMAND": ("background-command", 3), "BG-WAIT": ("background-command", 4)}
# 入力の目印 → 録った筋書きの名(1 つ目のターンの前の警告を turn/start の答えより先に書く — そのターンの通知は REPLAYED が流す)。
PREFACED = {"CODE-MODE": "code-mode-unavailable"}
# 履歴を載せた thread/resume の答えの、ターン 1 つが持つ文字の数(1 行を少しずつ書く単位)。
HISTORY_PIECE_CHARS = 1_000_000
# 録った答えの turns の欄に置き、書く時に履歴の列へ差し替える目印。
HISTORY_MARK = "__stub-history__"
# 走っていないターンへの turn/steer を断る JSON-RPC の誤りの code(invalid request)。
NOT_STEERABLE_CODE = -32600
# 入力の目印 → turn/steer の断り方(断りの前にターンの終わりの通知を書くか, 断りの文)。目印のターンは SLOW と同じに止めまで走り、
# 断りの文の {expected} は steer が名指したターンの id。
STEER_REFUSALS = {
    # ターンの終わりの通知が先に届き、断りが後に届く順(本物の codex の実測の 1 つ目)。
    "STEER-ENDED-FIRST": (True, "no active turn to steer"),
    # 断りが先に届き、終わりの通知が後(5〜200 ms ほど後)に届く順(実測の 2 つ目 — 断りを読んだ時点では終わりの通知がまだ無い)。
    "STEER-REFUSED-FIRST": (False, "no active turn to steer"),
    # 別のターンが走っていると名乗る断り(名指したターンの id が走っているターンと食い違う)。
    "STEER-OTHER-TURN": (False, "expected active turn id `{expected}` but found `stub-other-turn`"),
    # codex の版で文が変わっても、終わりの通知を読んでいれば走っていないと読める断り。
    "STEER-ENDED-UNNAMED": (True, "the turn is shutting down"),
    # 走っているターンが足せない種類だという断り(ターンは走り続ける — 走っていない断りと読み違えない)。
    "STEER-NOT-STEERABLE": (False, "cannot steer a review turn"),
}
# DB の行の rollout の path が無い thread の thread/resume を断る誤りの code(本物の codex 0.162.1 の答え — invalid request)。
NO_ROLLOUT_CODE = -32600
# rollout の file の名の頭(rollout-<刻 19 字>-<thread の id>.jsonl)の字数。
ROLLOUT_HEAD_CHARS = len("rollout-2026-10-19T00-00-00-")


def disabled_features(argv: list[str]) -> list[str]:
    """起こされた argv の --disable の後ろの機能の名を順に取るため(会話の宣言の切る機能が argv に載ったかを答える)。"""
    return [name for flag, name in zip(argv, argv[1:]) if flag == "--disable"]


def resume_history_bytes(argv: list[str]) -> int:
    """起こされた argv の --resume-history-bytes の後ろの数を取るため(無ければ 0 — 履歴を載せない)。"""
    return next((int(size) for flag, size in zip(argv, argv[1:]) if flag == "--resume-history-bytes"), 0)


def carries_history(argv: list[str], params: dict[str, object]) -> bool:
    """thread/resume の答えに履歴を載せるか決めるため(--resume-history-bytes が在り、要求が excludeTurns: true を名乗らないか、
    --ignore-exclude-turns で名乗りを無視する時)。"""
    if not resume_history_bytes(argv):
        return False
    return "--ignore-exclude-turns" in argv or params.get("excludeTurns") is not True


def said_text(inputs: list[dict[str, object]]) -> str:
    """ターンの入力の列から文字の入力をつないだ文を取るため。"""
    return " ".join(str(item.get("text", "")) for item in inputs if item.get("type") == "text")


def image_count(inputs: list[dict[str, object]]) -> int:
    """ターンの入力の列の画像の数を数えるため。"""
    return sum(1 for item in inputs if item.get("type") == "image")


def entry_kind(path: Path) -> str:
    """家の中の 1 つの入り口が link か実物か無いかを名乗るため。"""
    if path.is_symlink():
        return "link"
    if path.is_dir():
        return "dir"
    return "file" if path.exists() else "none"


def home_text() -> str:
    """この process の CODEX_HOME の形(dir と auth.json の権限・口座の id・config.toml と sessions の入り口)を答えの文にするため。"""
    home = Path(os.environ.get("CODEX_HOME", ""))
    auth = home / "auth.json"
    account = json.loads(auth.read_text(encoding="utf-8"))["tokens"]["account_id"] if auth.exists() else None
    auth_mode = oct(auth.stat().st_mode & 0o777) if auth.exists() else "none"
    return (f"HOME dir={oct(home.stat().st_mode & 0o777)} auth={auth_mode} account={account} "
            f"config={entry_kind(home / 'config.toml')} sessions={entry_kind(home / 'sessions')}")


def codex_home() -> Path:
    """この process の CODEX_HOME。"""
    return Path(os.environ.get("CODEX_HOME", ""))


def state_db_path() -> Path:
    """状態の DB の path(置き場 = $CODEX_SQLITE_HOME、無ければ $CODEX_HOME — 本物の codex の決まり)。"""
    return Path(os.environ.get("CODEX_SQLITE_HOME") or os.environ.get("CODEX_HOME", "")) / STATE_DB


def open_state_db() -> int:
    """起動の時に状態の DB を開くため(無ければ sessions を全部読んで作る — 頭の註)。答え = この起動が読んだ rollout の bytes。"""
    db = state_db_path()
    if db.exists():
        return 0
    making = db.with_name(STATE_DB + ".making")
    try:
        marker = os.open(making, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        sys.stderr.write(f"{STATE_INIT_FAILED} under {db.parent}\n")
        sys.stderr.flush()
        sys.exit(1)
    sessions = codex_home() / "sessions"
    rollouts = sorted(sessions.rglob("rollout-*.jsonl")) if sessions.is_dir() else []
    read = sum(len(path.read_bytes()) for path in rollouts)
    BACKFILL_PACE.wait(float(os.environ.get("STUB_BACKFILL_SECONDS", "0")))
    building = db.with_name(STATE_DB + ".building")
    connection = sqlite3.connect(building)
    connection.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL)")
    connection.executemany("INSERT INTO threads VALUES (?, ?)",
                           [(path.stem[ROLLOUT_HEAD_CHARS:], str(path)) for path in rollouts])
    connection.commit()
    connection.close()
    os.replace(building, db)
    os.close(marker)
    os.unlink(making)
    return read


def state_connection() -> sqlite3.Connection:
    """状態の DB へつなぐため(ほかの起動の書き込みとは待ち合う)。"""
    return sqlite3.connect(state_db_path(), timeout=30)


def write_rollout(thread_id: str) -> None:
    """新しい thread の rollout を sessions に書き、DB の行に家を通る path を書くため(本物の codex の形)。"""
    path = codex_home() / "sessions" / "2026" / "10" / "19" / f"rollout-2026-10-19T00-00-00-{thread_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "session_meta", "payload": {"id": thread_id}}) + "\n", encoding="utf-8")
    with state_connection() as connection:
        connection.execute("INSERT OR REPLACE INTO threads VALUES (?, ?)", (thread_id, str(path)))


def resume_refusal(thread_id: str) -> str | None:
    """thread/resume を本物の codex と同じく断る訳を引くため(DB の行の path が無い時だけ — 頭の註)。行が無ければ sessions の file を
    名で探して行を作る。答え = 断りの文か None。"""
    with state_connection() as connection:
        row = connection.execute("SELECT rollout_path FROM threads WHERE id = ?", (thread_id,)).fetchone()
        if row is not None:
            return None if Path(row[0]).exists() else f"{NO_ROLLOUT} {thread_id}"
        sessions = codex_home() / "sessions"
        found = next(iter(sessions.rglob(f"rollout-*-{thread_id}.jsonl")), None) if sessions.is_dir() else None
        if found is not None:
            connection.execute("INSERT INTO threads VALUES (?, ?)", (thread_id, str(found.resolve())))
    return None


def state_text(backfilled: int) -> str:
    """この起動が読んだ bytes と DB の行の数を答えの文にするため。"""
    with state_connection() as connection:
        rows = connection.execute("SELECT count(*) FROM threads").fetchone()[0]
    return f"STATE backfilled={backfilled} rows={rows}"


def recorded_messages(name: str) -> list[dict[str, object]]:
    """録った 1 本の stdout の行を JSON の object の列で読むため。"""
    return [json.loads(line) for line in (RECORDED / f"{name}.stdout.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


def answer_of(messages: list[dict[str, object]], request_id: int) -> dict[str, object]:
    """録った行から、その id の要求への答えを引くため。"""
    return next(message for message in messages if message.get("id") == request_id and "result" in message)


class Stub:
    """替え玉の 1 つの process の状態(thread とターンの id・止めの合図)。要求をまたいで書き換える欄は名の頭に mut_ を付ける(DOEFF002)。"""

    def __init__(self, backfilled: int) -> None:
        self.backfilled = backfilled
        self.two_turns = recorded_messages("two-turns")
        self.interrupt = recorded_messages("interrupt")
        thread_answer = answer_of(self.two_turns, 2)
        turn_answer = answer_of(self.two_turns, 3)
        self.recorded_thread = str(thread_answer["result"]["thread"]["id"])  # type: ignore[index]  # 録った答えの形は検が確かめている
        self.recorded_turn = str(turn_answer["result"]["turn"]["id"])  # type: ignore[index]  # 同上
        self.history_bytes = resume_history_bytes(sys.argv)
        self.mut_made = 0
        self.mut_thread_id = ""
        self.write_lock = threading.Lock()
        self.stop_slow = threading.Event()
        self.mut_slow_turn = ""
        self.mut_slow_thread: threading.Thread | None = None
        # 走っている SLOW のターンの turn/steer の断り方(STEER_REFUSALS の値 — 無ければ受ける)。
        self.mut_steer_refusal: tuple[bool, str] | None = None
        self.mut_opened_with: dict[str, object] = {}
        # 2 つのターンにまたがる録りの、録ったターンの id → この process が振ったターンの id。
        self.mut_spanned: dict[str, str] = {}

    def write(self, message: dict[str, object]) -> None:
        """1 行の message を stdout へ書くため(止めの thread と主の loop の両方から書くので lock の中で)。"""
        with self.write_lock:
            sys.stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()

    def write_with_history(self, message: dict[str, object]) -> None:
        """thread を開いた答え 1 行の thread の turns に、history_bytes ほどの履歴を載せて書くため(1 行のまま、ターン 1 つずつ書く —
        替え玉が行全体を 1 度に持たない)。"""
        result = message["result"]
        thread = {**result["thread"], "turns": HISTORY_MARK}  # type: ignore[index, dict-item]  # 録った答えの形は検が確かめている
        text = json.dumps({**message, "result": {**result, "thread": thread}}, ensure_ascii=False,  # type: ignore[dict-item]  # 同上
                          separators=(",", ":"))
        before, after = text.split(json.dumps(HISTORY_MARK), 1)
        filler = "x" * HISTORY_PIECE_CHARS
        with self.write_lock:
            sys.stdout.write(before + "[")
            for number in range(max(1, -(-self.history_bytes // HISTORY_PIECE_CHARS))):
                turn = {"id": f"history-turn-{number}", "status": "completed",
                        "items": [{"type": "agentMessage", "id": f"history-msg-{number}", "text": filler}]}
                sys.stdout.write(("," if number else "") + json.dumps(turn, separators=(",", ":")))
            sys.stdout.write("]" + after + "\n")
            sys.stdout.flush()

    def renamed(self, message: dict[str, object], turn_id: str) -> dict[str, object]:
        """録った行の thread とターンの id を、この process の id へ差し替えるため。"""
        text = json.dumps(message, ensure_ascii=False)
        return json.loads(text.replace(self.recorded_thread, self.mut_thread_id).replace(self.recorded_turn, turn_id))

    def fresh_id(self, kind: str) -> str:
        """thread とターンの id を振るため。"""
        self.mut_made += 1
        return f"stub{os.environ.get('STUB_ID_PREFIX', '')}-{kind}-{self.mut_made}"

    def spanning_turn(self, name: str, request_id: int, turn_id: str) -> list[dict[str, object]]:
        """2 つのターンにまたがる録り name のうち、要求 request_id で始めたターンの通知(その答えの後から最初の turn/completed まで)を
        取るため。thread の id と、この録りでこれまでに流したターン(前のターンを名乗る遅れた行を含む)の id を、この process の id へ
        差し替える。"""
        messages = recorded_messages(name)
        thread = str(answer_of(messages, 2)["result"]["thread"]["id"])  # type: ignore[index]  # 録った答えの形は検が確かめている
        recorded_turn = str(answer_of(messages, request_id)["result"]["turn"]["id"])  # type: ignore[index]  # 同上
        self.mut_spanned[recorded_turn] = turn_id
        start = messages.index(answer_of(messages, request_id)) + 1
        end = next(index for index, message in enumerate(messages) if index >= start and message.get("method") == "turn/completed")
        text = json.dumps(messages[start:end + 1], ensure_ascii=False).replace(thread, self.mut_thread_id)
        for recorded, mine in self.mut_spanned.items():
            text = text.replace(recorded, mine)
        return json.loads(text)

    def replayed_turn(self, name: str, turn_id: str) -> list[dict[str, object]]:
        """録った筋書き name の 1 つ目のターンの通知(turn/start の答えの後から、そのターンの turn/completed まで — 子の agent の
        ターンの終わりは数えない)を、その録りの thread とターンの id をこの process の id へ差し替えて取るため。"""
        messages = recorded_messages(name)
        thread = str(answer_of(messages, 2)["result"]["thread"]["id"])  # type: ignore[index]  # 録った答えの形は検が確かめている
        turn = str(answer_of(messages, 3)["result"]["turn"]["id"])  # type: ignore[index]  # 同上
        start = messages.index(answer_of(messages, 3)) + 1
        end = next(index for index, message in enumerate(messages)
                   if index >= start and message.get("method") == "turn/completed"
                   and message["params"]["turn"]["id"] == turn)  # type: ignore[index]  # 同上
        text = json.dumps(messages[start:end + 1], ensure_ascii=False)
        return json.loads(text.replace(thread, self.mut_thread_id).replace(turn, turn_id))

    def preface_of(self, name: str) -> list[dict[str, object]]:
        """録った筋書き name の、thread を開いた答え(id 2)と 1 つ目のターンの turn/start の答え(id 3)の間の警告(warning の通知)を、
        その録りの thread の id をこの process の id へ差し替えて取るため(codex はターンを始める時の警告を答えより先に出す)。"""
        messages = recorded_messages(name)
        thread = str(answer_of(messages, 2)["result"]["thread"]["id"])  # type: ignore[index]  # 録った答えの形は検が確かめている
        between = messages[messages.index(answer_of(messages, 2)) + 1:messages.index(answer_of(messages, 3))]
        text = json.dumps([message for message in between if message.get("method") == "warning"], ensure_ascii=False)
        return json.loads(text.replace(thread, self.mut_thread_id))

    def first_turn_notifications(self) -> list[dict[str, object]]:
        """録った 1 つ目のターンの通知(turn/start の答えの後から最初の turn/completed まで)を取るため。"""
        start = self.two_turns.index(answer_of(self.two_turns, 3)) + 1
        end = next(index for index, message in enumerate(self.two_turns)
                   if index >= start and message.get("method") == "turn/completed")
        return self.two_turns[start:end + 1]

    def handle(self, message: dict[str, object]) -> None:
        """要求 1 つに答えるため。"""
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}
        if method == "initialize":
            self.write({"id": request_id, "result": answer_of(self.two_turns, 1)["result"]})
        elif method == "thread/start":
            self.mut_thread_id = self.fresh_id("thread")
            self.mut_opened_with = dict(params)  # type: ignore[arg-type]  # 要求の形は handler の rpc.hy が作る
            write_rollout(self.mut_thread_id)
            self.write({"id": request_id, "result": self.renamed(answer_of(self.two_turns, 2), "")["result"]})
            started = next(m for m in self.two_turns if m.get("method") == "thread/started")
            self.write(self.renamed(started, ""))
        elif method == "thread/resume":
            refusal = resume_refusal(str(params["threadId"]))  # type: ignore[index]  # 同上
            if refusal is not None:
                self.write({"id": request_id, "error": {"code": NO_ROLLOUT_CODE, "message": refusal}})
                return
            self.mut_thread_id = str(params["threadId"])  # type: ignore[index]  # 同上
            self.mut_opened_with = dict(params)  # type: ignore[arg-type]  # 同上
            answer = {"id": request_id, "result": self.renamed(answer_of(self.two_turns, 2), "")["result"]}
            if carries_history(sys.argv, params):  # type: ignore[arg-type]  # 要求の形は handler の rpc.hy が作る
                self.write_with_history(answer)
            else:
                self.write(answer)
        elif method == "turn/start":
            self.start_turn(request_id, params)  # type: ignore[arg-type]  # 同上
        elif method == "turn/steer":
            self.steer_turn(request_id, params)  # type: ignore[arg-type]  # 同上
        elif method == "turn/interrupt":
            self.interrupt_turn(request_id)

    def answer_turn(self, turn_id: str, text: str) -> None:
        """答えの全文 text を 1 つの途中の文字と全文で出して終えるターンの通知を流すため(録った形の最小 — 筋書きの答え)。"""
        where = {"threadId": self.mut_thread_id, "turnId": turn_id}
        self.write({"method": "turn/started", "params": {"threadId": self.mut_thread_id, "turn": {"id": turn_id, "status": "inProgress"}}})
        self.write({"method": "item/started", "params": {**where, "item": {"type": "agentMessage", "id": "msg_1", "text": ""}}})
        self.write({"method": "item/agentMessage/delta", "params": {**where, "itemId": "msg_1", "delta": text}})
        self.write({"method": "item/completed", "params": {**where, "item": {"type": "agentMessage", "id": "msg_1", "text": text}}})
        self.write({"method": "turn/completed",
                    "params": {"threadId": self.mut_thread_id, "turn": {"id": turn_id, "status": "completed", "error": None}}})

    def settings_text(self, params: dict[str, object]) -> str:
        """thread を開いた要求と turn/start が名乗った宣言(effort・圧縮の閾値・model)と、argv の切る機能を答えの文にするため。"""
        config = self.mut_opened_with.get("config") or {}
        compact = config.get("model_auto_compact_token_limit") if isinstance(config, dict) else None
        disabled = ",".join(disabled_features(sys.argv))
        return f"SETTINGS effort={params.get('effort')} compact={compact} model={self.mut_opened_with.get('model')} disabled={disabled}"

    def start_turn(self, request_id: object, params: dict[str, object]) -> None:
        """ターンを始め、入力の文字で決めた筋書き(頭の註)を流すため。"""
        turn_id = self.fresh_id("turn")
        inputs = list(params.get("input", []))  # type: ignore[call-overload]  # 要求の形は handler の rpc.hy が作る
        said = said_text(inputs)
        prefaced = next((name for marker, name in PREFACED.items() if marker in said), None)
        for warning in self.preface_of(prefaced) if prefaced is not None else []:
            self.write(warning)
        self.write({"id": request_id, "result": self.renamed(answer_of(self.two_turns, 3), turn_id)["result"]})
        if IMAGES_PHRASE in said:
            self.answer_turn(turn_id, f"IMAGES {image_count(inputs)}")
            return
        if SETTINGS_PHRASE in said:
            self.answer_turn(turn_id, self.settings_text(params))
            return
        if HOME_PHRASE in said:
            self.answer_turn(turn_id, home_text())
            return
        if STATE_PHRASE in said:
            self.answer_turn(turn_id, state_text(self.backfilled))
            return
        spanning = next((recorded for marker, recorded in SPANNING.items() if marker in said), None)
        if spanning is not None:
            for notification in self.spanning_turn(spanning[0], spanning[1], turn_id):
                self.write(notification)
            return
        replayed = next((name for marker, name in REPLAYED.items() if marker in said), None)
        if replayed is not None:
            for notification in self.replayed_turn(replayed, turn_id):
                self.write(notification)
            return
        refusal = next((spec for marker, spec in STEER_REFUSALS.items() if marker in said), None)
        if "SLOW" not in said and refusal is None:
            for notification in self.first_turn_notifications():
                self.write(self.renamed(notification, turn_id))
            return
        self.mut_slow_turn = turn_id
        self.mut_steer_refusal = refusal
        self.stop_slow.clear()
        self.mut_slow_thread = threading.Thread(target=self.emit_slow, args=(turn_id,), daemon=True)
        self.mut_slow_thread.start()

    def emit_slow(self, turn_id: str) -> None:
        """止めが来るまで、答えの文字の途中を間を置いて出し続けるため(止めの thread の入口)。"""
        self.write({"method": "turn/started", "params": {"threadId": self.mut_thread_id, "turn": {"id": turn_id, "status": "inProgress"}}})
        self.write({"method": "item/started", "params": {"threadId": self.mut_thread_id, "turnId": turn_id,
                                                          "item": {"type": "agentMessage", "id": "msg_1", "text": ""}}})
        for index in range(MAX_SLOW_PIECES):
            if self.stop_slow.wait(DELTA_PAUSE_SECONDS):
                return
            self.write({"method": "item/agentMessage/delta",
                        "params": {"threadId": self.mut_thread_id, "turnId": turn_id, "itemId": "msg_1", "delta": f"slow-{index} "}})

    def steer_turn(self, request_id: object, params: dict[str, object]) -> None:
        """走っている SLOW のターンに足した文字を、頭に steered: を付けた答えの文字の途中として出すため(違うターンなら断る)。"""
        running = self.mut_slow_thread is not None and self.mut_slow_thread.is_alive()
        if not running or params.get("expectedTurnId") != self.mut_slow_turn:
            self.write({"id": request_id, "error": {"code": NOT_STEERABLE_CODE, "message": "no active turn to steer"}})
            return
        if self.mut_steer_refusal is not None:
            self.refuse_steer(request_id, *self.mut_steer_refusal)
            return
        self.write({"id": request_id, "result": {"turnId": self.mut_slow_turn}})
        said = said_text(list(params.get("input", [])))  # type: ignore[call-overload]  # 要求の形は handler の rpc.hy が作る
        self.write({"method": "item/agentMessage/delta",
                    "params": {"threadId": self.mut_thread_id, "turnId": self.mut_slow_turn, "itemId": "msg_1",
                               "delta": f"{STEERED_PREFIX}{said} "}})

    def refuse_steer(self, request_id: object, ends_first: bool, message: str) -> None:
        """走っているターンへの turn/steer を、目印の断り方で断るため(ends_first なら、断りより先にターンを completed で終えて終わりの
        通知を書く — 先に終えない断りのターンは止めが来るまで走り続ける。実物は終わりの通知が少し後に届く)。"""
        if ends_first:
            self.stop_slow.set()
            if self.mut_slow_thread is not None:
                self.mut_slow_thread.join(5)
            self.write({"method": "turn/completed", "params": {"threadId": self.mut_thread_id,
                                                               "turn": {"id": self.mut_slow_turn, "status": "completed", "error": None}}})
        self.write({"id": request_id, "error": {"code": NOT_STEERABLE_CODE, "message": message.format(expected=self.mut_slow_turn)}})

    def interrupt_turn(self, request_id: object) -> None:
        """遅いターンを止め、録った止めの終わり(状態 interrupted)を出すため。"""
        self.stop_slow.set()
        if self.mut_slow_thread is not None:
            self.mut_slow_thread.join(5)
        self.write({"id": request_id, "result": {}})
        recorded = next(m for m in self.interrupt if m.get("method") == "turn/completed")
        params = recorded["params"]
        turn = params["turn"]  # type: ignore[index]  # 録った行の形は検 test_lines.hy が確かめている
        self.write({**recorded, "params": {**params, "threadId": self.mut_thread_id,  # type: ignore[dict-item]  # 同上
                                           "turn": {**turn, "id": self.mut_slow_turn, "rootTurnId": self.mut_slow_turn}}})


def main() -> None:
    """stdin の要求を 1 行ずつ読んで答えるため(stdin の EOF で終わる)。"""
    stub = Stub(open_state_db())
    for line in sys.stdin:
        if line.strip():
            stub.handle(json.loads(line))


if __name__ == "__main__":
    main()
