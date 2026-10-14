import io
import json
import re
import unittest
from contextlib import redirect_stdout
from datetime import date

import pokerlater_log as pl
import regex_based_graph_night as poker

FIXTURE = "tests/fixtures/poker_night_20261021_pokerlater.json"


def card(text):
    rank, suit = text[:-1], text[-1]
    ranks = {"J": 11, "Q": 12, "K": 13, "A": 14}
    suits = {"c": 0, "d": 1, "h": 2, "s": 3}
    return {"rank": ranks.get(rank) or int(rank), "suit": suits[suit]}


def cards(*texts):
    return [card(text) for text in texts]


class PokerLaterLogTests(unittest.TestCase):
    def setUp(self):
        with open(FIXTURE) as file:
            self.export = json.load(file)

    def test_paths_and_dates_accept_pokerlater_files(self):
        self.assertEqual(
            poker.date_of_csv("pokerlater-2026-10-21-V1StGXR8_Z5jdHi6B-log.json"),
            date(2026, 10, 21),
        )
        self.assertEqual(poker.date_of_csv(FIXTURE), date(2026, 10, 21))
        self.assertEqual(poker.normalize_csv_path(FIXTURE), FIXTURE)

    def test_lines_match_the_golden_file_shared_with_rust(self):
        # src/main.rs checks the Rust converter against the same file, so the
        # two converters cannot drift apart.
        with open("tests/fixtures/poker_night_20261021_pokerlater.lines.csv") as file:
            golden = file.read()
        lines = pl.pokerlater_log_lines(self.export, poker.normalize_player_name)
        self.assertEqual("\n".join(lines), golden)

    def test_rejects_a_file_that_is_not_a_pokerlater_log(self):
        with self.assertRaises(ValueError):
            pl.load_export(io.StringIO(json.dumps({"format": "something-else"})))

    def test_every_completed_hand_becomes_a_round(self):
        event = poker.load_event(FIXTURE)
        complete = [h for h in self.export["hands"] if h["final"]["phase"] == "complete"]
        self.assertEqual(
            [r.round_number for r in event.rounds],
            [h["handNo"] for h in complete],
        )

    def test_stacks_at_each_hand_start_match_the_export(self):
        event = poker.load_event(FIXTURE)
        for poker_round, hand in zip(event.rounds, self.export["hands"]):
            self.assertEqual(
                poker_round.player_balances,
                {s["name"]: s["startingStack"] for s in hand["seats"]},
            )

    def test_inferred_buy_ins_equal_the_ledger(self):
        # Buy-ins and rebuys are not events in the export; the converter infers
        # them from stacks. The ledger's boughtIn is the independent answer.
        added = {}
        for line in pl.pokerlater_log_lines(self.export):
            match = re.search(
                r'The player ""(.*?) @ \S+ (?:joined the game with a stack of|rebought\. New stack) (\d+)',
                line,
            )
            if match:
                added[match[1]] = added.get(match[1], 0) + int(match[2])
        self.assertEqual(
            added,
            {line["name"]: line["boughtIn"] for line in self.export["ledger"]["lines"] if line["boughtIn"]},
        )

    def test_profit_history_is_in_cents_and_ends_on_the_ledger(self):
        event = poker.load_event(FIXTURE)
        history = event.player_stack_history()
        finals = {player: points[-1][0] for player, points in history.items()}
        self.assertEqual(finals, {"Stephen": -750, "Arash": 1300, "Spencer": -550})
        self.assertEqual(sum(finals.values()), 0)
        # Hand 2: Stephen started on 980 of a 1,000 buy-in, -20 chips = -5 cents
        # at the fixture's 100 chips = 25 cents.
        self.assertEqual(history["Stephen"][1][0], -5)

    def test_without_a_chip_value_one_chip_is_one_cent(self):
        self.export["chipRate"] = None
        for line in self.export["ledger"]["lines"]:
            line["netCents"] = None
        self.assertEqual(pl.cents_per_chip(self.export), 1.0)
        self.assertEqual(
            pl.pokerlater_final_profits(self.export),
            {"Stephen": -3000, "Arash": 5200, "Spencer": -2200},
        )

    def test_merged_ledger_names_play_as_the_line_they_merged_into(self):
        hand = self.export["hands"][0]
        hand["seats"][2]["name"] = "George"  # the fixture merges George into Spencer
        lines = pl.pokerlater_log_lines(self.export)
        self.assertFalse(any('George @' in line for line in lines))

    def test_timeouts_and_force_folds_add_no_line_of_their_own(self):
        lines = pl.pokerlater_log_lines(self.export)
        timeouts = sum(
            1 for h in self.export["hands"] for e in h["final"]["entries"] if e["kind"] == "timeout"
        )
        folds = sum(
            1 for h in self.export["hands"] for e in h["final"]["entries"] if e["kind"] == "fold"
        )
        self.assertGreater(timeouts, 0)
        self.assertEqual(sum(1 for line in lines if line.split(",")[0].endswith(' folds"')), folds)

    def test_a_shared_millisecond_keeps_actions_before_the_flop(self):
        # An all-in run-out deals the board in the same millisecond as the call.
        event = poker.load_event(FIXTURE)
        for poker_round in event.rounds:
            if hasattr(poker_round, "flop_time"):
                for action in poker_round.pre_flop_actions():
                    self.assertLess(action.time, poker_round.flop_time)

    def test_only_contested_pots_name_the_winning_hand(self):
        lines = pl.pokerlater_log_lines(self.export)
        with_hand = [line for line in lines if "from pot with " in line]
        self.assertTrue(with_hand)
        for line in with_hand:
            self.assertRegex(line, r"from pot with .+ \(combination: [^)]+\)")

    def test_stats_and_splitwise_text_run_on_a_pokerlater_night(self):
        event = poker.load_event(FIXTURE)
        out = io.StringIO()
        with redirect_stdout(out):
            poker.print_core_stats(event.rounds)
            poker.print_splitwise_instructions(event.player_stack_history())
        text = out.getvalue()
        self.assertIn("Winning Hands of Hands Played", text)
        self.assertIn("For the total, enter 13.0", text)
        self.assertNotIn("Uh oh!", text)


class HandDescriptionTests(unittest.TestCase):
    def test_categories(self):
        cases = [
            (cards("As", "Kd", "9c", "7h", "4s", "3d", "2c"), "A High"),
            (cards("Ks", "Kd", "9c", "7h", "4s", "3d", "2c"), "Pair"),
            (cards("Ks", "Kd", "9c", "9h", "4s", "3d", "2c"), "Two Pair"),
            (cards("Ks", "Kd", "Kc", "9h", "4s", "3d", "2c"), "Three of a Kind"),
            (cards("As", "2d", "3c", "4h", "5s", "Kd", "Kc"), "Straight"),
            (cards("As", "Ks", "9s", "7s", "4s", "3d", "2c"), "Flush"),
            (cards("Ks", "Kd", "Kc", "9h", "9s", "3d", "2c"), "Full House"),
            (cards("Ks", "Kd", "Kc", "Kh", "9s", "3d", "2c"), "Four of a Kind"),
            (cards("9s", "8s", "7s", "6s", "5s", "Kd", "Kc"), "Straight Flush"),
            (cards("As", "Ks", "Qs", "Js", "10s", "Kd", "Kc"), "Royal Flush"),
        ]
        for seven, name in cases:
            with self.subTest(name=name):
                self.assertTrue(pl.hand_description(seven).startswith(f"{name} (combination: "))

    def test_the_best_five_are_chosen(self):
        # A flush beats the straight also on the board; the wheel is 5-high.
        self.assertEqual(
            pl.hand_description(cards("Ah", "2h", "3h", "4c", "5d", "9h", "Jh")),
            "Flush (combination: A♥, J♥, 9♥, 3♥, 2♥)",
        )
        self.assertEqual(pl.best_hand(cards("As", "2d", "3c", "4h", "5s", "9d", "Jc"))[1], [5])

    def test_the_hand_types_parse_the_way_the_stats_expect(self):
        # winning_hand_types_by_player splits on "(combination:" then ","
        prefix = pl.hand_description(cards("As", "Kd", "9c", "7h", "4s", "3d", "2c")).split("(combination:")[0]
        self.assertIn("High", prefix.split(",")[0])


if __name__ == "__main__":
    unittest.main()
