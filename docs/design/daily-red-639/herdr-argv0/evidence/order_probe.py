"""前面の job が複数の process を持つ時、herdr の pane.process_info がどの順で並べるかを実測する(作った workspace は最後に閉じる)。"""
import json, os, socket, sys, time
SOCK = os.path.expanduser("~/.config/herdr/herdr.sock")
def call(method, params):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.connect(SOCK)
    s.sendall((json.dumps({"id": "probe", "method": method, "params": params}) + "\n").encode())
    buf = b""
    while b"\n" not in buf:
        d = s.recv(65536)
        if not d: break
        buf += d
    s.close()
    r = json.loads(buf)
    if "error" in r: raise RuntimeError(r["error"])
    return r["result"]
label = f"doeff-design-order-probe-{os.getpid()}"
ws = call("workspace.create", {"label": label, "cwd": "/tmp", "focus": False})
wsid = ws["workspace"]["workspace_id"]; pane = ws["root_pane"]["pane_id"]
try:
    time.sleep(1.5)
    for cmd in ["sleep 30 | cat", "bash -c 'sleep 30; true'"]:
        call("pane.send_text", {"pane_id": pane, "text": cmd})
        call("pane.send_keys", {"pane_id": pane, "keys": ["Enter"]})
        time.sleep(1.5)
        info = call("pane.process_info", {"pane_id": pane})["process_info"]
        procs = [(p["pid"], p["name"], p.get("argv0"), (p.get("argv") or [None])[0]) for p in info.get("foreground_processes", [])]
        print(f"観測: {sys.platform} command={cmd!r} pgid={info.get('foreground_process_group_id')} 並び(pid, name, argv0, argv[0])={procs}")
        call("pane.send_keys", {"pane_id": pane, "keys": ["ctrl+c"]})
        time.sleep(1.0)
finally:
    call("workspace.close", {"workspace_id": wsid})
    print(f"後片付け: workspace {wsid}({label})を閉じた")
