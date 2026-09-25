"""配備前に溜まった出来事の file を段の DB へ取り込む手順の道具(実行は operator の確認の後 — この版では撃たない)。

手順(pod ごと・配備の**後**に撃つ):

1. ``plan``   — 置き場の dir の file を session ごとに数え、行の数・bytes を JSON で出す(読むだけ)。
2. ``ship``   — file の行を OTLP で段の DB へ送る(``--apply`` が無ければ送らずに数だけ出す)。行の形は本番の送り手
   (headless_outbox.otlp_body)と同じ。seq は file の中の順(stdout の行 → stderr の行 → cache の流れの行)で
   1 から振るので、同じ file を 2 度送っても同じ鍵 (conversation, session, seq) になり、表(ReplacingMergeTree)が畳む。
3. ``verify`` — 段の DB の読み手(ClickHouse の HTTP の口)に session ごとの行数を問い、file の行数と一致した
   session の file の一覧を出す(``--out``)。**消さない** — 消すのは一覧を operator が確かめた後の別の手。

前提: 配備で host が入れ替わると、配備前の session はすべて終端になり(復帰の腕 — 器の process は host と共に
降りる)、次の手番は新しい session id で起きる。だから配備前の file の session id に、配備後の送り待ちの表の行が
重なることは無い(seq が衝突しない)。``plan`` は store に非終端の行が残っている session を ``deferred`` として
名乗り、``ship`` はそれを送らない。
"""

# pyright: strict
import argparse
import json
import os
import sqlite3
import sys
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from doeff_agents.sessionhost.headless_events import (
    CACHE_MARK,
    STDERR_SUFFIX,
    HeadlessEvent,
    Stream,
    key_of_locator,
)
from doeff_agents.sessionhost.headless_outbox import (
    NO_ATTRIBUTION,
    Attribution,
    attribution_of,
    otlp_body,
    urllib_post,
)

TERMINAL = ("done", "failed", "exited", "stopped", "cancelled")
SHIP_BATCH = 500


@dataclass(frozen=True)
class SessionFiles:
    session_id: str
    files: tuple[str, ...]
    lines: int
    bytes: int
    status: str | None
    deferred: bool


def _session_of_file(name: str) -> str | None:
    base = name[: -len(STDERR_SUFFIX)] if name.endswith(STDERR_SUFFIX) else name
    key = key_of_locator(base)
    return key.session_id if key is not None else None


def _count_lines(path: str) -> int:
    with open(path, "rb") as handle:
        return sum(1 for line in handle if line.strip())


def plan(root: str, db: str) -> list[SessionFiles]:
    by_session: dict[str, list[str]] = {}
    for name in sorted(os.listdir(root)):
        sid = _session_of_file(name)
        if sid is not None:
            by_session.setdefault(sid, []).append(os.path.join(root, name))
    statuses: dict[str, str] = {}
    if os.path.exists(db):
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            for sid, status in conn.execute("SELECT session_id, status FROM agent_sessions"):
                statuses[str(sid)] = str(status)
        finally:
            conn.close()
    out: list[SessionFiles] = []
    for sid, files in sorted(by_session.items()):
        status = statuses.get(sid)
        out.append(
            SessionFiles(
                session_id=sid,
                files=tuple(files),
                lines=sum(_count_lines(path) for path in files),
                bytes=sum(os.path.getsize(path) for path in files),
                status=status,
                deferred=status is not None and status not in TERMINAL,
            )
        )
    return out


def _order(path: str) -> str:
    """session の file を送る順の鍵: 本体の stdout → stderr → cache の流れ(op ごと・stdout → stderr)。"""
    name = os.path.basename(path)
    if CACHE_MARK in name:
        return f"2:{name}"
    return f"{1 if name.endswith(STDERR_SUFFIX) else 0}:{name}"


def events_of(item: SessionFiles, conversation_id: str, agent_job_id: str) -> list[HeadlessEvent]:
    events: list[HeadlessEvent] = []
    seq = 0
    for path in sorted(item.files, key=_order):
        name = os.path.basename(path)
        stream: Stream = "stderr" if name.endswith(STDERR_SUFFIX) else "stdout"
        base = name[: -len(STDERR_SUFFIX)] if stream == "stderr" else name
        key = key_of_locator(base)
        op = key.op if key is not None else ""
        at = _mtime_iso(path)
        with open(path, encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                line = raw.rstrip("\n")
                if not line.strip():
                    continue
                seq += 1
                events.append(
                    HeadlessEvent(
                        session_id=item.session_id,
                        seq=seq,
                        stream=stream,
                        op=op,
                        line=line,
                        at=at,
                        turn=0,
                        conversation_id=conversation_id,
                        agent_job_id=agent_job_id,
                    )
                )
    return events


def _mtime_iso(path: str) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(os.path.getmtime(path), UTC).isoformat()


def _attributions(db: str) -> dict[str, Attribution]:
    if not os.path.exists(db):
        return {}
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return {
            str(sid): attribution_of(raw)
            for sid, raw in conn.execute("SELECT session_id, launch_attribution_json FROM agent_sessions")
        }
    finally:
        conn.close()


def ship(root: str, db: str, url: str, node: str, apply: bool) -> dict[str, int]:
    attributions = _attributions(db)
    sent = 0
    sessions = 0
    for item in plan(root, db):
        if item.deferred:
            continue
        conversation, job = attributions.get(item.session_id, NO_ATTRIBUTION)
        events = events_of(item, conversation, job)
        sessions += 1
        for start in range(0, len(events), SHIP_BATCH):
            batch = events[start : start + SHIP_BATCH]
            if apply:
                body = json.dumps(otlp_body(batch, node, batch[-1].at), ensure_ascii=False).encode("utf-8")
                status = urllib_post(url.rstrip("/") + "/v1/logs", body)
                if not 200 <= status < 300:
                    raise RuntimeError(f"collector answered HTTP {status} for {item.session_id}")
            sent += len(batch)
    return {"sessions": sessions, "rows": sent, "applied": int(apply)}


def stored_counts(clickhouse_url: str, user: str, password: str, session_ids: Sequence[str]) -> dict[str, int]:
    """段の DB の session ごとの行数(重複は FINAL で畳んだ後)。読み手の user で問う。"""
    if not session_ids:
        return {}
    quoted = ",".join("'" + sid.replace("'", "''") + "'" for sid in session_ids)
    query = (
        "SELECT SessionId, count() FROM agentd_records.headless_events FINAL "
        f"WHERE SessionId IN ({quoted}) GROUP BY SessionId FORMAT TabSeparated"
    )
    request = urllib.request.Request(
        clickhouse_url.rstrip("/") + "/?" + urllib.parse.urlencode({"query": query}),
        headers={"X-ClickHouse-User": user, "X-ClickHouse-Key": password},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        text = response.read().decode("utf-8")
    counts: dict[str, int] = {}
    for line in text.splitlines():
        sid, _, count = line.partition("\t")
        counts[sid] = int(count)
    return counts


def verify(root: str, db: str, counts: dict[str, int]) -> dict[str, list[str]]:
    """file の行数と段の DB の行数が一致した session の file(外してよい候補)と、一致しない session。"""
    matched: list[str] = []
    mismatched: list[str] = []
    for item in plan(root, db):
        if item.deferred:
            continue
        if counts.get(item.session_id) == item.lines:
            matched.extend(item.files)
        else:
            mismatched.append(item.session_id)
    return {"verified_files": matched, "mismatched_sessions": mismatched}


def _read_secret(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read().strip()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m doeff_agents.sessionhost.headless_events_migrate")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "ship", "verify"):
        command = sub.add_parser(name)
        command.add_argument("--root", required=True, help="出来事の file の dir(DOEFF_SESSIONHOST_HEADLESS_DIR)")
        command.add_argument("--db", required=True, help="host の store(agentd.sqlite)— 読むだけ")
        if name == "ship":
            command.add_argument("--url", required=True, help="OTLP/HTTP の collector(例 http://effect-otel-collector…:4318)")
            command.add_argument("--node", required=True)
            command.add_argument("--apply", action="store_true", help="付けなければ送らずに数だけ出す")
        if name == "verify":
            command.add_argument("--clickhouse-url", required=True)
            command.add_argument("--user", required=True)
            command.add_argument("--password-file", required=True, help="読み手の password を持つ file(0600)")
            command.add_argument("--out", required=True, help="一致した file の一覧を書く JSON")
    args = parser.parse_args(argv)
    if args.command == "plan":
        items = plan(args.root, args.db)
        sys.stdout.write(json.dumps([asdict(item) for item in items], ensure_ascii=False, indent=1) + "\n")
        return 0
    if args.command == "ship":
        sys.stdout.write(json.dumps(ship(args.root, args.db, args.url, args.node, args.apply)) + "\n")
        return 0
    items = [item for item in plan(args.root, args.db) if not item.deferred]
    counts = stored_counts(
        args.clickhouse_url, args.user, _read_secret(args.password_file), [item.session_id for item in items]
    )
    result = verify(args.root, args.db, counts)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=1)
    sys.stdout.write(
        json.dumps({"verified_files": len(result["verified_files"]), "mismatched": len(result["mismatched_sessions"])})
        + "\n"
    )
    return 0 if not result["mismatched_sessions"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
