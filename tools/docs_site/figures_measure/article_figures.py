"""Writes three click-through figures of docs/articles/algebraic-effects-in-python.md: article-opening, monte-carlo and
hundred-fights (docs/assets/figures/<name>.html). They tell what the article's videos tell, one step per click, with the
narration of the video (tools/docs-video/src/pages/article-*/narration.json) as the captions, in finer steps.

The numbers come from the runs behind the videos (tools/docs-video/src/pages/article-*/measure/*.py): the random bot's wins
by seed, seed 3's second Choose and its playouts, the results of the first three tasks and the damage by card. A figure is
written here, not by hand, because a hundred task squares and a dozen JSON states are easy to get wrong by hand.

Usage (from the repository root): uv run --no-project python tools/docs_site/figures_measure/article_figures.py
"""

from __future__ import annotations

import json
from pathlib import Path

FIGURES = Path(__file__).resolve().parents[3] / "docs" / "assets" / "figures"

# random_wins_by_seed of article-hundred-fights/measure/hundred.py: seed n won when character n is "W".
RANDOM_WINS = "WWWWW.WWW.WWWWWWW.WWW.WW.W.W.WWWWWWWW.WW.WWW.WW.WW.WW.WW..W.WWWWWW.WWWWWWWW.WWW.WW.W.WWW..W...W.WWWW"
assert len(RANDOM_WINS) == 100 and RANDOM_WINS.count("W") == 73


def step(caption: str, **state: object) -> str:
    """One <li> of .sf-steps: the caption, and the state as JSON in a single-quoted attribute."""
    data = json.dumps(state, ensure_ascii=False).replace("&", "&amp;").replace("'", "&#39;")
    return f"  <li data-state='{data}'>{caption}</li>"


def tasks(anchors: tuple[int, ...]) -> str:
    """The hundred fights as squares; the seeds the random bot lost are "is-loss", and the named seeds are anchors."""
    cells = (
        "<span"
        + ("" if won == "W" else ' class="is-loss"')
        + (f' data-anchor="t{seed}"' if seed in anchors else "")
        + "></span>"
        for seed, won in enumerate(RANDOM_WINS)
    )
    return f'<div class="sf-tasks" data-anchor="tasks">{"".join(cells)}</div>'


def rings(count: int, core: str) -> str:
    """count rings, the outermost first in the markup, around core: ring i has the anchors r<i>, n<i> (its name) and m<i>
    (a room for what its handler does there), and the slot r<i> for its name."""
    opening = "".join(
        f'<div class="sf-ring" data-anchor="r{i}"><span class="sf-ring-name" data-anchor="n{i}"><span data-slot="r{i}"></span>'
        f'</span><div class="sf-ring-room" data-anchor="m{i}"></div>'
        for i in range(count, 0, -1)
    )
    return f'  <div class="sf-rings">\n    {opening}\n{core}\n    {"</div>" * count}\n  </div>'


def end(anchor: str) -> dict[str, str]:
    return {"at": anchor, "place": "end"}


def figure(comment: str, stage: str, steps: list[str]) -> str:
    return f"<!-- {comment} -->\n<div class=\"sf-stage\">\n{stage}\n</div>\n<ol class=\"sf-steps\">\n" + "\n".join(steps) + "\n</ol>\n"


# ---- article-opening: the opening of the article (video article-opening). -------------------------------------------------

OPENING_CORE = f"""      <div class="sf-core" data-anchor="core">
        <div class="sf-core-head"><span data-anchor="core-name" data-slot="core"></span><span class="sf-unchanged" data-anchor="unchanged">0 lines changed</span></div>
        <div class="sf-code">
          <div class="sf-line"><span class="co"># spire.py (excerpt)</span></div>
          <div class="sf-line" data-anchor="c-while"><span class="kw">while</span> s.hp &gt; 0 <span class="kw">and</span> s.enemy_hp &gt; 0:</div>
          <div class="sf-line" data-anchor="c-draw">    s, hand = <span class="kw">yield</span> draw_hand(s)  <span class="co"># it yields Roll</span></div>
          <div class="sf-line" data-anchor="c-show">    <span class="kw">yield</span> Show(<span class="st">f"T{{s.turn}} you {{s.hp}} | enemy {{s.enemy_hp}}"</span>)</div>
          <div class="sf-line" data-anchor="c-choose">    pick = <span class="kw">yield</span> Choose(s, hand, energy)</div>
          <div class="sf-line" data-anchor="c-hit">    s, dmg = hit(s, hand[pick])</div>
          <div class="sf-line" data-anchor="c-played">    <span class="kw">yield</span> Played(card, dmg)</div>
          <div class="sf-line" data-anchor="c-roll">    intent = <span class="kw">yield</span> Roll(3)</div>
          <div class="sf-line" data-anchor="c-return"><span class="kw">return</span> (<span class="st">"win"</span> <span class="kw">if</span> s.enemy_hp &lt;= 0 <span class="kw">else</span> <span class="st">"lose"</span>), s.turn</div>
        </div>
        {tasks(())}
      </div>"""

OPENING_STAGE = f"""  <div class="sf-call">
    <code data-slot="call"></code>
    <span class="sf-result is-text" data-anchor="result"><span data-slot="result"></span></span>
  </div>
{rings(5, OPENING_CORE)}
  <div class="sf-token is-effect" data-token="choose">Choose</div>
  <div class="sf-token is-effect" data-token="roll">Roll</div>
  <div class="sf-token is-effect" data-token="show">Show</div>
  <div class="sf-token is-effect" data-token="played">Played</div>
  <div class="sf-token is-k" data-token="k">k <small>the rest of battle</small></div>
  <div class="sf-token is-value" data-token="answer">the next move</div>
  <div class="sf-token is-value" data-token="tape">tape</div>
  <div class="sf-token is-value" data-token="note">runs battle(s) itself</div>"""

OFF5 = {f"r{i}": "is-off" for i in range(1, 6)}
REST_OF_BATTLE = {"c-hit": "is-k", "c-played": "is-k", "c-roll": "is-k", "c-return": "is-k"}
LAYER1 = {"r1": "scripted(moves)", "r2": "dice(7)", "r3": "printed", "r4": "quiet_stats", "r5": ""}
OUT = {"from": end("core-name")}

OPENING_STEPS = [
    step(
        "This is one function: <code>battle</code>, a small card fight. It asks for dice (<code>Roll</code>), a player "
        "(<code>Choose</code>), a screen (<code>Show</code>) and a count (<code>Played</code>). It never says who answers.",
        now=["c-draw", "c-show", "c-choose", "c-played", "c-roll"],
        marks=OFF5,
        values={"call": "battle()", "result": "", "core": "battle()", **{f"r{i}": "" for i in range(1, 6)}},
    ),
    step(
        "Each question is an effect. At <code>yield Choose(...)</code> the function stops and hands the question out, and what "
        "it has left to do becomes a value too.",
        now=["c-choose"],
        marks={**OFF5, **REST_OF_BATTLE},
        tokens={"choose": end("c-choose"), "k": end("c-return")},
    ),
    step(
        "The effect travels out to a handler, and the handler also gets the rest of the function, called <code>k</code>.",
        now=["c-choose"],
        marks={**OFF5, "r1": "is-new", "m1": "is-on", **REST_OF_BATTLE},
        values={"call": "run(scripted(moves)(battle()))", "r1": "scripted(moves)"},
        tokens={"choose": {"from": end("c-choose"), **end("n1")}, "k": {"from": end("c-return"), "at": "m1"}},
    ),
    step(
        "<code>scripted</code> answers from its list of moves. It resumes <code>k</code> with the next move, and "
        "<code>battle</code> goes on from its <code>yield</code>.",
        now=["c-choose", "c-hit"],
        marks={**{f"r{i}": "is-off" for i in range(2, 6)}, "m1": "is-on"},
        tokens={
            "answer": {"from": end("n1"), **end("c-choose"), "merge": True},
            "k": {"from": "m1", **end("c-choose"), "merge": True, "delay": 0.3},
        },
    ),
    step(
        "Stack handlers around it. Each effect travels out to the first ring whose handler takes it: <code>Choose</code> to "
        "the script, <code>Roll</code> to the dice, <code>Show</code> to the screen, <code>Played</code> to the counter.",
        marks={"r2": "is-new", "r3": "is-new", "r4": "is-new", "r5": "is-off"},
        values={"call": "run(quiet_stats(printed(dice(7)(scripted(moves)(battle())))))", **LAYER1},
        tokens={
            "choose": {**OUT, **end("n1")},
            "roll": {**OUT, **end("n2"), "delay": 0.15},
            "show": {**OUT, **end("n3"), "delay": 0.3},
            "played": {**OUT, **end("n4"), "delay": 0.45},
        },
    ),
    step(
        "A scripted player and a screen give the same fight on every run: three runs, <code>('win', 6)</code> each time.",
        marks={"r5": "is-off"},
        values={"result": "('win', 6) ×3"},
    ),
    step(
        "Swap in a random bot for the script. It answers <code>Choose</code> with a <code>Roll</code> of its own, which "
        "travels on out to the dice.",
        marks={"r1": "is-new", "r5": "is-off"},
        values={"call": "run(quiet_stats(printed(dice(3)(random_bot(battle())))))", "r1": "random_bot", "r2": "dice(3)", "result": ""},
        tokens={"choose": {**OUT, **end("n1")}, "roll": {"from": end("n1"), **end("n2"), "delay": 0.6}},
    ),
    step(
        "Take the screen away: <code>silent</code> takes every <code>Show</code> and draws nothing. The function does not "
        "change, not one line of <code>battle</code>.",
        marks={"r3": "is-new", "r5": "is-off", "unchanged": "is-on"},
        values={"call": "run(quiet_stats(silent(dice(3)(random_bot(battle())))))", "r3": "silent", "result": "('win', 4)"},
        tokens={"show": {**OUT, **end("n3")}},
    ),
    step(
        "Add a recorder. <code>record</code> passes each effect on out and keeps the answer that comes back: one fight leaves "
        "32 answers on the tape.",
        marks={"r1": "is-new"},
        values={
            "call": "run(quiet_stats(silent(dice(3)(random_bot(record(tape)(battle()))))))",
            "r1": "record(tape)", "r2": "random_bot", "r3": "dice(3)", "r4": "silent", "r5": "quiet_stats",
            "result": "tape: 32 answers",
        },
        tokens={
            "choose": {**OUT, **end("n2"), "via": [end("n1")]},
            "roll": {**OUT, **end("n3"), "via": [end("n1")], "delay": 0.3},
            "tape": {"from": end("n2"), **end("n1"), "delay": 1.4},
        },
    ),
    step(
        "<code>replay(tape)</code> answers every <code>Choose</code> and <code>Roll</code> from the tape. With no dice and "
        "no player, the whole fight comes out the same.",
        marks={"r1": "is-new", "r4": "is-off", "r5": "is-off"},
        values={
            "call": "run(quiet_stats(silent(replay(tape)(battle()))))",
            "r1": "replay(tape)", "r2": "silent", "r3": "quiet_stats", "r4": "", "r5": "", "result": "('win', 4) same: True",
        },
        tokens={
            "choose": {**OUT, **end("n1"), "merge": True},
            "roll": {**OUT, **end("n1"), "delay": 0.15, "merge": True},
            "tape": end("n1"),
        },
    ),
    step(
        "A Monte Carlo bot answers a question by running the fight itself, while the fight that asked waits as <code>k</code>.",
        marks={"r1": "is-new", "r5": "is-off", "m1": "is-on", **REST_OF_BATTLE},
        values={
            "call": "run(quiet_stats(silent(dice(3)(monte_carlo(PLAYOUTS)(battle())))))",
            "r1": "monte_carlo(PLAYOUTS)", "r2": "dice(3)", "r3": "silent", "r4": "quiet_stats", "r5": "", "result": "('win', 4)",
        },
        tokens={
            "choose": {"from": end("c-choose"), **end("n1")},
            "k": end("c-return"),
            "note": {"from": "m1", "at": "m1", "delay": 1.0},
        },
    ),
    step(
        "Run a hundred fights at once, each a task with its own dice, under <code>scheduled</code>. The random bot wins 73.",
        marks={"r1": "is-off", "r2": "is-off", "r3": "is-off", "r5": "is-new", "tasks": "is-on is-scored"},
        values={
            "call": "run(scheduled(quiet_stats(win_rate(random_bot, 100))))",
            "r1": "", "r2": "", "r3": "", "r4": "quiet_stats", "r5": "scheduled",
            "core": "100 × battle(), each with random_bot · dice(seed) · silent", "result": "73 of 100 won",
        },
    ),
    step(
        "Give the same hundred fights the Monte Carlo bot, and it wins all one hundred.",
        marks={"r1": "is-off", "r2": "is-off", "r3": "is-off", "tasks": "is-on is-scored is-all-win"},
        values={
            "call": "run(scheduled(quiet_stats(win_rate(monte_carlo(PLAYOUTS), 100))))",
            "core": "100 × battle(), each with monte_carlo(PLAYOUTS) · dice(seed) · silent", "result": "100 of 100 won",
        },
    ),
    step(
        "One function, unchanged, in every stack: a script, a random bot, a recorder, a replay, a Monte Carlo bot and a "
        "hundred tasks. That is algebraic effects in Python, with doeff.",
        now=["c-draw", "c-show", "c-choose", "c-played", "c-roll"],
        marks={**OFF5, "unchanged": "is-on"},
        values={"call": "battle(), in all of these stacks", "core": "battle()", "result": "", "r4": "", "r5": ""},
    ),
]

# ---- monte-carlo: Layer 4, monte_carlo answering seed 3's second Choose (video article-monte-carlo). ----------------------

CANDIDATES = ("Bash", "Strike", "Strike", "Defend", "end turn")
TABLE_ROWS = "\n".join(
    f'          <div class="sf-line" data-anchor="row{i}"><span>{move}</span><span data-slot="e{i}"></span><span data-slot="w{i}"></span></div>'
    for i, move in enumerate(CANDIDATES)
)

MONTE_STAGE = f"""  <div class="sf-call">
    <code data-slot="call"></code>
    <span class="sf-result is-text" data-anchor="result"><span data-slot="result"></span></span>
  </div>
  <div class="sf-panels">
    <section class="sf-panel is-program" data-panel="fight">
      <div class="sf-head"><span>the fight that asked</span><span>seed 3, turn 1</span></div>
      <div class="sf-vars"><span>hp = 40</span><span>enemy = <span data-slot="enemy"></span></span><span>energy = <span data-slot="energy"></span></span><span data-anchor="f-dice">its dice: <span data-slot="dice"></span></span></div>
      <div class="sf-vars"><span>hand = <span data-slot="hand"></span></span></div>
      <div class="sf-code">
        <div class="sf-line" data-anchor="f-choose">pick = <span class="kw">yield</span> Choose(s, hand, energy)</div>
        <div class="sf-line" data-anchor="f-card">card = hand[pick]</div>
        <div class="sf-line" data-anchor="f-hit">s, dmg = hit(s, card)</div>
        <div class="sf-line" data-anchor="f-played"><span class="kw">yield</span> Played(card, dmg)</div>
      </div>
      <div class="sf-waiting" data-anchor="f-wait">Paused: the fight waits inside <code>k</code> and has not moved</div>
    </section>
    <section class="sf-panel is-handler" data-panel="handler">
      <div class="sf-head"><span>the handler: monte_carlo(3)</span><span>3 playouts per move</span></div>
      <div class="sf-inbox">
        <div class="sf-slot"><span>effect</span><div data-anchor="in-effect"></div></div>
        <div class="sf-slot"><span>k</span><div data-anchor="in-k"></div></div>
      </div>
      <div class="sf-code">
        <div class="sf-line"><span class="co"># spire.py, monte_carlo (shortened)</span></div>
        <div class="sf-line" data-anchor="h-def"><span class="kw">def</span> <span class="fn">choose</span>(effect: Choose, k):</div>
        <div class="sf-line" data-anchor="h-for">  <span class="kw">for</span> i <span class="kw">in</span> [*affordable(effect), None]:</div>
        <div class="sf-line" data-anchor="h-n">    <span class="kw">for</span> n <span class="kw">in</span> range(playouts):</div>
        <div class="sf-line" data-anchor="h-hit">      s = hit(effect.state, effect.hand[i])[0]</div>
        <div class="sf-line" data-anchor="h-run">      result, _ = run(quiet_stats(silent(</div>
        <div class="sf-line" data-anchor="h-run2">        dice(n)(random_bot(battle(s))))))</div>
        <div class="sf-line" data-anchor="h-wins">      wins += result == <span class="st">"win"</span></div>
        <div class="sf-line" data-anchor="h-best">    <span class="kw">if</span> wins / playouts &gt; best_score: best = i</div>
        <div class="sf-line" data-anchor="h-resume">  <span class="kw">return</span> (<span class="kw">yield</span> Resume(k, best))</div>
      </div>
      <div class="sf-code sf-table">
        <div class="sf-line is-head"><span>move</span><span>enemy after</span><span>wins</span></div>
{TABLE_ROWS}
      </div>
      <div class="sf-box" data-anchor="p-box">
        <div><span>a playout: </span><span class="sf-box-stack" data-anchor="p-stack">quiet_stats · silent · dice(<span data-slot="pdice"></span>) · random_bot</span></div>
        <div>battle(s) from enemy <span data-slot="pfrom"></span> → <span data-slot="pres"></span></div>
        <div class="sf-box-room" data-anchor="p-room"></div>
      </div>
    </section>
  </div>
  <div class="sf-token is-effect" data-token="choose">Choose</div>
  <div class="sf-token is-k" data-token="k">k <small>the rest of the fight</small></div>
  <div class="sf-token is-value" data-token="playout">battle(s)</div>
  <div class="sf-token is-value" data-token="answer">0: Bash</div>"""

HELD = {"f-card": "is-k", "f-hit": "is-k", "f-played": "is-k", "f-wait": "is-on"}
IN_HAND = {"choose": {"at": "in-effect"}, "k": {"at": "in-k"}}
BOX = {"p-box": "is-on"}

MONTE_STEPS = [
    step(
        "The fight is on seed 3, turn 1, after one Strike: 2 energy and Bash, Strike, Strike, Defend in hand. It pauses at "
        "<code>yield Choose</code>.",
        active="fight",
        now=["f-choose"],
        values={
            "call": "run(quiet_stats(silent(dice(3)(monte_carlo(PLAYOUTS)(battle())))))", "result": "",
            "enemy": "38", "energy": "2", "dice": "dice(3)", "hand": "[Bash, Strike, Strike, Defend]",
            **{f"e{i}": "" for i in range(5)}, **{f"w{i}": "" for i in range(5)}, "pdice": "", "pfrom": "", "pres": "",
        },
        tokens={"choose": end("f-choose")},
    ),
    step(
        "The <code>Choose</code> travels out to <code>monte_carlo</code>. The handler holds the rest of the fight as "
        "<code>k</code>, and the fight waits inside it.",
        active="handler",
        now=["h-def"],
        marks=HELD,
        tokens={"choose": {"from": end("f-choose"), "at": "in-effect"}, "k": {"from": end("f-played"), "at": "in-k", "delay": 0.2}},
    ),
    step(
        "To answer, it runs <code>battle</code> itself: the same function a person plays. It needs no model of the game.",
        active="handler",
        now=["h-run", "h-run2"],
        marks=HELD,
        tokens=IN_HAND,
    ),
    step(
        "For each card it can play, and for ending the turn, it plays that card on the state with <code>hit</code>, a plain "
        "function. Bash takes the enemy from 38 to 30.",
        active="handler",
        now=["h-for", "h-hit", "row0"],
        marks=HELD,
        values={"e0": "30"},
        tokens=IN_HAND,
    ),
    step(
        "Then it runs the rest of the fight from there, under a random bot: playout 1 of 3. It wins.",
        active="handler",
        now=["h-n", "h-run", "h-run2", "row0"],
        marks={**HELD, **BOX},
        values={"pdice": "0", "pfrom": "30", "pres": "win", "w0": "1 of 3"},
        tokens={**IN_HAND, "playout": {"from": end("h-run2"), "at": "p-room"}},
    ),
    step(
        "Each run has a stack of its own, here <code>dice(1)</code>, so it does not use up the dice of the paused fight: that "
        "fight still has <code>dice(3)</code> to itself.",
        active="handler",
        now=["p-stack", "f-dice", "row0"],
        marks={**HELD, **BOX},
        values={"pdice": "1", "w0": "2 of 3"},
        tokens={**IN_HAND, "playout": {"from": end("h-run2"), "at": "p-room"}},
    ),
    step(
        "Playout 3, on <code>dice(2)</code>, wins as well. The handler counts the wins: Bash, 3 of 3.",
        active="handler",
        now=["h-wins", "row0"],
        marks={**HELD, **BOX},
        values={"pdice": "2", "w0": "3 of 3"},
        tokens={**IN_HAND, "playout": {"from": end("h-run2"), "at": "p-room"}},
    ),
    step(
        "The next move is Strike: the enemy at 32, and the three playouts, on <code>dice(0)</code>, <code>dice(1)</code> and "
        "<code>dice(2)</code>, win all three.",
        active="handler",
        now=["h-for", "h-hit", "row1"],
        marks={**HELD, **BOX},
        values={"e1": "32", "w1": "3 of 3", "pfrom": "32"},
        tokens={**IN_HAND, "playout": {"from": end("h-run2"), "at": "p-room"}},
    ),
    step(
        "Then the other Strike, Defend and ending the turn. Here every move wins all three. The paused fight has not moved: "
        "still turn 1, enemy 38.",
        active="handler",
        now=["row2", "row3", "row4", "f-choose"],
        marks={**HELD, **BOX},
        values={"e2": "32", "w2": "3 of 3", "e3": "38", "w3": "3 of 3", "e4": "38", "w4": "3 of 3", "pfrom": "38"},
        tokens=IN_HAND,
    ),
    step(
        "With a tie it keeps the first move, Bash: <code>best</code> changes only when a move scores higher, and no move "
        "beats 3 of 3.",
        active="handler",
        now=["h-best"],
        marks={**HELD, "row0": "is-best"},
        tokens=IN_HAND,
    ),
    step(
        "It resumes <code>k</code> with that move, and the fight goes on from its <code>yield</code>: Bash hits for 8, and "
        "the enemy is at 30.",
        active="fight",
        now=["h-resume", "f-card", "f-hit"],
        marks={"row0": "is-best"},
        values={"enemy": "30", "energy": "0", "hand": "[Strike, Strike, Defend]"},
        tokens={
            "answer": {"from": end("h-resume"), **end("f-choose"), "merge": True},
            "k": {"from": "in-k", **end("f-choose"), "merge": True, "delay": 0.3},
        },
    ),
    step(
        "On seed 3 the bot answers eleven <code>Choose</code> effects this way, with a hundred and sixty-five playouts (3 for "
        "each move it could make), and wins on turn 4.",
        active="run",
        values={"result": "('win', 4)"},
    ),
]

# ---- hundred-fights: Layer 5, win_rate as a hundred tasks (video article-hundred-fights). ----------------------------------

HUNDRED_CORE = f"""      <div class="sf-core" data-anchor="core">
        <div class="sf-core-head"><span data-anchor="core-name" data-slot="core"></span><span class="sf-unchanged">battle: 0 lines changed</span></div>
        <div class="sf-vars"><span>seed = <span data-slot="seed"></span></span><span>tasks = <span data-slot="tasks"></span></span></div>
        <div class="sf-code">
          <div class="sf-line" data-anchor="w-def"><span class="kw">def</span> <span class="fn">win_rate</span>(player, fights: int):</div>
          <div class="sf-line" data-anchor="w-init">    tasks = []</div>
          <div class="sf-line" data-anchor="w-for">    <span class="kw">for</span> seed <span class="kw">in</span> range(fights):</div>
          <div class="sf-line" data-anchor="w-spawn">        tasks.append((<span class="kw">yield</span> Spawn(silent(dice(seed)(player(battle()))))))</div>
          <div class="sf-line" data-anchor="w-gather">    results = <span class="kw">yield</span> Gather(*tasks)</div>
          <div class="sf-line" data-anchor="w-return">    <span class="kw">return</span> sum(r == <span class="st">"win"</span> <span class="kw">for</span> r, _ <span class="kw">in</span> results) / fights</div>
        </div>
        <div class="sf-box" data-anchor="t-box">
          <div><span>task <span data-slot="tid"></span>: </span><span class="sf-box-stack" data-anchor="t-stack">silent · dice(<span data-slot="tseed"></span>) · <span data-slot="tplayer"></span></span><span> around battle()</span> → <span data-slot="tres"></span></div>
          <div class="sf-box-room" data-anchor="t-room"></div>
        </div>
        {tasks((0, 1, 2, 3, 47, 92))}
      </div>"""

HUNDRED_STAGE = f"""  <div class="sf-call">
    <code data-slot="call"></code>
    <span class="sf-result is-text" data-anchor="result"><span data-slot="result"></span></span>
  </div>
{rings(2, HUNDRED_CORE)}
  <div class="sf-token is-effect" data-token="spawn">Spawn</div>
  <div class="sf-token is-k" data-token="k">k <small>the rest of win_rate</small></div>
  <div class="sf-token is-effect" data-token="roll">Roll</div>
  <div class="sf-token is-effect" data-token="choose">Choose</div>
  <div class="sf-token is-effect" data-token="show">Show</div>
  <div class="sf-token is-effect" data-token="gather">Gather</div>
  <div class="sf-token is-value" data-token="results">100 results</div>
  <div class="sf-token is-effect" data-token="played">Played</div>
  <div class="sf-token is-effect" data-token="played2">Played</div>
  <div class="sf-token is-effect" data-token="played3">Played</div>"""

REST_OF_WIN_RATE = {"w-for": "is-k", "w-spawn": "is-k", "w-gather": "is-k", "w-return": "is-k"}
GRID = {"tasks": "is-on"}
TASK = {"t-box": "is-on"}
RANDOM_CALL = "run(scheduled(quiet_stats(win_rate(random_bot, 100))))"

HUNDRED_STEPS = [
    step(
        "<code>win_rate</code> is a program too. It yields <code>Spawn</code> once per seed; the first, for seed 0, travels "
        "out to <code>scheduled</code>.",
        now=["w-for", "w-spawn"],
        marks=GRID,
        values={
            "call": RANDOM_CALL, "result": "", "r1": "quiet_stats", "r2": "scheduled", "core": "win_rate(random_bot, 100)",
            "seed": "0", "tasks": "[]", "tid": "0", "tseed": "0", "tplayer": "random_bot", "tres": "",
        },
        tokens={"spawn": {"from": end("w-spawn"), **end("n2")}},
    ),
    step(
        "Each <code>Spawn</code> starts a <code>battle</code> as a task: here task 0, the fight on seed 0.",
        now=["w-spawn"],
        marks={**GRID, **TASK, "t0": "is-run"},
        tokens={"spawn": end("n2")},
    ),
    step(
        "Each task carries its own stack, so the hundred fights do not share a random generator: task 0 rolls "
        "<code>dice(0)</code>, task 1 <code>dice(1)</code>, and so on.",
        now=["t-stack"],
        marks={**GRID, **TASK, "t0": "is-run"},
        tokens={"spawn": end("n2")},
    ),
    step(
        "The tasks take turns on one thread. At the <code>Spawn</code>, <code>scheduled</code> keeps the <code>k</code> of "
        "<code>win_rate</code> and runs the new task.",
        marks={**GRID, **TASK, **REST_OF_WIN_RATE, "t0": "is-run"},
        values={"tasks": "[task 0]"},
        tokens={"k": {"from": end("w-return"), **end("n2")}},
    ),
    step(
        "The fight's <code>Roll</code>, <code>Choose</code> and <code>Show</code> are answered by the task's own stack. No "
        "effect of a fight reaches <code>scheduled</code>, so the task runs to its end: <code>('win', 4)</code>.",
        now=["t-stack"],
        marks={**GRID, **TASK, **REST_OF_WIN_RATE, "t0": "is-done"},
        values={"tres": "('win', 4)"},
        tokens={
            "k": end("n2"),
            "roll": {"from": "t-room", **end("t-stack")},
            "choose": {"from": "t-room", **end("t-stack"), "delay": 0.2},
            "show": {"from": "t-room", **end("t-stack"), "delay": 0.4},
        },
    ),
    step(
        "And then <code>win_rate</code> goes on from its <code>yield</code>. Seed 1 runs the same way to "
        "<code>('win', 7)</code>, and seed 2 to <code>('win', 9)</code>.",
        now=["w-spawn"],
        marks={**GRID, **TASK, "t0": "is-done", "t1": "is-done", "t2": "is-done"},
        values={"seed": "2", "tasks": "[task 0, task 1, task 2]", "tid": "2", "tseed": "2", "tres": "('win', 9)"},
        tokens={"k": {"from": end("n2"), **end("w-spawn"), "merge": True}},
    ),
    step(
        "So it goes for all 100 seeds: 100 <code>Spawn</code> effects and 99 switches from one task to the next, all on one "
        "thread.",
        now=["w-for", "w-spawn"],
        marks={"tasks": "is-on is-all-done"},
        values={"seed": "99", "tasks": "[100 tasks]"},
    ),
    step(
        "<code>Gather</code> waits for every task and gives back their results. Here every fight has already ended, so the "
        "results come back at once.",
        now=["w-gather"],
        marks={"tasks": "is-on is-all-done"},
        tokens={
            "gather": {"from": end("w-gather"), **end("n2")},
            "results": {"from": end("n2"), **end("w-gather"), "delay": 0.9},
        },
    ),
    step(
        "On the same hundred seeds, the random bot wins seventy-three fights.",
        now=["w-return"],
        marks={"tasks": "is-on is-scored"},
        values={"result": "0.73"},
    ),
    step(
        "Change one argument, the player, and the Monte Carlo bot wins all one hundred.",
        now=["w-return"],
        marks={"tasks": "is-on is-scored is-all-win"},
        values={
            "call": "run(scheduled(quiet_stats(win_rate(monte_carlo(PLAYOUTS), 100))))",
            "core": "win_rate(monte_carlo(PLAYOUTS), 100)", "result": "1.0",
        },
    ),
    step(
        "A task's own handlers do not take <code>Played</code>, so it travels out past the <code>Spawn</code>, and one "
        "handler hears all hundred fights: with <code>damage_stats</code> in that ring, 1,096 <code>Played</code> from the "
        "random bot's fights add up per card.",
        marks={"r1": "is-new", "tasks": "is-on is-scored"},
        values={
            "call": "run(scheduled(damage_stats(totals)(win_rate(random_bot, 100))))", "r1": "damage_stats(totals)",
            "core": "win_rate(random_bot, 100)", "result": "Strike 3663 · Defend 0 · Bash 632",
        },
        tokens={
            "played": {"from": "t3", **end("n1")},
            "played2": {"from": "t47", **end("n1"), "delay": 0.25},
            "played3": {"from": "t92", **end("n1"), "delay": 0.5},
        },
    ),
]

FIGURES_TO_WRITE = {
    "article-opening": figure(
        "The opening of docs/articles/algebraic-effects-in-python.md (the video article-opening, one step per click): battle "
        "yields its effects, a handler gets each one with k, and the stacks around battle change while battle does not.\n"
        "     Written by tools/docs_site/figures_measure/article_figures.py; the run lines and results are the article's.",
        OPENING_STAGE,
        OPENING_STEPS,
    ),
    "monte-carlo": figure(
        "Layer 4 of docs/articles/algebraic-effects-in-python.md (the video article-monte-carlo, one step per click): "
        "monte_carlo answers the second Choose of seed 3.\n"
        "     Written by tools/docs_site/figures_measure/article_figures.py from the run of "
        "tools/docs-video/src/pages/article-monte-carlo/measure/monte.py.",
        MONTE_STAGE,
        MONTE_STEPS,
    ),
    "hundred-fights": figure(
        "Layer 5 of docs/articles/algebraic-effects-in-python.md (the video article-hundred-fights, one step per click): "
        "win_rate as a hundred tasks under scheduled.\n"
        "     Written by tools/docs_site/figures_measure/article_figures.py from the run of "
        "tools/docs-video/src/pages/article-hundred-fights/measure/hundred.py.",
        HUNDRED_STAGE,
        HUNDRED_STEPS,
    ),
}

if __name__ == "__main__":
    for name, body in FIGURES_TO_WRITE.items():
        (FIGURES / f"{name}.html").write_text(body, encoding="utf-8")
        print(FIGURES / f"{name}.html", body.count("<li "), "steps")
