"""zeus の worker の Pod の mount の係(sidecar workspace-mount)の宣言と、係が走らせる script(workspace-volumes/workspace-mount.sh)の検。

決まりは deploy/k8s/workspace-volumes/README.md の節「mount の係」と deploy/k8s/nodes/zeus/node.yaml の註。

確かめる事(宣言 — ``kubectl kustomize`` で組み立てた物を読む。kubectl が無ければ赤):
  (1) 係は zeus の worker の Pod の中の native sidecar(restartPolicy Always)で、root・privileged・image は worker と同じ。Pod を作る物は
      増えない(coordinator と機体ごとの doeff-worker だけ・worker の container は 1 つ)。
  (2) 係の /work は Bidirectional、worker の /work は HostToContainer で、どちらも同じ volume work(USB の SSD の作業の root)。
  (3) 頼みの dir は係と worker が同じ path に置く 1 つの emptyDir で、worker は env WORKER_WORKSPACE_MOUNT_DIR でその path を知る。
  (4) 係は script を名の固定した ConfigMap workspace-mount から読み(subPath でない — 替えても Pod を作り直さない)、ConfigMap の中身は
      repo の script と同じ。係は host の /dev を見て、script の device の既定の dir はその下の longhorn。
  (5) 係は USB の SSD の印を確かめる init own-work の後に起き、/work が shared でなければ起動で止まる。
確かめる事(script — 偽の命令〔stat・findmnt・blkid・mkfs.ext4・mount・umount・fstrim〕で回す):
  (6) 空の volume は uid 1000 の根と root の予約 0 の ext4 を作ってから付け、file system の在る volume は作らずに付ける。同じ物を 2 度
      付けても 1 度だけ付ける。外す・fstrim は付いている物を名指しで照らしてからする。
失敗ケース(断って何もしない事の検): 知らない命令・欄の数・volume の名・作業の root の外・. と ..・在る dir でない・symlink・attach されて
いない・付ける先に別の物・別の所に付いた volume・ext4 でない中身と区画の表(作り直さない)・付いていない所の fstrim・別の物を外す・使用中で外れない。
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

PACKAGE = Path(__file__).resolve().parents[1]
K8S = PACKAGE / "deploy" / "k8s"
SCRIPT = K8S / "workspace-volumes" / "workspace-mount.sh"

SIDECAR = "workspace-mount"
SCRIPT_CONFIGMAP = "workspace-mount"
SCRIPT_DIR = "/etc/workspace-mount"
REQUEST_ENV = "WORKER_WORKSPACE_MOUNT_DIR"
WORK_ROOT = "/mnt/fast_ssd_usb/agent-worker"
HOST_DEV = "/host/dev"
NAMESPACE = "agent-worker"
NODES = ("atlas", "eos", "helios", "k3s-0", "k3s-1", "k3s-2", "k3s-3", "zeus")
POD_KINDS = ("Deployment", "DaemonSet", "StatefulSet", "ReplicaSet", "Pod", "Job", "CronJob")

Manifest = dict


def _build(directory: Path) -> list[Manifest]:
    """directory を kubectl kustomize で組み立てた物(Flux が当てる物と同じ)。kubectl が無ければ赤(飛ばさない)。"""
    kubectl = shutil.which("kubectl")
    assert kubectl is not None, "kubectl が無い — 宣言を組み立てて確かめられない(このテストは飛ばさない)"
    done = subprocess.run(
        [kubectl, "kustomize", str(directory)], capture_output=True, text=True, timeout=25, check=False
    )
    assert done.returncode == 0, f"kubectl kustomize {directory} が失敗した: {done.stderr.strip()}"
    return [doc for doc in yaml.safe_load_all(done.stdout) if doc is not None]


@pytest.fixture(scope="module")
def rendered() -> list[Manifest]:
    return _build(K8S)


@pytest.fixture(scope="module")
def pod(rendered: list[Manifest]) -> Manifest:
    """zeus の worker の Pod の spec。"""
    found = [doc for doc in rendered if doc["kind"] == "Deployment" and doc["metadata"]["name"] == "doeff-worker-zeus"]
    assert len(found) == 1, f"zeus の worker の Deployment がちょうど 1 つ無い: {len(found)}"
    return found[0]["spec"]["template"]["spec"]


def _named(entries: list[Manifest], name: str, what: str) -> Manifest:
    found = [entry for entry in entries if entry["name"] == name]
    assert len(found) == 1, f"{what} {name} がちょうど 1 つ無い: {len(found)}"
    return found[0]


def _sidecar(pod: Manifest) -> Manifest:
    return _named(pod["initContainers"], SIDECAR, "initContainer")


def _worker(pod: Manifest) -> Manifest:
    assert [c["name"] for c in pod["containers"]] == ["worker"], "worker の Pod の container は worker 1 つ"
    return pod["containers"][0]


def _mount(container: Manifest, path: str) -> Manifest:
    found = [mount for mount in container.get("volumeMounts", []) if mount["mountPath"] == path]
    assert len(found) == 1, f"{container['name']} の {path} の mount がちょうど 1 つ無い: {found!r}"
    return found[0]


def _volume(pod: Manifest, name: str) -> Manifest:
    return _named(pod["volumes"], name, "volume")


def test_sidecar_is_a_root_privileged_native_sidecar_with_the_worker_image(pod: Manifest) -> None:
    sidecar = _sidecar(pod)
    assert sidecar["restartPolicy"] == "Always"
    assert sidecar["securityContext"] == {"privileged": True, "runAsUser": 0, "runAsNonRoot": False}
    assert sidecar["image"] == _worker(pod)["image"]
    assert "privileged" not in (_worker(pod).get("securityContext") or {})


def test_sidecar_adds_no_pod(rendered: list[Manifest], pod: Manifest) -> None:
    """Pod を作る物は coordinator と機体ごとの doeff-worker だけで、係は zeus の worker の Pod の中に在る(container は worker 1 つのまま)。"""
    makers = sorted((doc["kind"], doc["metadata"]["name"]) for doc in rendered if doc["kind"] in POD_KINDS)
    assert makers == sorted([("Deployment", "coordinator")] + [("Deployment", f"doeff-worker-{node}") for node in NODES])
    _worker(pod)


def test_sidecar_spreads_its_mounts_to_the_worker_through_the_host(pod: Manifest) -> None:
    sidecar_work = _mount(_sidecar(pod), "/work")
    worker_work = _mount(_worker(pod), "/work")
    assert sidecar_work["name"] == worker_work["name"] == "work"
    assert sidecar_work.get("mountPropagation") == "Bidirectional"
    assert worker_work.get("mountPropagation") == "HostToContainer"
    assert _volume(pod, "work")["hostPath"] == {"path": WORK_ROOT, "type": "Directory"}


def test_sidecar_stacks_nothing_under_the_work_root(pod: Manifest) -> None:
    """係の /work の下に別の volume を重ねない(Bidirectional では重ねた mount まで host へ広がる)。"""
    stacked = [m["mountPath"] for m in _sidecar(pod)["volumeMounts"] if m["mountPath"].startswith("/work/")]
    assert stacked == []


def test_worker_and_sidecar_share_one_request_dir_named_by_the_env(pod: Manifest) -> None:
    worker = _worker(pod)
    path = _named(worker["env"], REQUEST_ENV, "env")["value"]
    assert worker["env"][-1]["name"] == REQUEST_ENV, "env は末尾に足す(機体の dir の JSON patch が読む env の場所を動かさない)"
    sidecar_mount = _mount(_sidecar(pod), path)
    worker_mount = _mount(worker, path)
    assert sidecar_mount["name"] == worker_mount["name"]
    assert _volume(pod, sidecar_mount["name"]) == {"name": sidecar_mount["name"], "emptyDir": {}}
    assert f"{path}/*.req" in _sidecar(pod)["args"][0]


def test_the_volume_handler_asks_the_request_dir_and_device_dir_the_pod_declares(pod: Manifest) -> None:
    """job の子は env WORKER_WORKSPACE_MOUNT_DIR を継がないので、作業場所の volume の本番の答え手は頼みの dir を固定の path で持つ —
    その path が Pod の宣言の頼みの dir と同じで、答え手が mountinfo で volume を見分ける device の dir の名が script の device の dir と同じ。"""
    import hy  # noqa: F401  — Hy の module を読む前に要る

    from doeff_cluster._shared.core.workspace_volume_rules import DEVICE_DIR_NAME
    from doeff_cluster._shared.protocol.workspace_volumes import WORKSPACE_SITE

    assert WORKSPACE_SITE.mount_dir == _named(_worker(pod)["env"], REQUEST_ENV, "env")["value"]
    assert f"${{WORKSPACE_MOUNT_DEVICES:-{HOST_DEV}/{DEVICE_DIR_NAME}}}" in SCRIPT.read_text(encoding="utf-8")


def test_sidecar_reads_the_script_from_the_fixed_named_configmap(rendered: list[Manifest], pod: Manifest) -> None:
    sidecar = _sidecar(pod)
    mount = _mount(sidecar, SCRIPT_DIR)
    assert "subPath" not in mount and mount.get("readOnly") is True
    assert _volume(pod, mount["name"])["configMap"]["name"] == SCRIPT_CONFIGMAP
    assert f"sh {SCRIPT_DIR}/{SCRIPT.name} " in sidecar["args"][0]
    configmaps = [
        doc
        for doc in rendered
        if doc["kind"] == "ConfigMap"
        and doc["metadata"]["name"] == SCRIPT_CONFIGMAP
        and doc["metadata"].get("namespace") == NAMESPACE
    ]
    assert len(configmaps) == 1, f"ConfigMap {NAMESPACE}/{SCRIPT_CONFIGMAP} がちょうど 1 つ無い: {len(configmaps)}"
    assert configmaps[0]["data"] == {SCRIPT.name: SCRIPT.read_text(encoding="utf-8")}


def test_sidecar_sees_the_host_devices_where_the_script_looks(pod: Manifest) -> None:
    mount = _mount(_sidecar(pod), HOST_DEV)
    assert _volume(pod, mount["name"])["hostPath"] == {"path": "/dev", "type": "Directory"}
    script = SCRIPT.read_text(encoding="utf-8")
    assert f"${{WORKSPACE_MOUNT_DEVICES:-{HOST_DEV}/longhorn}}" in script
    assert "${WORKSPACE_MOUNT_ROOT:-/work}" in script


def test_sidecar_starts_after_the_usb_marker_check_and_stops_without_shared_work(pod: Manifest) -> None:
    names = [c["name"] for c in pod["initContainers"]]
    assert names.index("own-work") < names.index(SIDECAR)
    args = _sidecar(pod)["args"][0]
    assert "findmnt -rn -o PROPAGATION -M /work" in args
    assert args.index("PROPAGATION") < args.index("exit 1") < args.index("while :")


# --- script を偽の命令で回す ---------------------------------------------------------------------------------------------

# 偽の命令: 状態は $FAKE の file — mounts(1 行「<major:minor> <付けた先>」)・fs/<volume の名>(file system の型か区画の表の印
# PTTYPE — 無ければ空の volume)・busy(在れば umount が使用中で断る)・calls(命令の記録)。attach した device は devices の dir の
# file で、中身が major:minor。
FAKES = {
    "stat": """
eval "path=\\${$#}"
[ -f "$path" ] || { echo "stat: cannot statx '$path': No such file or directory" >&2; exit 1; }
echo "block special file $(cat "$path")"
""",
    "findmnt": """
touch "$FAKE/mounts"
target=
while [ $# -gt 0 ]; do case $1 in -M) target=$2; shift ;; esac; shift; done
if [ -z "$target" ]; then cat "$FAKE/mounts"; exit 0; fi
found=1
while read -r number path; do [ "$path" = "$target" ] && { echo "$number"; found=0; }; done <"$FAKE/mounts"
exit $found
""",
    "blkid": """
eval "device=\\${$#}"
kind="$FAKE/fs/$(basename "$device")"
[ -f "$kind" ] || exit 2
[ "$(cat "$kind")" = PTTYPE ] && exit 0
cat "$kind"
""",
    "mkfs.ext4": """
echo "mkfs.ext4 $*" >>"$FAKE/calls"
eval "device=\\${$#}"
echo ext4 >"$FAKE/fs/$(basename "$device")"
""",
    "mount": """
echo "mount $*" >>"$FAKE/calls"
eval "target=\\${$#}"
eval "device=\\${$(($# - 1))}"
echo "$(cat "$device") $target" >>"$FAKE/mounts"
""",
    "umount": """
echo "umount $*" >>"$FAKE/calls"
[ ! -e "$FAKE/busy" ] || { echo "umount: $1: target is busy." >&2; exit 32; }
grep -v " $1\\$" "$FAKE/mounts" >"$FAKE/mounts.new"; mv "$FAKE/mounts.new" "$FAKE/mounts"
""",
    "fstrim": """
echo "fstrim $*" >>"$FAKE/calls"
echo "$2: 1 GiB (1073741824 bytes) trimmed"
""",
}


@dataclass(frozen=True)
class Bench:
    """偽の命令と、作業の root・device の dir・頼みの dir の 1 組。"""

    root: Path
    devices: Path
    fake: Path
    bin: Path
    requests: Path

    def attach(self, volume: str, number: str, fs: str | None = None) -> None:
        (self.devices / volume).write_text(number, encoding="utf-8")
        if fs is not None:
            (self.fake / "fs" / volume).write_text(fs, encoding="utf-8")

    def dir(self, name: str) -> Path:
        path = self.root / "ws" / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def mounted(self) -> dict[str, str]:
        mounts = self.fake / "mounts"
        lines = mounts.read_text(encoding="utf-8").splitlines() if mounts.exists() else []
        return {line.split(" ", 1)[1]: line.split(" ", 1)[0] for line in lines}

    def calls(self) -> list[str]:
        calls = self.fake / "calls"
        return calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []

    def ask(self, line: str) -> tuple[int, str]:
        """頼み 1 行を置いて script を走らせ、答え(code と訳)を読む。script の終了の code は答えの code と同じ。"""
        request = self.requests / "r1.req"
        request.write_text(line + "\n", encoding="utf-8")
        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "WORKSPACE_MOUNT_ROOT": str(self.root),
            "WORKSPACE_MOUNT_DEVICES": str(self.devices),
            "FAKE": str(self.fake),
            "TMPDIR": str(self.fake),
        }
        done = subprocess.run(["sh", str(SCRIPT), str(request)], env=env, capture_output=True, text=True, timeout=20)
        answer = self.requests / "r1.out"
        assert answer.exists(), f"答えが無い(stderr: {done.stderr})"
        assert not (self.requests / "r1.out.tmp").exists()
        code, _, notes = answer.read_text(encoding="utf-8").partition("\n")
        answer.unlink()
        assert int(code) == done.returncode, (code, done.returncode, done.stderr)
        return int(code), notes


@pytest.fixture
def bench(tmp_path: Path) -> Bench:
    top = tmp_path.resolve()
    made = Bench(top / "work", top / "devices", top / "fake", top / "bin", top / "requests")
    for directory in (made.root, made.devices, made.fake / "fs", made.bin, made.requests):
        directory.mkdir(parents=True)
    for name, body in FAKES.items():
        command = made.bin / name
        command.write_text("#!/bin/sh\n" + body.lstrip("\n"), encoding="utf-8")
        command.chmod(0o755)
    return made


def test_blank_volume_gets_an_ext4_owned_by_the_worker_then_is_mounted(bench: Bench) -> None:
    bench.attach("ws-a", "252:1")
    target = bench.dir("a")
    code, notes = bench.ask(f"mount ws-a {target}")
    assert code == 0, notes
    mkfs = [call for call in bench.calls() if call.startswith("mkfs.ext4 ")]
    assert mkfs == [f"mkfs.ext4 -q -m 0 -E root_owner=1000:1000,assume_storage_prezeroed=1 {bench.devices / 'ws-a'}"]
    assert bench.mounted() == {str(target): "252:1"}


def test_volume_with_ext4_is_mounted_without_making_a_new_one(bench: Bench) -> None:
    bench.attach("ws-a", "252:1", fs="ext4")
    target = bench.dir("a")
    assert bench.ask(f"mount ws-a {target}")[0] == 0
    assert bench.calls() == [f"mount -t ext4 {bench.devices / 'ws-a'} {target}"]


def test_mounting_the_same_volume_twice_mounts_once(bench: Bench) -> None:
    bench.attach("ws-a", "252:1", fs="ext4")
    target = bench.dir("a")
    assert bench.ask(f"mount ws-a {target}")[0] == 0
    code, notes = bench.ask(f"mount ws-a {target}")
    assert code == 0 and "既に" in notes
    assert len([call for call in bench.calls() if call.startswith("mount ")]) == 1


@pytest.mark.parametrize(
    ("line", "problem"),
    [
        ("format ws-a {a}", "知らない命令"),
        ("mount ws-a {a} more", "欄が 3 つでない"),
        ("mount WS-A {a}", "volume の名"),
        ("mount ../ws-a {a}", "volume の名"),
        ("mount ws-a /etc", "の下でない"),
        ("mount ws-a {root}", "の下でない"),
        ("mount ws-a {root}/ws/../ws/a", ". か .."),
        ("mount ws-a {a}/", ". か .."),
        ("mount ws-a {root}/ws/none", "在る dir でない"),
        ("mount ws-a {root}/link", "symlink"),
        ("mount ws-b {a}", "attach されていない"),
        ("fstrim ws-a {a}", "何も付いていない"),
    ],
    ids=[
        "verb",
        "fields",
        "upper-name",
        "path-name",
        "outside-root",
        "root-itself",
        "dot-dot",
        "trailing-slash",
        "no-dir",
        "symlink",
        "not-attached",
        "fstrim-unmounted",
    ],
)
def test_out_of_the_rules_is_refused_and_nothing_runs(bench: Bench, line: str, problem: str) -> None:
    bench.attach("ws-a", "252:1")
    target = bench.dir("a")
    (bench.root / "link").symlink_to(target)
    code, notes = bench.ask(line.format(a=target, root=bench.root))
    assert code == 1 and problem in notes, notes
    assert bench.calls() == []
    assert bench.mounted() == {}


def test_another_thing_at_the_target_is_not_covered(bench: Bench) -> None:
    bench.attach("ws-a", "252:1", fs="ext4")
    bench.attach("ws-b", "252:2", fs="ext4")
    target = bench.dir("a")
    assert bench.ask(f"mount ws-a {target}")[0] == 0
    code, notes = bench.ask(f"mount ws-b {target}")
    assert code == 1 and "別の物" in notes
    assert bench.mounted() == {str(target): "252:1"}


def test_one_volume_is_mounted_at_one_dir_only(bench: Bench) -> None:
    bench.attach("ws-a", "252:1", fs="ext4")
    first, second = bench.dir("a"), bench.dir("b")
    assert bench.ask(f"mount ws-a {first}")[0] == 0
    code, notes = bench.ask(f"mount ws-a {second}")
    assert code == 1 and str(first) in notes
    assert bench.mounted() == {str(first): "252:1"}


@pytest.mark.parametrize("found", ["xfs", "PTTYPE"], ids=["other-fs", "partition-table"])
def test_contents_other_than_ext4_are_never_formatted(bench: Bench, found: str) -> None:
    bench.attach("ws-a", "252:1", fs=found)
    code, notes = bench.ask(f"mount ws-a {bench.dir('a')}")
    assert code == 1 and "ext4 でない" in notes
    assert bench.calls() == []


def test_umount_takes_off_the_named_volume(bench: Bench) -> None:
    bench.attach("ws-a", "252:1", fs="ext4")
    target = bench.dir("a")
    assert bench.ask(f"mount ws-a {target}")[0] == 0
    assert bench.ask(f"umount ws-a {target}")[0] == 0
    assert bench.mounted() == {}
    assert bench.calls()[-1] == f"umount {target}"


def test_umount_of_a_bare_dir_does_nothing(bench: Bench) -> None:
    bench.attach("ws-a", "252:1", fs="ext4")
    code, notes = bench.ask(f"umount ws-a {bench.dir('a')}")
    assert code == 0 and "何も付いていない" in notes
    assert bench.calls() == []


def test_umount_does_not_take_off_another_volume(bench: Bench) -> None:
    bench.attach("ws-a", "252:1", fs="ext4")
    bench.attach("ws-b", "252:2", fs="ext4")
    target = bench.dir("a")
    assert bench.ask(f"mount ws-a {target}")[0] == 0
    code, notes = bench.ask(f"umount ws-b {target}")
    assert code == 1 and "ws-b でない" in notes
    assert bench.mounted() == {str(target): "252:1"}


def test_busy_volume_stays_mounted_and_the_reason_is_answered(bench: Bench) -> None:
    bench.attach("ws-a", "252:1", fs="ext4")
    target = bench.dir("a")
    assert bench.ask(f"mount ws-a {target}")[0] == 0
    (bench.fake / "busy").touch()
    code, notes = bench.ask(f"umount ws-a {target}")
    assert code == 1 and "target is busy" in notes
    assert bench.mounted() == {str(target): "252:1"}


def test_fstrim_runs_on_the_mounted_volume(bench: Bench) -> None:
    bench.attach("ws-a", "252:1", fs="ext4")
    target = bench.dir("a")
    assert bench.ask(f"mount ws-a {target}")[0] == 0
    code, notes = bench.ask(f"fstrim ws-a {target}")
    assert code == 0 and "trimmed" in notes
    assert bench.calls()[-1] == f"fstrim -v {target}"


def test_script_runs_with_the_tools_of_the_image_on_its_path() -> None:
    """script は blkid・mkfs.ext4・fstrim の在る /usr/sbin を道の後ろに足す(係の container の道に無くても引ける・偽の命令を替えない)。"""
    assert 'PATH="$PATH:/usr/sbin:/sbin"' in SCRIPT.read_text(encoding="utf-8")
