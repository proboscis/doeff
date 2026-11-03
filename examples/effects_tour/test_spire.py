# test_spire.py
from doeff import Resume, handler, run

from spire import Fight, Played, battle, dice, quiet_stats, scripted, silent


def test_a_strike_finishes_an_enemy_on_6_hit_points():
    # seed 7 deals Defend Strike Defend Defend Strike, and the script plays the Strike
    fight = battle(Fight(enemy_hp=6))
    assert run(quiet_stats(silent(dice(7)(scripted([1])(fight))))) == ("win", 1)


def test_bash_makes_the_next_strikes_hit_harder():
    played = []

    @handler
    def log(effect: Played, k):
        played.append((effect.card, effect.damage))
        return (yield Resume(k, None))

    run(log(silent(dice(7)(scripted([0, 0, 0, None] * 20)(battle())))))
    assert played[-3:] == [("Bash", 8), ("Strike", 9), ("Strike", 9)]
