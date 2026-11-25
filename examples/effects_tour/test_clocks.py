# test_clocks.py
from doeff import Resume, do, handler, run
from doeff_core_effects import scheduled
from doeff_time import GetTime, sim_time_handler

from clocks import START, Draw, in_real_time, rising
from spire import EndTurn, Lasting, battle, dice, poison, quiet_stats, scripted, silent, turns

MOVES = [1, 1, 0, 0, 0, 0, 0, None, 0, 0, 0]


def test_a_poison_deals_3_at_the_end_of_each_of_3_turns_with_no_scheduler():
    @do
    def five_turns():
        yield Lasting(poison(3, turns=3))
        dealt = ()
        for _ in range(5):
            dealt = (*dealt, (yield EndTurn()))
        return dealt

    assert run(turns()(five_turns())) == (3, 3, 3, 0, 0)


def test_an_animation_draws_a_frame_every_tenth_of_a_second_of_virtual_time():
    frames = ()

    @handler
    def drawn(effect: Draw, k):
        nonlocal frames
        now = yield GetTime()
        frames = (*frames, ((now - START).total_seconds(), effect.text, effect.height))
        return (yield Resume(k, None))

    run(scheduled(sim_time_handler(start_time=START)(drawn(rising("-6")))))
    assert frames == ((0.0, "-6", 0), (0.1, "-6", 1), (0.2, "-6", 2), (0.3, "-6", 3))


def test_the_fight_in_real_time_ends_as_the_same_fight_with_no_screen(capsys):
    plain = run(quiet_stats(silent(dice(7)(scripted(MOVES)(turns()(battle()))))))
    assert in_real_time(MOVES) == plain == ("win", 5)
    assert "poison deals 3" in capsys.readouterr().out
