# test_clocks.py
from doeff import Resume, do, handler, run
from doeff_core_effects import scheduled
from doeff_time import GetTime, sim_time_handler

from clocks import START, Draw, Lasting, TurnEnds, battle, player, poison, rising, turns


def test_a_poison_deals_3_at_the_end_of_each_of_5_turns():
    @do
    def seven_turns():
        yield Lasting(poison(3, turns=5))
        dealt = ()
        for _ in range(7):
            dealt = (*dealt, (yield TurnEnds()))
        return dealt

    assert run(turns()(seven_turns())) == (3, 3, 3, 3, 3, 0, 0)


def test_an_animation_draws_a_frame_every_tenth_of_a_second_of_virtual_time():
    frames = []

    @handler
    def drawn(effect: Draw, k):
        now = yield GetTime()
        frames.append(((now - START).total_seconds(), effect.text.strip()))
        return (yield Resume(k, None))

    run(scheduled(sim_time_handler(start_time=START)(drawn(rising("-6")))))
    assert frames == [(0.0, "-6"), (0.1, "-6^"), (0.2, "-6^^"), (0.3, "-6^^^")]


def test_the_poison_and_four_strikes_bring_30_hit_points_down_on_turn_4():
    @handler
    def unseen(effect: Draw, k):
        return (yield Resume(k, None))

    fight = player(["Poison", "Strike", "Strike", "Strike"])(turns()(battle()))
    assert run(scheduled(sim_time_handler(start_time=START)(unseen(fight)))) == 4
