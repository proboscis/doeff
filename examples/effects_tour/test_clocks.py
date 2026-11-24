# test_clocks.py
from doeff import Resume, Spawn, do, handler, run
from doeff_core_effects import scheduled
from doeff_time import GetTime, sim_time_handler

from clocks import START, Draw, EndTurn, battle, player, poison, rising, turns


def test_a_poison_deals_3_at_the_end_of_each_of_5_turns():
    @do
    def seven_turns():
        yield Spawn(poison(3, turns=5), daemon=True)
        dealt = ()
        for _ in range(7):
            dealt = (*dealt, (yield EndTurn()))
        return dealt

    assert run(scheduled(turns()(seven_turns()))) == (3, 3, 3, 3, 3, 0, 0)


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


def test_the_poison_and_four_strikes_bring_30_hit_points_down_on_turn_4():
    @handler
    def unseen(effect: Draw, k):
        return (yield Resume(k, None))

    fight = player(["Poison", "Strike", "Strike", "Strike"])(turns()(battle()))
    assert run(scheduled(sim_time_handler(start_time=START)(unseen(fight)))) == 4
