import csv, os
import matplotlib.pyplot as plt
import pandas as pd
from datetime import datetime
from matplotlib.dates import DateFormatter
import argparse
import re
from pprint import pprint
from collections import defaultdict
from enum import Enum
from functools import reduce

CSV_FILE = "logs/poker_night_20220707.csv"
START_HAND_REGEX = re.compile('\"-- starting hand \#(\d+).*,(\d+)')
ADMIN_ADJUSTMENT_REGEX = re.compile('"The admin updated the player ""(.*?) @ \S+ stack from (\d+) to (\d+)')
BUY_IN_REGEX = re.compile('"The player ""(.*?) @ .* joined the game with a stack of (\d+).",[^,]+,(\d+)')
SIT_DOWN_REGEX = re.compile('"The player ""(.*?) @ \S+ sit back with the stack of (\d+).",[^,]+,(\d+)')
EXIT_REGEX = re.compile('"The player ""(.*?) @ \S+ quits the game with a stack of (\d+).",[^,]+,(\d+)')
STANDUP_REGEX = re.compile('"The player ""(.*?) @ \S+ stand up with the stack of (\d+).",[^,]+,(\d+)')
PLAYER_HAND_REGEX = re.compile('"""(.*?) @ \S+ shows a ([^.]+)')
PLAYER_STACK_REGEX = re.compile('#\d+ ""(.*?) @ [^\""]+"" \((\d+)\)')
PLAYER_WINNER_WITH_HAND_REGEX = re.compile('"""(.*?) @ \S+ collected (\d+) from pot with (.*?)"')
PLAYER_WINNER_WITHOUT_HAND_REGEX = re.compile('"""(.*?) @ \S+ collected (\d+) from pot')
FLOP_CARDS_REGEX = re.compile('"Flop:  \[(.*?)]",[^,]+,(\d+)') # why does "Flop:  " have two spaces smh
TURN_CARD_REGEX = re.compile('"Turn: .* \[(.*?)]",[^,]+,(\d+)')
RIVER_CARD_REGEX = re.compile('"River: .* \[(.*?)]",[^,]+,(\d+)')
UNDEALT_CARDS_REGEX = re.compile('"Undealt cards: .* \[(.*?)]')
PLAYER_NAME_REGEX = re.compile('[\"]{2,3}(\S+) @ ') # can have two or three " before name
PLAYER_INITIAL_POST_REGEX = re.compile('"""(.*?) @ \S+"" posts a (straddle|big blind|small blind) of (\d+)",[^,]+,(\d+)')
PLAYER_FOLDS_REGEX = re.compile('"""(.*?) @ \S+"" folds",[^,]+,(\d+)')
PLAYER_CALLS_REGEX = re.compile('"""(.*?) @ \S+"" calls (\d+)(?: and go all in)?",[^,]+,(\d+)')
PLAYER_CHECKS_REGEX = re.compile('"""(.*?) @ \S+"" checks",[^,]+,(\d+)')
PLAYER_BETS_REGEX = re.compile('"""(.*?) @ \S+"" bets (\d+)(?: and go all in)?",[^,]+,(\d+)')
PLAYER_RAISES_REGEX = re.compile('"""(.*?) @ \S+"" raises to (\d+)(?: and go all in)?",[^,]+,(\d+)')

# If we get new players need to add them here
KNOWN_NAME_FIX_UPS = {
        "peelic": "Prilik",
        "arashh": "Arash",
        "ash": "Arash", #?
        "arash": "Arash",
        "susan": "Arash", #?
        "annie": "Annie",
        "spenner": "Spencer",
        "spenny": "Spencer",
        "spenny2": "Spencer",
        "speny": "Spencer",
        "spange": "Ethan",
        "biz": "Alex",
        "guest": "George",
        "stevo-ipad": "Stephen",
        "stevo": "Stephen",
        "stephen": "Stephen",
        "david": "David",
        "daveed": "David",
        "daveeed": "David",
        "daveeeed": "David",
        "eveeeda": "David",
        "jerms": "James",
        "jems": "James",
        "gems": "James",
        "james": "James",
        "josh": "Josh",
        "jonah": "Jonah",
        "Jonah Dlin": "Jonah",
        "max": "Max",
        "sam": "Sam",
        "daveeeeeed": "David",
        "daveeeeeeed": "David",
        "dave": "David",
        "dave-eed": "David",
        "daveod" : "David",
        "stevo-tesla": "Stephen",
}

class RoundAction(Enum):
    posts_small_blind = "small blind"
    posts_big_blind = "big blind"
    posts_straddle = "straddle"
    bets = "bets"
    folds = "folds"
    raises = "raises"
    checks = "checks"
    calls = "calls"

class PlayerRoundAction():
    def __init__(self, player, round_action, unix_time, amount=0):
        self.player = player
        self.amount = int(amount)
        self.action_type = round_action
        self.time = datetime.fromtimestamp(int(unix_time) / 100000)

    def to_string(self):
        if self.amount > 0:
            return f'{self.player} {self.action_type.value} {self.amount} at {self.time}'
        return f'{self.player} {self.action_type.value} at {self.time}'

TYPE_STAND = 1
TYPE_SIT = 2
TYPE_JOIN = 3
TYPE_EXIT = 4

class PlayerMovement():
    def __init__(self, amount, unix_time, movement_type):
        self.amount = int(amount)
        self.movement_type = movement_type
        self.time = datetime.fromtimestamp(int(unix_time) / 100000)

class PokerNightEvent():
    def __init__(self, date, event_logs):
        self.date = date

        round_logs = []
        log_idx = 0
        while (log_idx < len(event_logs)):
            row = event_logs[log_idx]
            if re.match(START_HAND_REGEX, row):
                start_idx = log_idx
                end_idx = start_idx + 1
                # loop until we get to the next hand
                while (end_idx < len(event_logs) and not re.match(START_HAND_REGEX, event_logs[end_idx])):
                    end_idx += 1
                round_logs.append(event_logs[start_idx:end_idx])
                log_idx = end_idx - 1
            log_idx += 1

        self.rounds = []
        for round_log in round_logs:
            poker_round = PokerRound(round_log)
            self.rounds.append(poker_round)

    def player_stack_history(self):
        player_to_stack_history = {} # player_name to array of tuples of (amount, hand_time)
        player_buyin_amount = {}
        player_sitting_at_table = {}
        player_adjustments = {}
        player_exit = {}
        for poker_round in self.rounds:
            balances, stand_ups, sit_downs, joins, exits, adjustments = poker_round.player_balances, poker_round.players_stood_up, poker_round.players_sat_down, poker_round.player_game_joins, poker_round.players_exited, poker_round.admin_adjustments

            # some buyins occur before the Player Stacks line in a round, some come after the end. Can have multiple joins per
            for player, joins_array in joins.items():
                for join in joins_array:
                    if join.time < poker_round.start_time:
                        player_buyin_amount[player] = join.amount + player_buyin_amount.get(player, 0)
                        player_sitting_at_table[player] = True
                        player_exit.pop(player, None)

            # check for exits from last round, make sure we record their final balance
            for player, exit in player_exit.items():
                # check they haven't come back at the start of the round
                if not player_sitting_at_table.get(player, False):
                    exited_profit = exit.amount - player_buyin_amount[player]
                    existing_player_stack_history = player_to_stack_history.get(player, [])
                    existing_player_stack_history.append(
                        (exited_profit, poker_round.start_time)
                    )
                    player_to_stack_history[player] = existing_player_stack_history

            # CORE PROFIT CALCULATION
            for player, balance in balances.items():
                adjusted_balance = balance - player_adjustments.get(player, 0)

                profit = adjusted_balance - player_buyin_amount[player]
                existing_player_stack_history = player_to_stack_history.get(player, [])
                existing_player_stack_history.append(
                    (profit, poker_round.start_time)
                )
                player_to_stack_history[player] = existing_player_stack_history

            # sit downs occur during the round
            for player, sit_down in sit_downs.items():
                player_sitting_at_table[player] = True
                player_exit.pop(player, None)

            # add in buyins that occurred after the end of the round
            for player, joins_array in joins.items():
                for join in joins_array:
                    # Check that the player didn't just sit down as we don't want to double count thier money in play
                    if join.time >= poker_round.start_time and not player_sitting_at_table.get(player, False):
                        player_sitting_at_table[player] = True
                        player_buyin_amount[player] = join.amount + player_buyin_amount.get(player, 0)
                        player_exit.pop(player, None)



            # stand ups occurr after the end of the round
            for player, stand_up in stand_ups.items():
                player_sitting_at_table[player] = False


            # exits always occur after the end of the round
            for player, exit in exits.items():
                player_sitting_at_table[player] = False
                player_exit[player] = exit

            # adjustments are only recorded "for the next hand"
            for player, amount in adjustments.items():
                player_adjustments[player] = player_adjustments.get(player, 0) + amount

#            print("ROUND:", poker_round.round_number, "\nSTACK:", player_to_stack_history, "\nBUY INS:", player_buyin_amount)

        return player_to_stack_history

class PokerRound(): # multiple rounds in a poker night event
    def __init__(self, round_logs):
        self.metadata_extraction(round_logs)
        self.starting_balances_extraction(round_logs)
        self.extract_table_cards(round_logs)
        self.extract_winner_info(round_logs)
        self.extract_hands(round_logs)
        self.extract_admin_balance_adjustments(round_logs)
        self.extract_player_movements(round_logs)
        self.extract_player_actions_during_round(round_logs)

    def metadata_extraction(self, round_logs):
        start_log = round_logs[0]
        round_number_str, _ = re.findall(START_HAND_REGEX, start_log)[0]
        self.round_number = int(round_number_str)
        end_unix_time = round_logs[-1].split(",")[-1]
        self.end_time = datetime.fromtimestamp(int(end_unix_time) / 100000)

    def extract_admin_balance_adjustments(self, round_logs):
        self.admin_adjustments = {}
        for row in round_logs:
            if re.match(ADMIN_ADJUSTMENT_REGEX, row):
                player, from_balance, to_balance = re.findall(ADMIN_ADJUSTMENT_REGEX, row)[0]
                self.admin_adjustments[player] = int(to_balance) - int(from_balance)

    def extract_player_movements(self, round_logs):
        self.players_exited = {} # player name to PlayerMovement
        self.players_stood_up = {}
        self.players_sat_down = {}
        self.player_game_joins = {} # player name to array of tuples of PlayerMovement. Can be more than one join! (i.e. round one, but standing, then sit down during round 1 => two joined logs, see 2022-01-13).
        for row in round_logs:
            if re.match(EXIT_REGEX, row): # lose all money
                player, amount, unix_time = re.findall(EXIT_REGEX, row)[0]
                exited = PlayerMovement(amount, unix_time, TYPE_EXIT)
                self.players_exited[player] = exited
            elif re.match(STANDUP_REGEX, row): # stand up
                player, amount, unix_time = re.findall(STANDUP_REGEX, row)[0]
                stood_up = PlayerMovement(amount, unix_time, TYPE_STAND)
                self.players_stood_up[player] = stood_up
            elif re.match(BUY_IN_REGEX, row): # buy in, can be multiple in round_logs
                player, amount, unix_time = re.findall(BUY_IN_REGEX, row)[0]
                buy_in = PlayerMovement(amount, unix_time, TYPE_JOIN)
                existing_joins = self.player_game_joins.get(player, [])
                existing_joins.append(buy_in)
                self.player_game_joins[player] = existing_joins
            elif re.match(SIT_DOWN_REGEX, row): # sit down
                player, amount, unix_time = re.findall(SIT_DOWN_REGEX, row)[0]
                sit_down = PlayerMovement(amount, unix_time, TYPE_SIT)
                self.players_sat_down[player] = sit_down


    def starting_balances_extraction(self, round_logs):
        self.player_balances = {}
        for log in round_logs:
            if log.startswith("\"Player stacks"):
                for player, stack in re.findall(PLAYER_STACK_REGEX, log):
                    self.player_balances[player] = int(stack)
                # use the timestamp from the balances line instead of the "starting hand" line
                # because folks join the game in the beginning after the initial "starting hand" line smh
                unix_time = log.split(",")[-1]
                self.start_time = datetime.fromtimestamp(int(unix_time) / 100000)
                break

    def extract_hands(self, round_logs):
        self.player_to_hand = {}
        for row in round_logs:
            if re.match(PLAYER_HAND_REGEX, row):
                player, hand = re.findall(PLAYER_HAND_REGEX, row)[0]
                self.player_to_hand[player] = hand

    def extract_winner_info(self, round_logs):
        self.winning_amounts = []
        self.winning_players = []
        self.winning_hands = []
        for row in round_logs:
            if re.match(PLAYER_WINNER_WITH_HAND_REGEX, row):
                winning_info = re.findall(PLAYER_WINNER_WITH_HAND_REGEX, row)[0]
                self.winning_players.append(winning_info[0])
                self.winning_amounts.append(int(winning_info[1]))
                self.winning_hands.append(winning_info[2])
            elif re.match(PLAYER_WINNER_WITHOUT_HAND_REGEX, row):
                # The winning hand is optional, as folks can win if everyone folds without showing their hand
                winning_info = re.findall(PLAYER_WINNER_WITHOUT_HAND_REGEX, row)[0]
                self.winning_players.append(winning_info[0])
                self.winning_amounts.append(int(winning_info[1]))

    def extract_table_cards(self, round_logs):
        for row in round_logs:
            if re.match(FLOP_CARDS_REGEX, row):
                table_cards, unix_time = re.findall(FLOP_CARDS_REGEX, row)[0]
                self.table_cards = table_cards.split(", ")
                self.flop_time = datetime.fromtimestamp(int(unix_time) / 100000)
            elif re.match(UNDEALT_CARDS_REGEX, row):
                self.undealt_cards = re.findall(UNDEALT_CARDS_REGEX, row)[0].split(", ")
            elif re.match(TURN_CARD_REGEX, row):
                new_card, unix_time = re.findall(TURN_CARD_REGEX, row)[0]
                self.table_cards.append(new_card)
                self.turn_time = datetime.fromtimestamp(int(unix_time) / 100000)
            elif re.match(RIVER_CARD_REGEX, row):
                new_card, unix_time = re.findall(RIVER_CARD_REGEX, row)[0]
                self.table_cards.append(new_card)
                self.river_time = datetime.fromtimestamp(int(unix_time) / 100000)

    def extract_player_actions_during_round(self, round_logs):
        player_actions = [] # list of PlayerRoundActions
        for row in round_logs:
            round_action = None
            if re.match(PLAYER_INITIAL_POST_REGEX, row):
                player, action_type, amount, unix_time = re.findall(PLAYER_INITIAL_POST_REGEX, row)[0]
                round_action = PlayerRoundAction(player, RoundAction(action_type), unix_time, amount)
            elif re.match(PLAYER_FOLDS_REGEX, row):
                player, unix_time = re.findall(PLAYER_FOLDS_REGEX, row)[0]
                round_action = PlayerRoundAction(player, RoundAction.folds, unix_time)
            elif re.match(PLAYER_CHECKS_REGEX, row):
                player, unix_time = re.findall(PLAYER_CHECKS_REGEX, row)[0]
                round_action = PlayerRoundAction(player, RoundAction.checks, unix_time)
            elif re.match(PLAYER_CALLS_REGEX, row):
                player, amount, unix_time = re.findall(PLAYER_CALLS_REGEX, row)[0]
                round_action = PlayerRoundAction(player, RoundAction.calls, unix_time, amount)
            elif re.match(PLAYER_BETS_REGEX, row):
                player, amount, unix_time = re.findall(PLAYER_BETS_REGEX, row)[0]
                round_action = PlayerRoundAction(player, RoundAction.bets, unix_time, amount)
            elif re.match(PLAYER_RAISES_REGEX, row):
                player, amount, unix_time = re.findall(PLAYER_RAISES_REGEX, row)[0]
                round_action = PlayerRoundAction(player, RoundAction.raises, unix_time, amount)

            if round_action:
                is_all_in = "go all in" in row
                round_action.all_in = is_all_in
                player_actions.append(round_action)

        self.player_actions = player_actions

    def pre_flop_actions(self):
        if not hasattr(self, 'flop_time'): # sometimes rounds don't go to a flop
            return self.player_actions

        return list(filter(lambda action: action.time < self.flop_time, self.player_actions))

    def pre_turn_actions(self):
        if not hasattr(self, 'flop_time'):
            return []

        # if the round ends after the flop, but before the turn, get all the actions after flop
        if not hasattr(self, 'turn_time'):
            return list(filter(lambda action: action.time >= self.flop_time, self.player_actions))

        return list(filter(lambda action: action.time >= self.flop_time and action.time < self.turn_time, self.player_actions))

    def pre_river_actions(self):
        if not hasattr(self, 'turn_time'): # sometimes rounds don't go to a turn
            return []

        # if the round ends after the turn, but before the river, get all the actions after turn
        if not hasattr(self, 'river_time'):
            return list(filter(lambda action: action.time >= self.turn_time, self.player_actions))

        return list(filter(lambda action: action.time >= self.turn_time and action.time < self.river_time, self.player_actions))

    def post_river_actions(self):
        if not hasattr(self, 'river_time'): # sometimes rounds don't go to a turn or river
            return []

        return list(filter(lambda action: action.time >= self.river_time, self.player_actions))

def date_of_csv(csv_name):
    return datetime.strptime(csv_name.split(".")[0].split("_")[2], "%Y%m%d").date()

def fix_up_player_names(log_lines):
    normalized_name_log_lines = []
    for line in log_lines:
        name_matches = re.findall(PLAYER_NAME_REGEX, line)
        if name_matches:
            for name in name_matches:
                if name.lower() in KNOWN_NAME_FIX_UPS:
                    normalized_name = KNOWN_NAME_FIX_UPS[name.lower()]
                    #print("Fixing name", name, "to", normalized_name)
                elif all(char in set('george') for char in name.lower()): # is it greg?
                    #print("Found an elusive greg", name)
                    normalized_name = "George"
                else:
                    print("Not sure if this person is already normalized", name)
                    print("If this is a new player, add them to KNOWN_NAME_FIX_UPS and run again")
                    exit(-1)

                existing_player_name_format= '""%s @ ' % name
                replacement_player_name_format = '""%s @ ' % normalized_name
                line = line.replace(existing_player_name_format, replacement_player_name_format)

        normalized_name_log_lines.append(line)

    return normalized_name_log_lines


def graph_stack_history(player_history, title, last_file, show_event_points=False):
    for player in player_history:
        player_hand_times = [ ht for _, ht in player_history[player] ]
        player_chips = [ chips for chips, _ in player_history[player] ]
        df_dict = {
            player: player_chips,
            "Time": player_hand_times,
        }
        df = pd.DataFrame(df_dict)
        if show_event_points:
            plt.plot("Time", player, data=df, marker='o', label="{}: ${:.2f}".format(player, player_chips[-1] / 100.00))
        else:
            plt.plot("Time", player, data=df, label="{}: ${:.2f}".format(player, player_chips[-1] / 100.00))


    plt.legend()
    plt.title(title)
    plt.ylabel("Profit in cents (USD)")

    if show_event_points:
        file_name = last_file.split(".")[0] + "_all_time_profit_graph.png"
    else:
        file_name = last_file.split(".")[0] + "_profit_graph.png"
    file_name = file_name.replace("logs", "graphs")
    plt.savefig(file_name)
    plt.show()

### Stats methods

# helper method to get the poker round from a timestamp
def poker_round_for_timestamp(rounds, time):
    for round in rounds:
        if round.start_time <= time and round.end_time >= time:
            return round

    return None

# returns the player name who won the most rounds and those rounds. Also all other player wins
def most_wins(rounds):
    player_wins = {}
    for round in rounds:
        for player in round.winning_players:
            existing_winning_rounds = player_wins.get(player, [])
            existing_winning_rounds.append(round)
            player_wins[player] = existing_winning_rounds

    most_winning_player, winning_rounds = max(player_wins.items(), key = lambda k : len(k[1]))

    return most_winning_player, winning_rounds, player_wins

# returns the round where the most amount was won
def biggest_win(rounds):
    biggest_amount = 0
    biggest_round = None
    for round in rounds:
        for amount in round.winning_amounts:
            if amount > biggest_amount:
                biggest_amount = amount
                biggest_round = round

    return biggest_round

# returns the number of rounds where a player was seated
def rounds_played_by_players(rounds):
    player_round_counts = defaultdict(lambda: 0)
    for round in rounds:
        for player in round.player_balances: # they only have a balance if they're in the start of that round
            player_round_counts[player] += 1

    return player_round_counts

# returns the largest raise/bet for the round actions provided
def largest_raise_or_bet_for_round_actions(round_actions):
    biggest_raise_action = None

    raise_or_bet_actions = filter(lambda action: action.action_type in [RoundAction.raises, RoundAction.bets], round_actions)
    for raise_action in raise_or_bet_actions:
        if biggest_raise_action is None or raise_action.amount > biggest_raise_action.amount:
                biggest_raise_action = raise_action

    return biggest_raise_action

# returns the number of folds per player in round_actions
def number_of_folds_per_player(round_actions):
    player_folds = defaultdict(lambda: 0)
    fold_actions = filter(lambda action: action.action_type == RoundAction.folds, round_actions)
    for action in fold_actions:
        player_folds[action.player] += 1

    return player_folds

# all-ins for players in round_actions
def all_ins_per_player(round_actions):
    player_all_ins = defaultdict(lambda: [])
    for round in round_actions:
        if round.all_in:
            player_all_ins[round.player].append(round)

    return player_all_ins

# returns the winning PokerRounds by each player corresponding to the rounds in the
# player_to_round_actions dict rounds.
def player_wins_for_round_actions(all_rounds, player_to_round_actions):
    player_to_winning_rounds = defaultdict(lambda: [])
    for player, round_actions in player_to_round_actions.items():
        for action in round_actions:
            poker_round = poker_round_for_timestamp(all_rounds, action.time) # use the timestamp to look up the round (inefficient, but it works)
            if player in poker_round.winning_players:
                player_to_winning_rounds[player].append(poker_round)

    return player_to_winning_rounds

# returns the number of times per player that they showed their hidden hand
# after they won the round
def gentleman_scores_by_player(rounds):
    rounds_without_winner_showing_hand = list(filter(lambda r: r.winning_hands == [], rounds))
    player_to_num_hand_shows_post_win = defaultdict(lambda: 0)
    for round in rounds_without_winner_showing_hand:
        for winning_player in round.winning_players:
            if winning_player in round.player_to_hand: # if we know what their hand is
                player_to_num_hand_shows_post_win[winning_player] += 1

    return player_to_num_hand_shows_post_win

# returns the number of different types of shown winning hands per player
def winning_hand_types_by_player(rounds):
    player_to_hand_type_to_wins = defaultdict(lambda: defaultdict(lambda: 0))
    hands_with_known_win = 0
    for round in rounds:
        for i in range(len(round.winning_players)):
            if round.winning_hands == []:
                continue # unknown winning hand
            winning_player = round.winning_players[i]
            hand = round.winning_hands[i]
            hands_with_known_win += 1
            prefix, _ = hand.split("(combination:")# i.e. Pair, K's
            hand_type = prefix.split(",")[0] # i.e. Pair
            if "High" in hand_type: # "A's High" -> "High Card"
                hand_type = "High Card"
            player_to_hand_type_to_wins[winning_player][hand_type] += 1

    hand_display_sort_order = {"High Card": 0, "Pair": 1, "Two Pair": 2, "Three of a Kind": 3, "Straight": 4, "Flush": 5, "Full House": 6, "Four of a Kind": 7, "Straight Flush": 8}
    for player, winning_hands_counts in player_to_hand_type_to_wins.items():
        sorted_winning_hands_counts = sorted(winning_hands_counts.items(), key=lambda i:hand_display_sort_order[i[0]])
        print(f'{player}:')
        for hand, count in sorted_winning_hands_counts:
            print(f'  {hand}: {count}/{hands_with_known_win} ({count / hands_with_known_win * 100.0:.2f}%)')

    return player_to_hand_type_to_wins, hands_with_known_win



def print_core_stats(rounds):
    num_rounds_with_player = rounds_played_by_players(rounds)
    most_wins_player, winning_rounds, all_player_wins = most_wins(rounds)

    all_player_wins_list = list(all_player_wins.items())
    all_player_wins_list.sort(key=lambda w: len(w[1]), reverse=True)
    print("\n------- Winning Hands of Hands Played")
    print(f'{most_wins_player} won the most rounds at {len(winning_rounds)} rounds out of {num_rounds_with_player[most_wins_player]} played rounds ({len(winning_rounds)/num_rounds_with_player[most_wins_player] * 100:.2f}%).\n')
    for player, rounds_won in all_player_wins_list:
        print(f'{player} won {len(rounds_won)}/{num_rounds_with_player[player]} ({len(rounds_won)/num_rounds_with_player[player] * 100:.2f}%)')

    biggest_win_round = biggest_win(rounds)
    print("\n------- Biggest Winning Hand")
    print(f'{", ".join(biggest_win_round.winning_players)} won the most at {biggest_win_round.winning_amounts} on {biggest_win_round.start_time}.\nTable cards: {biggest_win_round.table_cards}.\nWinning hands: {", ".join(biggest_win_round.winning_hands)}.\nAll player\'s cards: {biggest_win_round.player_to_hand}')

    gent_scores_by_player_dict = gentleman_scores_by_player(rounds)
    gent_scores_by_player = list(gent_scores_by_player_dict.items())
    gent_scores_by_player.sort(key=lambda p: p[1], reverse=True)

    print("\n------- Gentleman Scores (Showing Hidden Hand After Win)")
    formatted = "\n".join("{: >10} {: >5} ({:,.2f}%)".format(player, gent_score, gent_score / len(all_player_wins[player]) * 100.0) for player, gent_score in gent_scores_by_player)
    print(formatted)

    all_pre_flop_actions = reduce(lambda acc_actions, round: acc_actions + round.pre_flop_actions(), rounds, [])
    all_pre_turn_actions = reduce(lambda acc_actions, round: acc_actions + round.pre_turn_actions(), rounds, [])
    all_pre_river_actions = reduce(lambda acc_actions, round: acc_actions + round.pre_river_actions(), rounds, [])
    all_post_river_actions = reduce(lambda acc_actions, round: acc_actions + round.post_river_actions(), rounds, [])

    biggest_raise_pre_flop = largest_raise_or_bet_for_round_actions(all_pre_flop_actions)
    biggest_raise_pre_turn = largest_raise_or_bet_for_round_actions(all_pre_turn_actions)
    biggest_raise_pre_river = largest_raise_or_bet_for_round_actions(all_pre_river_actions)
    biggest_raise_post_river = largest_raise_or_bet_for_round_actions(all_post_river_actions)

    print("\n------- Biggest Raises/Bets")
    # if biggest_raise_pre_flop:
    #     print("--- Pre-flop")
    #     print(f'{biggest_raise_pre_flop.to_string()}')
    #     round = poker_round_for_timestamp(rounds, biggest_raise_pre_flop.time)
    #     print(f'  {", ".join(round.winning_players)} won {round.winning_amounts} this round\n  Table cards: {round.table_cards}\n  Winning hands: {", ".join(round.winning_hands)}\n  All player\'s cards: {round.player_to_hand}')
    if biggest_raise_pre_turn:
        print("--- Pre-turn")
        print(f'{biggest_raise_pre_turn.to_string()}')
        round = poker_round_for_timestamp(rounds, biggest_raise_pre_turn.time)
        print(f'  {", ".join(round.winning_players)} won {round.winning_amounts} this round\n  Table cards: {round.table_cards}\n  Winning hands: {", ".join(round.winning_hands)}\n  All player\'s cards: {round.player_to_hand}')
    if biggest_raise_pre_river:
        print("--- Pre-river")
        print(f'{biggest_raise_pre_river.to_string()}')
        round = poker_round_for_timestamp(rounds, biggest_raise_pre_river.time)
        print(f'  {", ".join(round.winning_players)} won {round.winning_amounts} this round\n  Table cards: {round.table_cards}\n  Winning hands: {", ".join(round.winning_hands)}\n  All player\'s cards: {round.player_to_hand}')
    if biggest_raise_post_river:
        print("--- Post-river")
        print(f'{biggest_raise_post_river.to_string()}')
        round = poker_round_for_timestamp(rounds, biggest_raise_post_river.time)
        print(f'  {", ".join(round.winning_players)} won {round.winning_amounts} this round\n  Table cards: {round.table_cards}\n  Winning hands: {", ".join(round.winning_hands)}\n  All player\'s cards: {round.player_to_hand}')

    player_pre_flop_folds = list(number_of_folds_per_player(all_pre_flop_actions).items())
    player_pre_flop_folds.sort(key=lambda w: w[1], reverse=True)

    player_pre_turn_folds = list(number_of_folds_per_player(all_pre_turn_actions).items())
    player_pre_turn_folds.sort(key=lambda w: w[1], reverse=True)

    player_pre_river_folds = list(number_of_folds_per_player(all_pre_river_actions).items())
    player_pre_river_folds.sort(key=lambda w: w[1], reverse=True)

    player_post_river_folds = list(number_of_folds_per_player(all_post_river_actions).items())
    player_post_river_folds.sort(key=lambda w: w[1], reverse=True)

    print("\n------- Folds Per Player of Hands Played")
    print("--- Pre-flop")
    formatted = "\n".join("{: >10} {: >10} ({:,.2f}%)".format(player, fold_count, fold_count / num_rounds_with_player[player] * 100.0) for player, fold_count in player_pre_flop_folds)
    print(formatted)
    print("--- Pre-turn")
    formatted = "\n".join("{: >10} {: >10} ({:,.2f}%)".format(player, fold_count, fold_count / num_rounds_with_player[player] * 100.0) for player, fold_count in player_pre_turn_folds)
    print(formatted)
    print("--- Pre-river")
    formatted = "\n".join("{: >10} {: >10} ({:,.2f}%)".format(player, fold_count, fold_count / num_rounds_with_player[player] * 100.0) for player, fold_count in player_pre_river_folds)
    print(formatted)
    print("--- Post-river")
    formatted = "\n".join("{: >10} {: >10} ({:,.2f}%)".format(player, fold_count, fold_count / num_rounds_with_player[player] * 100.0) for player, fold_count in player_post_river_folds)
    print(formatted)

    player_pre_flop_all_ins_dict = all_ins_per_player(all_pre_flop_actions)
    player_pre_flop_all_ins = list(player_pre_flop_all_ins_dict.items())
    player_pre_flop_all_ins.sort(key=lambda w: len(w[1]), reverse=True)
    player_wins_for_pre_flop_all_ins = player_wins_for_round_actions(rounds, player_pre_flop_all_ins_dict)

    player_pre_turn_all_ins_dict = all_ins_per_player(all_pre_turn_actions)
    player_pre_turn_all_ins = list(player_pre_turn_all_ins_dict.items())
    player_pre_turn_all_ins.sort(key=lambda w: len(w[1]), reverse=True)
    player_wins_for_pre_turn_all_ins = player_wins_for_round_actions(rounds, player_pre_turn_all_ins_dict)

    player_pre_river_all_ins_dict = all_ins_per_player(all_pre_river_actions)
    player_pre_river_all_ins = list(player_pre_river_all_ins_dict.items())
    player_pre_river_all_ins.sort(key=lambda w: len(w[1]), reverse=True)
    player_wins_for_pre_river_all_ins = player_wins_for_round_actions(rounds, player_pre_river_all_ins_dict)

    player_post_river_all_ins_dict = all_ins_per_player(all_post_river_actions)
    player_post_river_all_ins = list(player_post_river_all_ins_dict.items())
    player_post_river_all_ins.sort(key=lambda w: len(w[1]), reverse=True)
    player_wins_for_post_river_all_ins = player_wins_for_round_actions(rounds, player_post_river_all_ins_dict)

    print("\n------- All-ins Per Player of Hands Played")
    if player_pre_flop_all_ins:
        print("--- Pre-flop")
        formatted = "\n".join("{: >10} {: >10} {:,.2f}% (Won {:,.2f}%)".format(player, len(all_ins), len(all_ins) / num_rounds_with_player[player] * 100.0, len(player_wins_for_pre_flop_all_ins[player]) / len(all_ins) * 100.0) for player, all_ins in player_pre_flop_all_ins)
        print(formatted)
    if player_pre_turn_all_ins:
        print("--- Pre-turn")
        formatted = "\n".join("{: >10} {: >10} {:,.2f}% (Won {:,.2f}%)".format(player, len(all_ins), len(all_ins) / num_rounds_with_player[player] * 100.0, len(player_wins_for_pre_turn_all_ins[player]) / len(all_ins) * 100.0) for player, all_ins in player_pre_turn_all_ins)
        print(formatted)

    if player_pre_river_all_ins:
        print("--- Pre-river")
        formatted = "\n".join("{: >10} {: >10} {:,.2f}% (Won {:,.2f}%)".format(player, len(all_ins), len(all_ins) / num_rounds_with_player[player] * 100.0, len(player_wins_for_pre_river_all_ins[player]) / len(all_ins) * 100.0) for player, all_ins in player_pre_river_all_ins)
        print(formatted)

    if player_post_river_all_ins:
        print("--- Post-river")
        formatted = "\n".join("{: >10} {: >10} {:,.2f}% (Won {:,.2f}%)".format(player, len(all_ins), len(all_ins) / num_rounds_with_player[player] * 100.0, len(player_wins_for_post_river_all_ins[player]) / len(all_ins) * 100.0) for player, all_ins in player_post_river_all_ins)
        print(formatted)

    print("\n------- Winning Hand Breakdown By Player")
    winning_hand_types_by_player(rounds)

### Main execution

parser = argparse.ArgumentParser(description="Just an example",
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument("-a", "--all", action="store_true", help="graph all csvs in one chart")
parser.add_argument("-d", "--date", help="graph the logs/poker_night_YYYYMMDD.csv on a chart", default=CSV_FILE)

args = parser.parse_args()

if args.all:
    print("Graphing all csvs in single chart")
    csv_files = [f for f in os.listdir('logs/') if os.path.isfile('logs/' + f) and f.endswith(".csv")]
    csv_files.sort()

    event_date = date_of_csv(csv_files[-1]).strftime("%Y/%m/%d")
    all_player_history = {}
    all_poker_rounds = []
    for filename in csv_files:
        filename = 'logs/' + filename
        with open(filename) as file:
            logs = fix_up_player_names(file.readlines())
            if logs[0] == "entry,at,order\n":
                logs.pop(0) # drop csv header
            logs.reverse()
            curr_event_date = date_of_csv(filename).strftime("%Y/%m/%d")
            event = PokerNightEvent(curr_event_date, logs)
            all_poker_rounds += event.rounds
            player_history = event.player_stack_history()

            # now merge the latest event with the on-going logs that only store the final profits each week
            for player, current_event_stack in player_history.items():
                if player not in all_player_history:
                    # they're new this game, just add them in cause they start at zero
                    all_player_history[player] = [current_event_stack[-1]]
                else:
                    # need to update the current event's profit to account for profit before this game
                    last_profit = all_player_history[player][-1][0]
                    updated_event_stack = [(current_event_stack[-1][0] + last_profit, current_event_stack[-1][1])]
                    all_player_history[player] += updated_event_stack

    # Print some stats out
    print_core_stats(all_poker_rounds)

    graph_stack_history(all_player_history, "All-time profit history as of " + event_date, csv_files[-1], show_event_points=True)

else:
    csv_file = "logs/poker_night_" + args.date + ".csv"
    print("Graphing single csv", csv_file)
    event_date = date_of_csv(csv_file).strftime("%Y/%m/%d")
    with open(csv_file) as file:
        logs = fix_up_player_names(file.readlines())
        if logs[0] == "entry,at,order\n":
            logs.pop(0) # drop csv header
        logs.reverse()
        event = PokerNightEvent(event_date, logs)
        player_history = event.player_stack_history()

        # Print some stats out
        print_core_stats(event.rounds)

        graph_stack_history(player_history, "Profit for " + event_date, csv_file)

