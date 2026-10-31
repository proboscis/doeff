"""doeff-hy-check(doeff_hy._static_check の main)が、展開と pyright の前に機体の検の枠を 1 つ取る失敗ケース(agora-redesign)。

直す前は、会話が直に起動した doeff-hy-check(`uv run doeff-hy-check …`・`python -m doeff_hy._static_check …`)は枠を取らずに走った —
枠に入っていたのは agora-controllers の手の入口(scripts/hy_type_check.hy の gate)の子だけだった。

縁の検は機体の正本(~/dotfiles/agent/tests/machine_check_slots.py)を検体の home の下へ写し、隣に宣言(K = 1・置き場は検体の dir)を
置く — 機体の本物の枠を埋めず、本物の保持者に待たされない。枠を持つ別の process は正本の命令の包み run。doeff-hy-check は本物の
入口(`python -m doeff_hy._static_check`)を子の process で撃ち、偽にするのは pyright だけ(撃たれた事を file に記す)。正本の無い宿
(doeff だけを clone した機体)では skip。

M-slot(doeff_hy._check_slot)と正本の間の契約は、口だけを持つ正本(INTERFACE_ONLY_CANON)で確かめる — 方策と持ち物は中を開かず正本の口へ
渡す・正本はどの名の環境変数も読める・正本の take の中の失敗は名乗って素通し・
1 枠の中の並走の数は正本の口 process_width が答え、口が無い・落ちた・形の違う答えでも取った枠は持ち続ける。
方策と持ち物を開かない形の機械の検は tests/test_check_slot_opacity.py。
"""

import io
import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest
from doeff_hy._check_slot import CANON_UNDER_HOME, MARK, MODULE_KEY_PREFIX, SlotHeld, canon_module, take_slot
from doeff_hy._env_places import cache_home

#: 機体の正本(検の process の home の下 — 検体の home へ写す元)。
CANON = Path.home() / CANON_UNDER_HOME
#: 正本が読む環境変数の名の見本(doeff が知らない名 — 正本がどの名を読んでも答えが届く事を確かめる)。
PROBE_NAME = "DOEFF_HY_CHECK_SLOT_PROBE"
#: 口だけを持つ正本(検体の home の正本の置き場に置く)。read_policy は印の object を返し、take は受けた方策が印そのものか・
#: 環境変数 PROBE_NAME の読みの答え・名乗りを持ち物に記して返し、持ち物は解かれた回数を数える。記録は module の TAKES(take が受けた物)
#: と WIDTHS(process_width が受けた物)に残る — 検は sys.modules の正本から読む(SlotHeld は持ち物を預からない)。__FAIL__ の行は
#: take の中の失敗、__WIDTH__ の行は process_width の答え(口自体を外す変異は WITHOUT_WIDTH_MOUTH)。
INTERFACE_ONLY_CANON = f"""
POLICY = object()
TAKES = []
WIDTHS = []


class PolicyUnreadable(Exception):
    pass


class SlotWaitExpired(Exception):
    pass


class Seen:
    def __init__(self, same, probe, label):
        self.same = same
        self.probe = probe
        self.label = label
        self.released = 0

    def release(self):
        self.released += 1


def read_policy():
    return POLICY


def take(policy, label, environ, out):
    __FAIL__
    seen = Seen(policy is POLICY, environ.get("{PROBE_NAME}"), label)
    TAKES.append(seen)
    return seen


def process_width(policy, holding, environ):
    WIDTHS.append((policy is POLICY, holding is TAKES[-1] if TAKES else holding, environ.get("{PROBE_NAME}")))
    return __WIDTH__
"""

@dataclass(frozen=True)
class Scene:
    """検体の世界 — 正本の写しを置いた home・その写し・検める根・偽の pyright と、それが撃たれた事を記す file。"""

    home: Path
    canon: Path
    root: Path
    pyright: Path
    started: Path

    def check_command(self) -> list[str]:
        """会話が直に撃つのと同じ入口で、根の 1 file を検める命令。"""
        return [
            sys.executable,
            "-m",
            "doeff_hy._static_check",
            "--root",
            str(self.root),
            "--no-cache",
            "--pyright",
            str(self.pyright),
            str(self.root / "probe.hy"),
        ]

    def pyright_runs(self) -> list[str]:
        return (
            self.started.read_text(encoding="utf-8").splitlines() if self.started.exists() else []
        )


def _scene(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wait_s: float, processes: int = 1) -> Scene:
    """機体の正本を検体の home へ写し、隣に宣言(K = 1・置き場 tmp_path/slots・待ちの上限 wait_s 秒)を置いて、子の process の
    HOME を検体の home にする。Hy の bytecode などの cache の根は今の機体の物のまま(冷えた cache で起動に分を払わない)。"""
    if not CANON.is_file():
        pytest.skip(f"機体の正本 {CANON} が無い宿(dotfiles を置かない機体)")
    home = tmp_path / "home"
    canon = home / CANON_UNDER_HOME
    canon.parent.mkdir(parents=True)
    shutil.copy2(CANON, canon)
    # 宣言の鍵は正本の policy_of が求める物を全部置く — 正本が processesPerSlot を求めるようになって(dotfiles 142108b9f)、
    # 無い宣言は読めないと断られ枠を取らずに走った。
    canon.with_name("machine_check_slots.json").write_text(
        json.dumps(
            {
                "slots": 1,
                "processesPerSlot": processes,
                "dir": str(tmp_path / "slots"),
                "waitLimitSeconds": wait_s,
            }
        ),
        encoding="utf-8",
    )
    root = tmp_path / "repo"
    root.mkdir()
    (root / "probe.hy").write_text("(setv answer 1)\n", encoding="utf-8")
    started = tmp_path / "pyright-started.log"
    pyright = tmp_path / "pyright"
    pyright.write_text(
        f"#!/bin/sh\necho started >> {shlex.quote(str(started))}\necho '{{\"generalDiagnostics\": []}}'\n",
        encoding="utf-8",
    )
    pyright.chmod(0o755)
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home()))
    monkeypatch.setenv("HOME", str(home))
    return Scene(home, canon, root, pyright, started)


def _blocker(scene: Scene) -> "subprocess.Popen[str]":
    """写しの枠を正本の命令の包み run で持つ別の process(stdin を閉じるまで持つ)。"""
    blocker = subprocess.Popen(
        [
            sys.executable,
            str(scene.canon),
            "run",
            "--label",
            "blocker-holds",
            "--",
            sys.executable,
            "-c",
            "import sys; print('held', flush=True); sys.stdin.read()",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert blocker.stdout is not None
    assert blocker.stdout.readline() == "held\n", "別の process が写しの枠を取れなかった"
    return blocker


def _end(blocker: "subprocess.Popen[str]") -> None:
    if blocker.stdin is not None and not blocker.stdin.closed:
        blocker.stdin.close()
    if blocker.poll() is None:
        blocker.kill()
    blocker.wait(timeout=15)


def test_a_full_slot_holds_a_directly_started_doeff_hy_check_until_it_frees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 受入 c07865153: 枠(写しの宣言 K = 1)が埋まっている間、会話が直に起動した doeff-hy-check は pyright を撃たずに待って保持者
    # (pid と名乗り)を名乗り、空いた拍に枠を取って検める。直す前は枠を見ずに検め、名乗りの行を 1 つも出さずに終わった。
    scene = _scene(tmp_path, monkeypatch, 60.0)
    blocker = _blocker(scene)
    try:
        check = subprocess.Popen(
            scene.check_command(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        assert check.stderr is not None
        waiting = next((line for line in check.stderr if MARK in line), "")
        runs_while_full = scene.pyright_runs()
        assert blocker.stdin is not None
        blocker.stdin.close()
        _, rest = check.communicate(timeout=120)
        assert f"pid {blocker.pid} blocker-holds" in waiting, (waiting, rest)
        assert "待つのは doeff-hy-check repo" in waiting, waiting
        assert runs_while_full == [], "枠が埋まっている間に pyright を撃った"
        assert check.returncode == 0, rest
        assert "を取った(待ち" in rest, rest
        assert scene.pyright_runs() == ["started"]
        assert blocker.wait(timeout=15) == 0, "枠を持っていた process が終わらない"
    finally:
        _end(blocker)


def test_doeff_hy_check_runs_nothing_when_the_slot_wait_expires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 待ちが正本の宣言の上限を越えたら、展開も pyright も起こさずに「走れなかった」(exit 2)で終わる — 呼び手(agora-controllers の
    # hy_type_check.hy・hy_type_trend.hy)は 0・1 以外を「測れなかった」と読む。合格(0)にも赤(1)にもしない。
    scene = _scene(tmp_path, monkeypatch, 1.0)
    blocker = _blocker(scene)
    try:
        done = subprocess.run(
            scene.check_command(), capture_output=True, text=True, timeout=120, check=False
        )
        assert done.returncode == 2, done.stderr
        assert f"doeff-hy-check: 走れなかった: {MARK}" in done.stderr, done.stderr
        assert scene.pyright_runs() == [], "待ちの上限を越えたのに pyright を撃った"
    finally:
        _end(blocker)


def test_doeff_hy_check_inside_a_slot_holder_takes_no_second_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 枠を持つ process の子孫(gate が起こす doeff-hy-check・pytest の中の呼び)は 2 つ目を取らない — K = 1 で祖先が唯一の枠を持っても
    # 待たずに検める(待てば上限 5 秒で exit 2 になる)。
    scene = _scene(tmp_path, monkeypatch, 5.0)
    done = subprocess.run(
        [
            sys.executable,
            str(scene.canon),
            "run",
            "--label",
            "outer-holds",
            "--",
            *scene.check_command(),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert MARK not in done.stderr, done.stderr
    assert scene.pyright_runs() == ["started"]


def test_a_host_without_the_canon_takes_no_slot_and_says_nothing(tmp_path: Path) -> None:
    # 正本の無い宿(doeff だけを clone した機体)は黙って素通し — 枠を取らず、名乗らない。
    out = io.StringIO()
    held = take_slot(tmp_path / "home", "doeff-hy-check repo", out)
    assert held == SlotHeld(None)
    assert out.getvalue() == ""


def _interface_canon(home: Path, fail: str = "pass", width: str = "5") -> Path:
    target = home / CANON_UNDER_HOME
    target.parent.mkdir(parents=True)
    target.write_text(
        INTERFACE_ONLY_CANON.replace("__FAIL__", fail).replace("__WIDTH__", width), encoding="utf-8"
    )
    return target


def _without_width_mouth(home: Path) -> None:
    """process_width の口が無い正本(幅の口を足す前の dotfiles の先端)。"""
    target = home / CANON_UNDER_HOME
    target.parent.mkdir(parents=True)
    source = INTERFACE_ONLY_CANON.replace("__FAIL__", "pass")
    target.write_text(source[: source.index("def process_width")], encoding="utf-8")


def _canon_of(target: Path) -> ModuleType:
    """take_slot が読んだ口だけの正本(sys.modules の同じ個体 — 記録 TAKES・WIDTHS を持つ)。"""
    return sys.modules[MODULE_KEY_PREFIX + os.path.realpath(target)]


def test_the_declared_policy_and_any_environment_name_reach_the_canon_take(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 方策は正本の read_policy の答えのまま take へ届き(値を縮める・作り直す口が在れば別の物になる)、正本は doeff が知らない名の
    # 環境変数も読める。盲検 A の反例: 正本へ渡す env を保持者の印 1 つの写しに絞っていたので、正本が会話の id を読むようになると
    # doeff-hy-check だけが黙って会話ごとの数えの外で走った。
    canon = _interface_canon(tmp_path / "home")
    monkeypatch.setenv(PROBE_NAME, "seen-by-the-canon")
    out = io.StringIO()
    held = take_slot(tmp_path / "home", "doeff-hy-check repo", out)
    assert isinstance(held, SlotHeld), out.getvalue()
    assert held.let_go is not None, out.getvalue()
    (seen,) = _canon_of(canon).TAKES
    assert vars(seen) == {
        "same": True,
        "probe": "seen-by-the-canon",
        "label": "doeff-hy-check repo",
        "released": 0,
    }
    assert out.getvalue() == ""
    held.release()
    held.release()
    assert seen.released == 2, "SlotHeld.release は正本の解く口をそのまま呼ぶ(2 度目を無視するのは正本の仕事)"


def test_the_canon_width_is_asked_with_the_same_opaque_values_and_reaches_the_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1 枠の中の並走の数は正本の口 process_width が答える。口へ渡るのは take の前に得た方策・take の答え・環境そのもの(入れ子の規則と
    # env の名は正本の中 — 盲検 B の反例は、持ち物から欄を読んで doeff が別の規則を持った)。答えは SlotHeld.width でそのまま届く。
    canon = _interface_canon(tmp_path / "home", width="7")
    monkeypatch.setenv(PROBE_NAME, "seen-by-the-canon")
    out = io.StringIO()
    held = take_slot(tmp_path / "home", "doeff-hy-check repo", out)
    assert isinstance(held, SlotHeld) and held.width == 7, (held, out.getvalue())
    assert _canon_of(canon).WIDTHS == [(True, True, "seen-by-the-canon")]
    assert out.getvalue() == ""


def test_a_canon_without_the_width_mouth_still_holds_the_slot_and_says_so(tmp_path: Path) -> None:
    # 盲検 A の反例: 口の揃いが全か無かで、正本に無い口を足すと枠そのものを失った。幅の口は任意 — 古い正本でも枠は取り、
    # 幅は届かない(None・呼び手が宣言の無い機体の既定で走る)ことを 1 行名乗る。
    _without_width_mouth(tmp_path / "home")
    out = io.StringIO()
    held = take_slot(tmp_path / "home", "doeff-hy-check repo", out)
    assert isinstance(held, SlotHeld), out.getvalue()
    assert held.let_go is not None and held.width is None, held
    assert out.getvalue().startswith(f"{MARK} 枠の正本に並走の数の口(process_width)が無い"), out.getvalue()


def test_a_failing_width_mouth_keeps_the_slot_that_was_taken(tmp_path: Path) -> None:
    # 幅の口の失敗を take の失敗と同じ囲いに入れると、取った枠の fd を持ったまま「枠を取らずに走る」と名乗る(盲検 A の付随の危険)。
    # 幅の口が落ちても取った枠は持ち続け(解く口が残る)、幅だけが届かない。
    canon = _interface_canon(tmp_path / "home", width="1 / 0")
    out = io.StringIO()
    held = take_slot(tmp_path / "home", "doeff-hy-check repo", out)
    assert isinstance(held, SlotHeld) and held.let_go is not None and held.width is None, (held, out.getvalue())
    assert "process_width が落ちた" in out.getvalue(), out.getvalue()
    held.release()
    assert _canon_of(canon).TAKES[0].released == 1


@pytest.mark.parametrize("answer", ["0", "-1", "True", "'2'", "2.0"])
def test_a_width_answer_that_is_not_a_positive_integer_is_named_and_dropped(tmp_path: Path, answer: str) -> None:
    # 口の形の確かめ(数の判断ではない): 正の整数でない答えは幅にしない。枠は持ち続ける。
    _interface_canon(tmp_path / "home", width=answer)
    out = io.StringIO()
    held = take_slot(tmp_path / "home", "doeff-hy-check repo", out)
    assert isinstance(held, SlotHeld) and held.let_go is not None and held.width is None, (held, out.getvalue())
    assert "正の整数でない" in out.getvalue(), out.getvalue()


def test_a_failing_canon_take_lets_the_check_run_and_says_why(tmp_path: Path) -> None:
    # 正本の take の中の失敗(置き場を作れない OSError など)は 1 行名乗って素通し — traceback のまま抜けると main の終了 1(新しい赤)
    # と区別できない(盲検 A の副次の実測: 作れない置き場で exit 1・stdout 空)。
    _interface_canon(tmp_path / "home", "raise OSError('置き場を作れない')")
    out = io.StringIO()
    held = take_slot(tmp_path / "home", "doeff-hy-check repo", out)
    assert held == SlotHeld()
    assert out.getvalue().startswith(
        f"{MARK} 枠の正本の take が落ちたので枠を取らずに走る(OSError"
    ), out.getvalue()


def _width_probe(
    scene: Scene,
    env_name: str,
    *,
    hand_down: str | None = None,
    wrapped: bool = True,
) -> dict[str, object]:
    """枠の取り手(doeff の take_slot)が答えた幅と、その子が見た幅の env の値を JSON で受ける。子を正本の命令の包みの中
    (wrapped)か直に撃つ。hand_down = 包みの中でもう 1 度包む前に、外側の入口が子へ配る並走の数(検の中の検 — 配る口は正本の
    公開の定数 env_name)。"""
    show = [
        sys.executable,
        "-c",
        "import json, os, sys; from pathlib import Path; from doeff_hy._check_slot import take_slot;"
        " held = take_slot(Path.home(), 'width-probe', sys.stderr);"
        " print(json.dumps({'env': os.environ.get(sys.argv[1]), 'width': held.width})); held.release()",
        env_name,
    ]
    command = show
    if wrapped:
        command = [sys.executable, str(scene.canon), "run", "--label", "inner", "--", *show]
    if hand_down is not None:
        relay = [
            sys.executable,
            "-c",
            "import os, subprocess, sys; env = dict(os.environ, **{sys.argv[1]: sys.argv[2]});"
            " sys.exit(subprocess.run(sys.argv[3:], env=env).returncode)",
            env_name,
            hand_down,
            *command,
        ]
        command = [sys.executable, str(scene.canon), "run", "--label", "outer", "--", *relay]
    done = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    assert done.returncode == 0, done.stderr
    answer: dict[str, object] = json.loads(done.stdout.strip().splitlines()[-1])
    return answer


def _canon_answer(scene: Scene, env_name: str, seen_env: object) -> int:
    """同じ口を正本の module で直に引いた答え(包みの中の入口 = 持ち物が None・子が見た env の値)— 期待の値を doeff の検に
    書かず、正本の規則の座に問う(規則や env の名が替わっても、doeff の検は正本の答えとの一致だけを見る)。"""
    module = canon_module(scene.home, io.StringIO())
    assert module is not None
    environ = {} if seen_env is None else {env_name: str(seen_env)}
    return int(module.process_width(module.read_policy(), None, environ))


@pytest.mark.parametrize("processes", [1, 2, 3])
def test_the_width_follows_the_declaration_for_a_direct_check_and_for_a_check_inside_the_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, processes: int
) -> None:
    # 実物の正本: 枠を自分で取る直の入口は宣言の数(契約: 枠を取った入口は宣言の値)。命令の包みの中(祖先が枠を持つ — take は None)
    # の入口は、包みが子へ配った値を受けた正本の口の答えと同じ数(期待は正本に問う)で、宣言を越えない。宣言の数を替えるだけで
    # 従う(コードは変わらない)。
    scene = _scene(tmp_path, monkeypatch, 60.0, processes=processes)
    module = canon_module(scene.home, io.StringIO())
    if module is None or not callable(vars(module).get("process_width")):
        pytest.skip("機体の正本に並走の数の口 process_width が無い(dotfiles を追随する前の機体)")
    name = module.PROCESSES_ENV
    # 子は検の process の環境を受け継ぐ(os.environ を値として組まない — DOEFF004)。前の値の有る無しは monkeypatch で決める。
    monkeypatch.delenv(name, raising=False)
    direct = _width_probe(scene, name, wrapped=False)
    assert direct["width"] == processes, direct
    monkeypatch.setenv(name, "1")  # 呼び手の前の値は枠の外の値 — 自分で枠を取った入口は宣言の数
    stale = _width_probe(scene, name, wrapped=False)
    assert stale["width"] == processes, stale
    monkeypatch.delenv(name, raising=False)
    inside = _width_probe(scene, name)
    assert inside["width"] == _canon_answer(scene, name, inside["env"]), inside
    assert isinstance(inside["width"], int) and inside["width"] <= processes, inside


def test_a_check_inside_the_wrapper_keeps_the_smaller_width_the_outer_entry_handed_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 外側の入口が子へ配った残りの幅(1)は、内側の入口が宣言(2)へ戻さない — 木全体の並走 ≤ 宣言(契約: 配った値以下)。内側の入口が
    # 受ける数は、子が見た値を受けた正本の口の答えと同じで、配られた数を越えない。規則そのものの値は正本の検が持つ。
    scene = _scene(tmp_path, monkeypatch, 60.0, processes=2)
    module = canon_module(scene.home, io.StringIO())
    if module is None or not callable(vars(module).get("process_width")):
        pytest.skip("機体の正本に並走の数の口 process_width が無い(dotfiles を追随する前の機体)")
    name = module.PROCESSES_ENV
    done = _width_probe(scene, name, hand_down="1")
    assert done["env"] is not None, "外側の入口が配った数が子へ届いていない"
    assert done["width"] == _canon_answer(scene, name, done["env"]), done
    assert isinstance(done["width"], int) and done["width"] <= 1, done
