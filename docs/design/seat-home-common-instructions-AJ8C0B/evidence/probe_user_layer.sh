#!/bin/bash
# 据わっている Claude Code 本体から「user 層の CLAUDE.md と skills をどこから読むか」を実射で測る。
#
# 計り方: ANTHROPIC_BASE_URL を手元の捕捉 server に向け、本体が実際に組んだ
# /v1/messages の request body を読む。body に入っている = 席の context に載っている。
# 札は使わない(dummy 値・本物の資格は 1 本も渡さない)。API へは 1 byte も出ない。
#
# 使い方: probe_user_layer.sh <作業 dir>
set -u
R="${1:?usage: probe_user_layer.sh <workdir>}"
rm -rf "$R"; mkdir -p "$R"
BIN="$(command -v claude)"
echo "### claude binary: $BIN"
echo "### claude --version: $(claude --version 2>&1)"
echo "### uname: $(uname -srm)"
echo "### date: $(date -u +%FT%TZ)"

cat > "$R/capture.py" <<'PY'
import http.server, json, sys
OUT = sys.argv[2]
class H(http.server.BaseHTTPRequestHandler):
    def _cap(self):
        n = int(self.headers.get('content-length') or 0)
        body = self.rfile.read(n) if n else b''
        with open(OUT, 'ab') as f:
            f.write(b'=== ' + self.path.encode() + b' ===\n' + body + b'\n')
        self.send_response(400)
        self.send_header('content-type','application/json')
        b = json.dumps({"type":"error","error":{"type":"invalid_request_error","message":"probe capture"}}).encode()
        self.send_header('content-length',str(len(b))); self.end_headers(); self.wfile.write(b)
    do_POST = _cap
    do_GET = _cap
    def log_message(self, *a): pass
http.server.HTTPServer(('127.0.0.1', int(sys.argv[1])), H).serve_forever()
PY

MEM_MARK=MARKER_USER_MEMORY_9F3A
SKILL_MARK=PROBE_SKILL_MARKER_7C1B
mkdir -p "$R/canon/skills/probe-skill"
printf -- '# PROBE-CANON-MEMORY\n\n%s\n' "$MEM_MARK" > "$R/canon/CLAUDE.md"
printf -- '---\nname: probe-skill\ndescription: %s probe only\n---\n\nbody\n' "$SKILL_MARK" > "$R/canon/skills/probe-skill/SKILL.md"

port=8900
probe() { # probe <tag> <home> <cwd> <fakehome> [env K=V ...] -- [claude args...]
  local tag=$1 home=$2 cwd=$3 fh=$4; shift 4
  local envs=() args=()
  while [ $# -gt 0 ]; do [ "$1" = "--" ] && { shift; break; }; envs+=("$1"); shift; done
  args=("$@")
  port=$((port+1))
  local cap="$R/cap_$tag.txt"; : > "$cap"
  python3 "$R/capture.py" $port "$cap" >/dev/null 2>&1 & local srv=$!
  sleep 2
  ( cd "$cwd" && env -i PATH=/usr/local/bin:/usr/bin:/bin HOME="$fh" CLAUDE_CONFIG_DIR="$home" \
      ANTHROPIC_BASE_URL="http://127.0.0.1:$port" ANTHROPIC_API_KEY=probe-dummy-not-a-credential \
      CLAUDE_CODE_MAX_RETRIES=0 "${envs[@]}" timeout 75 claude -p "say hi" --max-turns 1 "${args[@]}" >/dev/null 2>&1 )
  local rc=$?
  kill -TERM $srv 2>/dev/null; wait $srv 2>/dev/null
  echo "--- $tag  rc=$rc  home=$home cwd=$cwd env=${envs[*]:-none} args=${args[*]:-none}"
  echo "    requests=$(grep -c '^=== /v1/messages' "$cap")  user_memory_occurrences=$(grep -o $MEM_MARK "$cap" | wc -l)  skill_occurrences=$(grep -o $SKILL_MARK "$cap" | wc -l)"
  grep -o 'Contents of [^ ]*CLAUDE[^ ]*\.md ([^)]*)' "$cap" | sort | uniq -c | sed 's/^/    cited: /'
}

echo
echo "## 1. 置き場の解決 — user 層は CLAUDE_CONFIG_DIR の下か"
mkdir -p "$R/homeA" "$R/homeB/skills" "$R/homeC" "$R/fakehome" "$R/cwd"
ln -s "$R/canon/CLAUDE.md" "$R/homeA/CLAUDE.md"; ln -s "$R/canon/skills" "$R/homeA/skills"   # 案 A: symlink
cp "$R/canon/CLAUDE.md" "$R/homeB/CLAUDE.md"; cp -r "$R/canon/skills/probe-skill" "$R/homeB/skills/"  # 実体
ln "$R/canon/CLAUDE.md" "$R/homeC/CLAUDE.md"; ln -s "$R/canon/skills" "$R/homeC/skills"      # hard link
probe A_symlink          "$R/homeA" "$R/cwd" "$R/fakehome" --
probe B_regular_file     "$R/homeB" "$R/cwd" "$R/fakehome" --
probe C_hardlink         "$R/homeC" "$R/cwd" "$R/fakehome" --

echo
echo "## 2. 反例 — entrypoint local-agent で symlink / hard link の user 記憶が黙って落ちる"
probe A_symlink_localagent  "$R/homeA" "$R/cwd" "$R/fakehome" CLAUDE_CODE_ENTRYPOINT=local-agent --
probe B_regular_localagent  "$R/homeB" "$R/cwd" "$R/fakehome" CLAUDE_CODE_ENTRYPOINT=local-agent --
probe C_hardlink_localagent "$R/homeC" "$R/cwd" "$R/fakehome" CLAUDE_CODE_ENTRYPOINT=local-agent --

echo
echo "## 3. 今日の倒れ方と新しい形(Mac の形: 家の下に cwd・\$HOME/.claude/CLAUDE.md が在る)"
FH="$R/fh3"; H="$FH/.local/state/doeff/agentd-homes/claude/acct"; BARE="$FH/.local/state/doeff/agentd-homes/claude/bare"
mkdir -p "$FH/.claude" "$FH/repos/doeff" "$H" "$BARE" "$R/outside/work"
cp "$R/canon/CLAUDE.md" "$FH/.claude/CLAUDE.md"
cp "$R/canon/CLAUDE.md" "$H/CLAUDE.md"; ln -s "$R/canon/skills" "$H/skills"
probe G_today_cwd_in_home  "$BARE" "$FH/repos/doeff" "$FH" --
probe H_today_cwd_outside  "$BARE" "$R/outside/work" "$FH" --
probe I_new_cwd_in_home    "$H"    "$FH/repos/doeff" "$FH" --
probe J_new_cwd_outside    "$H"    "$R/outside/work" "$FH" --
probe K_new_excluded       "$H"    "$FH/repos/doeff" "$FH" -- --settings "{\"claudeMdExcludes\":[\"$FH/CLAUDE.md\",\"$FH/.claude/CLAUDE.md\"]}"
echo
echo "### done"

# --- 追補(2026-09-21): 除外の陽性対照 ---
# claudeMdExcludes が repo 自身の CLAUDE.md(= project 層で届くべき物)を巻き添えにしないこと。
addendum() {
  local R="$1"
  local FH="$R/fh3" H="$R/fh3/.local/state/doeff/agentd-homes/claude/acct"
  printf -- '# repo of the work\n\nMARKER_PROJECT_REPO_MD_4D2E\n' > "$FH/repos/doeff/CLAUDE.md"
  echo
  echo "## 4. 除外の陽性対照 — 作業する repo 自身の CLAUDE.md は落ちない"
  probe M_excluded_keeps_repo_md "$H" "$FH/repos/doeff" "$FH" -- \
    --settings "{\"claudeMdExcludes\":[\"$FH/.claude/CLAUDE.md\"]}"
  local cap="$R/cap_M_excluded_keeps_repo_md.txt"
  echo "    repo_md_occurrences=$(grep -o MARKER_PROJECT_REPO_MD_4D2E "$cap" | wc -l)"
  rm -f "$FH/repos/doeff/CLAUDE.md"
  echo
  echo "## 5. 対照 — 有人の席(家 = \$HOME/.claude)は同じ形でも 1 件ちょうど(path が同じなので本体が畳む)"
  ln -sfn "$R/canon/skills" "$FH/.claude/skills"
  probe L_human_session "$FH/.claude" "$FH/repos/doeff" "$FH" --

  echo
  echo "## 6. pod の形 — 除外が名指す path が実在しなくても無害(\$HOME/.claude が無い宿)"
  local PFH="$R/fh4" PH="$R/fh4/.local/state/doeff/agentd-homes/claude/acct"
  mkdir -p "$PH" "$PFH/repos/doeff"
  cp "$R/canon/CLAUDE.md" "$PH/CLAUDE.md"; ln -s "$R/canon/skills" "$PH/skills"
  printf -- '# repo of the work\n\nMARKER_PROJECT_REPO_MD_4D2E\n' > "$PFH/repos/doeff/CLAUDE.md"
  probe N_pod_exclude_nonexistent "$PH" "$PFH/repos/doeff" "$PFH" -- \
    --settings "{\"claudeMdExcludes\":[\"$PFH/.claude/CLAUDE.md\"]}"
  echo "    repo_md_occurrences=$(grep -o MARKER_PROJECT_REPO_MD_4D2E "$R/cap_N_pod_exclude_nonexistent.txt" | wc -l)"
}
[ "${2:-}" = "--addendum" ] && addendum "$1"
