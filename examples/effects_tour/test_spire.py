# test_spire.py
from doeff import Resume, handler, run

from spire import Fight, Played, battle, dice, quiet_stats, scripted, silent, turns


def played_in(fight):
    """Run a silent fight on seed 7 and return the (card, damage) of every Played, in order."""
    played = []

    @handler
    def log(effect: Played, k):
        played.append((effect.card, effect.damage))
        return (yield Resume(k, None))

    run(log(silent(dice(7)(fight))))
    return played


def test_a_strike_finishes_an_enemy_on_6_hit_points():
    # seed 7 deals Defend Strike Bash Defend Strike, and the script plays the Strike
    fight = scripted([1])(turns()(battle(Fight(enemy_hp=6))))
    assert run(quiet_stats(silent(dice(7)(fight)))) == ("win", 1)


def test_bash_makes_the_next_strike_hit_harder():
    played = played_in(scripted([2, 1])(turns()(battle())))
    assert played[:2] == [("Bash", 8), ("Strike", 9)]


def test_a_poison_deals_3_at_the_end_of_each_of_the_next_3_turns():
    # the script plays the Poison on turn 5; it is still dealing on turn 7, and that wins the fight
    played = played_in(scripted([0, 0, 0, None] * 20)(turns()(battle())))
    assert [damage for card, damage in played if card == "Poison"] == [0, 3, 3, 3]
    assert played[-1] == ("Poison", 3)
