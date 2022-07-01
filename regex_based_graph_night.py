import csv, os
import matplotlib.pyplot as plt
import pandas as pd
from datetime import datetime
from matplotlib.dates import DateFormatter
import argparse
import re
from pprint import pprint

CSV_FILE = "poker_night_20220609.csv"
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
FLOP_CARDS_REGEX = re.compile('"Flop:  \[(.*?)]') # why does "Flop:  " have two spaces smh
TURN_OR_RIVER_CARD_REGEX = re.compile('"(Turn|River): .* \[(.*?)]')
UNDEALT_CARDS_REGEX = re.compile('"Undealt cards: .* \[(.*?)]')

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

    def most_wins(self):
        player_wins = {}
        for round in self.rounds:
            for player in round.winning_players:
                existing_winning_rounds = player_wins.get(player, [])
                existing_winning_rounds.append(round)
                player_wins[player] = existing_winning_rounds
            
        most_winning_player, winning_rounds = max(player_wins.items(), key = lambda k : len(k[1]))
        
        print(most_winning_player)
        
        biggest_win_round = max(winning_rounds, key=lambda r: r.winning_amounts[0]) # TODO: need to use the right index here, could be second winner splitting pot?
        
        print(most_winning_player, "had biggest win of", vars(biggest_win_round))

        


class PokerRound(): # multiple rounds in a poker night event
    def __init__(self, round_logs):
        self.metadata_extraction(round_logs)
        self.starting_balances_extraction(round_logs)
        self.extract_table_cards(round_logs)
        self.extract_winner_info(round_logs)
        self.extract_hands(round_logs)
        self.extract_admin_balance_adjustments(round_logs)
        self.extract_player_movements(round_logs)

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
                if player in self.players_stood_up:
                    print("THIS SHOULDN'T HAPPEN?", vars(self))
                    breakpoint()
                    exit(-1)
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
                self.table_cards = re.findall(FLOP_CARDS_REGEX, row)[0].split(", ")
            elif re.match(UNDEALT_CARDS_REGEX, row):
                self.undealt_cards = re.findall(UNDEALT_CARDS_REGEX, row)[0].split(", ")
            elif re.match(TURN_OR_RIVER_CARD_REGEX, row):
                _, new_card = re.findall(TURN_OR_RIVER_CARD_REGEX, row)[0]
                self.table_cards.append(new_card)

def date_of_csv(csv_name):
    return datetime.strptime(csv_name.split(".")[0].split("_")[2], "%Y%m%d").date()

def fix_up_player_names(player_to_stack_history):
    known_fix_ups = {
            "peelic": "Prilik",
            "arashh": "Arash",
            "ash": "Arash", #?
            "arash": "Arash",
            "susan": "Arash", #?
            "annie": "Annie",
            "spenner": "Spencer",
            "spenny": "Spencer",
            "spenny2": "Spencer",
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
            "jerms": "James",
            "jems": "James",
            "gems": "James",
            "james": "James",
            "josh": "Josh",
            "jonah": "Jonah",
            "max": "Max",
            "sam": "Sam",
    }

    names = player_to_stack_history.keys()
    normalized_player_name_dict = {}
    for name in names:
        if name.lower() in known_fix_ups:
            normalized_name = known_fix_ups[name.lower()]
            #print("Fixing name", name, "to", normalized_name)
        elif all(char in set('george') for char in name.lower()): # is it greg?
            #print("Found an elusive greg", name)
            normalized_name = "George"
        else:
            print("Not sure if this person is already normalized", name)
            normalized_name = name.capitalize()
        normalized_player_name_dict[normalized_name] = player_to_stack_history[name] if normalized_name not in normalized_player_name_dict else normalized_player_name_dict[normalized_name] + player_to_stack_history[name]

    return normalized_player_name_dict


def graph_stack_history(player_history, title, show_event_points=False):
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
    plt.ylabel("Profit in cents (CAD)")

    file_name = CSV_FILE.split(".")[0] + "_profit_graph.png"
    plt.savefig(file_name)
    plt.show()



parser = argparse.ArgumentParser(description="Just an example",
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument("-a", "--all", action="store_true", help="graph all csvs in one chart")
parser.add_argument("-i", "--input", help="graph this one csv on a chart", default=CSV_FILE)

args = parser.parse_args()

if args.all:
    print("Graphing all csvs in single chart")
    csv_files = [f for f in os.listdir('.') if os.path.isfile(f) and f.endswith(".csv")]
    csv_files.sort()

    event_date = date_of_csv(csv_files[-1]).strftime("%Y/%m/%d")
    all_player_history = {}
    for filename in csv_files:
        with open(filename) as file:
            logs = file.readlines()
            if logs[0] == "entry,at,order\n":
                logs.pop(0) # drop csv header
            logs.reverse()
            curr_event_date = date_of_csv(filename).strftime("%Y/%m/%d")
            event = PokerNightEvent(curr_event_date, logs)
            player_history = event.player_stack_history()
            normalized_name_player_history = fix_up_player_names(player_history)

            # now merge the latest event with the on-going logs that only store the final profits each week
            for player, current_event_stack in normalized_name_player_history.items():
                if player not in all_player_history:
                    # they're new this game, just add them in cause they start at zero
                    all_player_history[player] = [current_event_stack[-1]]
                else:
                    # need to update the current event's profit to account for profit before this game
                    last_profit = all_player_history[player][-1][0]
                    updated_event_stack = [(current_event_stack[-1][0] + last_profit, current_event_stack[-1][1])]
                    all_player_history[player] += updated_event_stack


    graph_stack_history(all_player_history, "All-time profit history as of " + event_date, show_event_points=True)

else:
    csv_file = args.input
    print("Graphing single csv", csv_file)
    event_date = date_of_csv(csv_file).strftime("%Y/%m/%d")
    with open(csv_file) as file:
        logs = file.readlines()
        if logs[0] == "entry,at,order\n":
            logs.pop(0) # drop csv header
        logs.reverse()
        event = PokerNightEvent(event_date, logs)
        player_history = event.player_stack_history()
        normalized_name_player_history = fix_up_player_names(player_history)
        
        event.most_wins()
        graph_stack_history(normalized_name_player_history, "Profit for " + event_date)

