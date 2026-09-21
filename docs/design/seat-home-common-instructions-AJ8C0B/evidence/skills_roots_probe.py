"""skills がどの根から席の context に載るかを実射で測る(計画段の証拠)。

計り方は evidence/probe_user_layer.sh と同じ: ANTHROPIC_BASE_URL を手元の捕捉 server に向け、
本体が組んだ request body に目印の skill の説明文が載った数を数える。札は dummy で、API へは出ない。

使い方: python3 skills_roots_probe.py <作業 dir> <本体の実行体>
"""
from __future__ import annotations

import http.server
import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path

MARK_HOMEDOT = b"SKILLROOT_MARK_HOMEDOT_A1"
MARK_CONFIG = b"SKILLROOT_MARK_CFG_B2"
MARK_PROJECT = b"SKILLROOT_MARK_PROJECT_P3"


def put_skill(root: Path, name: str, mark: bytes) -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_bytes(
        b"---\nname: " + name.encode() + b"\ndescription: " + mark + b" probe only\n---\n\nbody\n")


def serve(cap: Path) -> http.server.HTTPServer:
    class Capture(http.server.BaseHTTPRequestHandler):
        def _capture(self) -> None:
            n = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(n) if n else b""
            with open(cap, "ab") as fh:
                fh.write(b"=== " + self.path.encode() + b" ===\n" + body + b"\n")
            reply = json.dumps({"type": "error",
                                "error": {"type": "invalid_request_error", "message": "probe"}}).encode()
            self.send_response(400)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

        def do_POST(self) -> None:
            self._capture()

        def do_GET(self) -> None:
            self._capture()

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Capture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def probe(root: Path, body: str, tag: str, *, home: Path, cfg: Path, cwd: Path) -> None:
    cap = root / f"cap_{tag}.txt"
    cap.write_bytes(b"")
    server = serve(cap)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "CLAUDE_CONFIG_DIR": str(cfg),
           "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_address[1]}",
           "ANTHROPIC_API_KEY": "probe-dummy-not-a-credential", "CLAUDE_CODE_MAX_RETRIES": "0"}
    try:
        rc: object = subprocess.run([body, "-p", "say hi", "--max-turns", "1"], cwd=cwd, env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    timeout=90, check=False).returncode
    except subprocess.TimeoutExpired:
        rc = "timeout"
    server.shutdown()
    text = cap.read_bytes()
    print(f"--- {tag}: rc={rc} requests={text.count(b'=== /v1/messages')} "
          f"homedot(A)={text.count(MARK_HOMEDOT)} cfg(B)={text.count(MARK_CONFIG)} "
          f"project(P)={text.count(MARK_PROJECT)}")
    print(f"      HOME={home}  CLAUDE_CONFIG_DIR={cfg}  cwd={cwd}")


def main(root: Path, body: str) -> None:
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    # 1. $HOME/.claude/skills に目印 A・家は空・作業ディレクトリは $HOME の下
    home = root / "h1"
    put_skill(home / ".claude/skills", "probe-skill", MARK_HOMEDOT)
    (home / "work/repo").mkdir(parents=True)
    empty = root / "cfg-empty"
    empty.mkdir()
    probe(root, body, "home-dot-skills__cwd-under-home", home=home, cfg=empty, cwd=home / "work/repo")
    # 2. 同じだが作業ディレクトリは $HOME の外
    outside = root / "outside/work"
    outside.mkdir(parents=True)
    probe(root, body, "home-dot-skills__cwd-outside-home", home=home, cfg=empty, cwd=outside)
    # 3. 対照: 家(CLAUDE_CONFIG_DIR)/skills に目印 B
    cfg = root / "cfg-b"
    put_skill(cfg / "skills", "probe-skill", MARK_CONFIG)
    probe(root, body, "config-dir-skills__control", home=home, cfg=cfg, cwd=home / "work/repo")
    # 4. 衝突: 家に B、作業ディレクトリの project 層(cwd/.claude/skills)に同じ名前で P
    repo4 = home / "work/repo4"
    put_skill(repo4 / ".claude/skills", "probe-skill", MARK_PROJECT)
    probe(root, body, "collision__config-B_vs_project-P", home=home, cfg=cfg, cwd=repo4)


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2])
