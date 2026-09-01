import unittest
from datetime import date

import regex_based_graph_night as poker


class CsvParsingTests(unittest.TestCase):
    def _load_event(self, path):
        with open(path) as file:
            logs = poker.fix_up_player_names(file.readlines())
        if logs[0] == "entry,at,order\n":
            logs.pop(0)
        logs.reverse()
        return poker.PokerNightEvent(poker.date_of_csv(path), logs)

    # ---- Pure parsing/formatting helpers ----

    def test_date_of_csv_extracts_date_and_errors_without_one(self):
        self.assertEqual(
            poker.date_of_csv("logs/poker_night_20220707.csv"), date(2022, 7, 7)
        )
        self.assertEqual(
            poker.date_of_csv("logs/poker_night_20220106_tourney.csv"),
            date(2022, 1, 6),
        )
        with self.assertRaises(ValueError):
            poker.date_of_csv("logs/no_date_here.csv")

    def test_normalize_csv_path_covers_all_input_forms(self):
        self.assertEqual(poker.normalize_csv_path("20220707.csv"), "20220707.csv")
        self.assertEqual(
            poker.normalize_csv_path("logs/poker_night_20220707"),
            "logs/poker_night_20220707.csv",
        )
        self.assertEqual(
            poker.normalize_csv_path("poker_night_20220707"),
            "logs/poker_night_20220707.csv",
        )
        self.assertEqual(
            poker.normalize_csv_path("20220707"), "logs/poker_night_20220707.csv"
        )

    def test_fix_up_player_names_normalizes_known_aliases_case_insensitively(self):
        lines = [
            '"""STEVO-IPAD @ abc"" folds",2022-01-01T00:00:00.000Z,100\n',
            '"The admin updated the player ""spange @ xyz"" stack from 100 to 200.",2022-01-01T00:00:00.000Z,101\n',
        ]
        fixed = poker.fix_up_player_names(lines)
        self.assertIn('"""Stephen @ abc""', fixed[0])
        self.assertIn('""Ethan @ xyz""', fixed[1])

    def test_fix_up_player_names_maps_elusive_greg_variants_to_george(self):
        for alias in ("goero", "groeegeoeg", "greg"):
            line = f'"""{alias} @ abc"" folds",2022-01-01T00:00:00.000Z,100\n'
            fixed = poker.fix_up_player_names([line])
            self.assertIn('"""George @ abc""', fixed[0], msg=f"alias {alias!r}")

    def test_fix_up_player_names_exits_on_unrecognized_player_name(self):
        lines = ['"""Xavier @ abc"" folds",2022-01-01T00:00:00.000Z,100\n']
        with self.assertRaises(SystemExit):
            poker.fix_up_player_names(lines)

    def test_splitwise_email_mapping_known_and_unknown_players(self):
        self.assertEqual(
            poker.SPLITWISE_EMAIL_BY_PLAYER["Arash"], "arashrai17@gmail.com"
        )
        self.assertEqual(
            poker.SPLITWISE_EMAIL_BY_PLAYER["George"], "georgeutsin@gmail.com"
        )
        self.assertNotIn("Bob", poker.SPLITWISE_EMAIL_BY_PLAYER)

    def test_player_movement_time_decodes_fractional_unix_timestamp(self):
        # Real "order" value from logs/poker_night_20220707.csv, which decodes to
        # 2022-07-07T22:48:17.581Z (UTC); comparing via .timestamp() keeps the
        # assertion independent of the machine's local timezone.
        movement = poker.PlayerMovement(1000, "165723409758100", poker.TYPE_JOIN)
        self.assertEqual(int(movement.time.timestamp()), 1_657_234_097)
        self.assertEqual(movement.time.microsecond, 581_000)

    def test_player_round_action_to_string_includes_amount_only_when_present(self):
        with_amount = poker.PlayerRoundAction(
            "Arash", poker.RoundAction.bets, "165723409758100", amount=190
        )
        self.assertTrue(with_amount.to_string().startswith("Arash bets 190 at "))

        without_amount = poker.PlayerRoundAction(
            "Arash", poker.RoundAction.folds, "165723409758100"
        )
        self.assertTrue(without_amount.to_string().startswith("Arash folds at "))
        self.assertNotIn("folds 0", without_amount.to_string())

    # ---- CSV parsing pipeline, exercised against real logs/ files ----

    def test_read_logs_strips_header_and_reverses_to_chronological_order(self):
        with open("logs/poker_night_20220707.csv") as file:
            logs = poker.fix_up_player_names(file.readlines())
        self.assertEqual(logs[0], "entry,at,order\n")
        logs.pop(0)
        logs.reverse()

        self.assertNotIn("entry,at,order\n", logs)
        self.assertIn("requested a seat", logs[0])

        order_of = lambda line: int(line.strip().split(",")[-1])
        for previous, current in zip(logs, logs[1:]):
            self.assertLessEqual(
                order_of(previous),
                order_of(current),
                msg="logs must be reordered oldest-first",
            )

    def test_poker_night_event_splits_rounds_with_sequential_hand_numbers(self):
        event = self._load_event("logs/poker_night_20220707.csv")

        self.assertEqual(len(event.rounds), 87)
        self.assertEqual(event.rounds[0].round_number, 1)
        self.assertEqual(event.rounds[-1].round_number, 87)
        for previous, current in zip(event.rounds, event.rounds[1:]):
            self.assertEqual(current.round_number, previous.round_number + 1)

    def test_admin_stack_adjustment_is_backed_out_of_player_profit(self):
        # logs/poker_night_20210520.csv records: "The admin updated the player
        # ""spange @ ..."" stack from 173 to 1173." (spange normalizes to Ethan).
        event = self._load_event("logs/poker_night_20210520.csv")

        adjustment_round = next(
            r for r in event.rounds if "Ethan" in r.admin_adjustments
        )
        self.assertEqual(adjustment_round.admin_adjustments["Ethan"], 1000)

        history = event.player_stack_history()
        checksum = sum(entries[-1][0] for entries in history.values())
        self.assertEqual(
            checksum,
            0,
            "an admin adjustment that isn't backed out of profit breaks the zero-sum invariant",
        )

    def test_rebuy_is_distinguished_from_initial_buy_in(self):
        # logs/poker_night_20260722.csv: georgoergo (-> George) joins once and
        # rebuys twice ("rebought. New stack 1000.").
        event = self._load_event("logs/poker_night_20260722.csv")

        george_joins = []
        for round in event.rounds:
            george_joins.extend(round.player_game_joins.get("George", []))

        self.assertEqual(len(george_joins), 3, "one initial buy-in plus two rebuys")
        self.assertEqual(george_joins[0].movement_type, poker.TYPE_JOIN)
        self.assertTrue(
            all(j.movement_type == poker.TYPE_REBUY for j in george_joins[1:])
        )
        self.assertTrue(all(j.amount == 1000 for j in george_joins))

    def test_exit_records_zero_stack_quits_correctly(self):
        event = self._load_event("logs/poker_night_20220707.csv")

        round26 = next(r for r in event.rounds if r.round_number == 26)
        exit_movement = round26.players_exited["Jonah"]
        self.assertEqual(exit_movement.amount, 0)
        self.assertEqual(exit_movement.movement_type, poker.TYPE_EXIT)

    def test_winner_without_shown_hand_still_records_win_with_empty_hand(self):
        event = self._load_event("logs/poker_night_20220707.csv")

        round8 = next(r for r in event.rounds if r.round_number == 8)
        self.assertEqual(round8.winning_players, ["Jonah"])
        self.assertEqual(round8.winning_amounts, [340])
        self.assertEqual(
            round8.winning_hands,
            [],
            "winning without a showdown should not fabricate a hand",
        )

    def test_flop_turn_river_cards_parsed_in_chronological_order(self):
        event = self._load_event("logs/poker_night_20220707.csv")

        round1 = next(r for r in event.rounds if r.round_number == 1)
        self.assertEqual(len(round1.table_cards), 5, "flop (3) + turn (1) + river (1)")
        self.assertTrue(round1.start_time <= round1.flop_time)
        self.assertTrue(round1.flop_time < round1.turn_time)
        self.assertTrue(round1.turn_time < round1.river_time)
        self.assertTrue(round1.river_time <= round1.end_time)

    def test_round_action_time_buckets_partition_all_player_actions(self):
        event = self._load_event("logs/poker_night_20220707.csv")

        round1 = next(r for r in event.rounds if r.round_number == 1)
        pre_flop = len(round1.pre_flop_actions())
        pre_turn = len(round1.pre_turn_actions())
        pre_river = len(round1.pre_river_actions())
        post_river = len(round1.post_river_actions())

        self.assertEqual((pre_flop, pre_turn, pre_river, post_river), (8, 3, 5, 3))
        self.assertEqual(
            pre_flop + pre_turn + pre_river + post_river,
            len(round1.player_actions),
            "every action must fall into exactly one street bucket",
        )

    def test_player_stack_history_checksum_is_zero_across_varied_logs(self):
        # Covers a regular night with folds/exits, a night with an admin
        # adjustment, a night with rebuys, and a tournament-format night.
        files = [
            "logs/poker_night_20220707.csv",
            "logs/poker_night_20210520.csv",
            "logs/poker_night_20260722.csv",
            "logs/poker_night_20260715.csv",
            "logs/poker_night_20220106_tourney.csv",
        ]

        for path in files:
            with self.subTest(path=path):
                event = self._load_event(path)
                history = event.player_stack_history()
                checksum = sum(entries[-1][0] for entries in history.values())
                self.assertEqual(
                    checksum, 0, f"{path} should have zero-sum player profits"
                )

    def test_tournament_format_parses_expected_round_count(self):
        event = self._load_event("logs/poker_night_20220106_tourney.csv")
        self.assertEqual(len(event.rounds), 57)

    def test_most_wins_and_biggest_win_identify_correct_round(self):
        event = self._load_event("logs/poker_night_20220707.csv")

        winner, winning_rounds, _all_wins = poker.most_wins(event.rounds)
        self.assertEqual(winner, "Prilik")
        self.assertEqual(len(winning_rounds), 20)

        biggest = poker.biggest_win(event.rounds)
        self.assertEqual(biggest.round_number, 43)
        self.assertEqual(biggest.winning_players, ["Spencer"])
        self.assertEqual(biggest.winning_amounts, [2120])

    def test_gentleman_scores_count_hidden_hand_shown_after_win(self):
        event = self._load_event("logs/poker_night_20220707.csv")

        scores = poker.gentleman_scores_by_player(event.rounds)
        self.assertEqual(scores["George"], 3)
        self.assertEqual(scores["Stephen"], 3)
        self.assertEqual(scores["Spencer"], 2)


if __name__ == "__main__":
    unittest.main()
