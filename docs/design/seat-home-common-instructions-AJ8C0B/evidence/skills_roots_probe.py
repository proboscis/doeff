"""skills がどの根から席の context に載るかを実射で測る(計画段の証拠)。

計り方は evidence/probe_user_layer.sh と同じ: ANTHROPIC_BASE_URL を手元の捕捉 server に向け、
本体が組んだ request body に目印の skill が載った数を数える。札は dummy で、API へは出ない。

測る問いは 3 つ:
  - project 層の走査(作業ディレクトリから上へ)はどこで止まるか(git の root・ホームディレクトリ)
  - 実席と同じ起動形で、家(CLAUDE_CONFIG_DIR)が空なら dotfiles の skills は載らないか
  - 運んだ形(家の skills を dotfiles の skills への dir symlink にする)なら載るか

使い方: python3 skills_roots_probe.py <作業 dir> <本体の実行体> <HOME の下の git でない作業 dir(自分で作る)>
"""
from __future__ import annotations

import http.server
import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path

MARK_ANCESTOR = b"SKILLROOT_MARK_ANCESTOR_W1"
MARK_FAKEHOME = b"SKILLROOT_MARK_FAKEHOME_W2"
MARK_CONFIG = b"SKILLROOT_MARK_CFG_B"
MARK_PROJECT = b"SKILLROOT_MARK_PROJECT_P"
# dotfiles の skill が一覧に載った目印。100 件を超えると一覧の文字数の予算で説明文が落ちて名前だけの行に
# なるので、名前の行で数える(request body は JSON なので改行は 2 文字の \\n)。CLAUDE.md の本文にも名前は
# 出るが、行頭の "- " は付かない。
MARK_DOTFILES = b"\\n- herdr-comms"

# 実席の起動形(agentd が撃つ引数から --resume と会話固有の値を除いたもの)
LIVE_FLAGS = ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
              "--include-partial-messages", "--dangerously-skip-permissions", "--effort", "xhigh",
              "--model", "claude-opus-5", "--autocompact", "400000"]


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


def probe(root: Path, body: str, tag: str, *, home: Path, cfg: Path, cwd: Path,
          live: bool = False, oauth: bool = False) -> None:
    cap = root / ("cap_" + "".join(c if c.isalnum() else "_" for c in tag) + ".txt")
    cap.write_bytes(b"")
    server = serve(cap)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "CLAUDE_CONFIG_DIR": str(cfg),
           "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_address[1]}",
           "CLAUDE_CODE_MAX_RETRIES": "0"}
    env["CLAUDE_CODE_OAUTH_TOKEN" if oauth else "ANTHROPIC_API_KEY"] = "probe-dummy-not-a-credential"
    if live:
        env["DISABLE_AUTO_UPDATE"] = "true"
        env["DISABLE_UPDATE_PROMPT"] = "true"
        argv = [body, *LIVE_FLAGS]
        stdin = json.dumps({"type": "user",
                            "message": {"role": "user", "content": "say hi"}}).encode() + b"\n"
    else:
        argv = [body, "-p", "say hi", "--max-turns", "1"]
        stdin = b""
    try:
        rc: object = subprocess.run(argv, cwd=cwd, env=env, input=stdin,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    timeout=90, check=False).returncode
    except subprocess.TimeoutExpired:
        rc = "timeout"
    server.shutdown()
    text = cap.read_bytes()
    print(f"--- {tag}: rc={rc} requests={text.count(b'=== /v1/messages')} "
          f"ancestor(W1)={text.count(MARK_ANCESTOR)} fakehome(W2)={text.count(MARK_FAKEHOME)} "
          f"cfg(B)={text.count(MARK_CONFIG)} project(P)={text.count(MARK_PROJECT)} "
          f"dotfiles={text.count(MARK_DOTFILES)}")
    print(f"      HOME={home}  CLAUDE_CONFIG_DIR={cfg}  cwd={cwd}  live={live} oauth={oauth}")


def main(root: Path, body: str, scratch: Path, real_home: Path) -> None:
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    empty = root / "cfg-empty"
    empty.mkdir()
    fake_home = root / "h"
    fake_home.mkdir()
    doeff = real_home / "repos/doeff"

    # A. project 層の走査の止まり方
    # 1. 祖先の dir(ホームでない)に目印・作業ディレクトリは git でない — 上へたどって読むはず
    put_skill(root / "anc/.claude/skills", "probe-anc", MARK_ANCESTOR)
    (root / "anc/a/b").mkdir(parents=True)
    probe(root, body, "1 ancestor, non-git cwd", home=fake_home, cfg=empty, cwd=root / "anc/a/b")
    # 2. 同じ形だが作業ディレクトリが git の root — その上の祖先は読まないはず
    put_skill(root / "anc2/.claude/skills", "probe-anc", MARK_ANCESTOR)
    (root / "anc2/repo").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root / "anc2/repo")], check=True)
    probe(root, body, "2 ancestor above git root", home=fake_home, cfg=empty, cwd=root / "anc2/repo")
    # 3. 本物のホームの .claude/skills(dotfiles を指す)— 家は空・作業ディレクトリはホームの下
    scratch.mkdir(parents=True, exist_ok=True)
    probe(root, body, "3 real HOME, ~/.claude/skills, empty cfg", home=real_home, cfg=empty,
          cwd=scratch)
    # 4. 本物のホームより下の祖先に目印 — ホームの手前までは読むはず
    put_skill(scratch.parent / ".claude/skills", "probe-anc", MARK_ANCESTOR)
    probe(root, body, "4 real HOME, ancestor below HOME", home=real_home, cfg=empty, cwd=scratch)
    # 5. env の HOME を偽の dir に付け替え、その下から — 本体は env の HOME をホームとして扱うか
    put_skill(fake_home / ".claude/skills", "probe-fh", MARK_FAKEHOME)
    (fake_home / "work").mkdir()
    probe(root, body, "5 fake env HOME, cwd under it", home=fake_home, cfg=empty,
          cwd=fake_home / "work")

    # B. 実席の起動形・作業ディレクトリ = ~/repos/doeff(git の root)
    # 6. 家は空 — 今日の席
    probe(root, body, "6 live form, empty cfg", home=real_home, cfg=empty, cwd=doeff, live=True)
    probe(root, body, "6o live form + oauth dummy, empty cfg", home=real_home, cfg=empty, cwd=doeff,
          live=True, oauth=True)
    # 7. Phase 3 の形: 家/skills -> dotfiles の skills への dir symlink
    carried = root / "cfg-carried"
    carried.mkdir()
    (carried / "skills").symlink_to(real_home / "dotfiles/agent/skills")
    probe(root, body, "7 live form, cfg/skills symlink", home=real_home, cfg=carried, cwd=doeff,
          live=True)
    probe(root, body, "7o live form + oauth dummy, cfg/skills symlink", home=real_home, cfg=carried,
          cwd=doeff, live=True, oauth=True)
    # 8. 名前の衝突: 家に目印 B、git の root の project 層に同じ名前で目印 P
    cfg_b = root / "cfg-b"
    put_skill(cfg_b / "skills", "probe-skill", MARK_CONFIG)
    repo = root / "repo-collide"
    put_skill(repo / ".claude/skills", "probe-skill", MARK_PROJECT)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    probe(root, body, "8 live form, collision cfg-B vs project-P", home=real_home, cfg=cfg_b,
          cwd=repo, live=True)
    # 9. 8 の対照: 家は空・同じ repo — project 層の P だけなら載るはず(8 の P=0 が「読まれない」でないことの確かめ)
    probe(root, body, "9 live form, project-P alone", home=real_home, cfg=empty, cwd=repo, live=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), Path.home())
