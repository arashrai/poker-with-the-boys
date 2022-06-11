import csv, os
import matplotlib.pyplot as plt
import pandas as pd
from datetime import datetime
from matplotlib.dates import DateFormatter
import argparse

CSV_FILE = "poker_night_20220609.csv"

def date_of_csv(csv_name):
    return datetime.strptime(csv_name.split(".")[0].split("_")[-1], "%Y%m%d").date()

def extract_data_from_stack_row(row):
    player_to_amount = {}
    quote_split = row[0].split('"')

    i = 0
    while i != len(quote_split):
        if "@" in quote_split[i]:
            player_name = quote_split[i][: quote_split[i].index("@") - 1]
            amount = int(
                quote_split[i + 1][
                    quote_split[i + 1].index("(") + 1 : quote_split[i + 1].index(")")
                ]
            )
            player_to_amount[player_name] = amount
        i += 1

    return player_to_amount, row[2]


def extract_data_from_approval_row(row):
    r = row[0]
    player_name = r[r.index('"') + 1 : r.index("@") - 1]
    amount = int(r[r.index("of") + 3 : r.index(".")])
    return player_name, amount


def extract_data_from_admin_stack_change_row(row):
    #print(row)
    r = row[0]
    player_name = r[r.index('"') + 1 : r.index("@") - 1]
    amount = int(r[r.index("adding ") + 7 : r.index("chips") - 1])
    return player_name, amount


def extract_data_from_quit_row(row):
    r = row[0]
    player_name = r[r.index('"') + 1 : r.index("@") - 1]
    amount = int(r[r.index("of") + 3 : r.index(".")])
    return player_name, amount


def extract_stack_history(logs):
    player_to_stack_history = {} # player_name to array of tuples of (amount, hand_time)
    player_to_buy_ins = {}
    player_to_is_eliminated = {}

    hand_times = []

    for row in logs:
        if "Player stacks" in row[0]:
            player_to_amount, unix_time = extract_data_from_stack_row(row)
            hand_time = datetime.fromtimestamp(int(unix_time) / 100000)

            for player, amount in player_to_amount.items():
                if player in player_to_stack_history:
                    player_to_stack_history[player].append(
                        (amount - player_to_buy_ins[player] * 1000, hand_time)
                    )
                elif player not in player_to_stack_history:
                    # if they aren't in the history, give them zero amount for all previous hands but this one
                    player_to_stack_history[player] = [ (0, prev_hand_time) for prev_hand_time in hand_times ] + [
                        (amount - player_to_buy_ins[player] * 1000, hand_time)
                    ]
            for p in player_to_stack_history:
                if p not in player_to_amount:
                    if player_to_is_eliminated[p]:
                        # if they're out of the game, give them the min amount (lost it all)
                        player_to_stack_history[p].append(
                            (0 - player_to_buy_ins[p] * 1000, hand_time)
                        )
                    else:
                        # if they're around but not in on the round, give them their prev value
                        player_to_stack_history[p].append(
                                (player_to_stack_history[p][-1][0], hand_time)
                        )
            hand_times.append(hand_time)
        elif "The admin approved the player" in row[0]:
            player, amount = extract_data_from_approval_row(row)
            if player not in player_to_buy_ins:
                player_to_buy_ins[player] = 0
            player_to_buy_ins[player] += 1
            player_to_is_eliminated[player] = False
        elif "WARNING: the admin queued the stack change" in row[0]:
            player, amount = extract_data_from_admin_stack_change_row(row)
            player_to_buy_ins[player] += 1
        elif "quits the game" in row[0]:
            player, amount = extract_data_from_quit_row(row)
            if amount == 0:
                player_to_is_eliminated[player] = True

    return player_to_stack_history


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

    all_player_history = {}
    for filename in csv_files:
        with open(filename) as file:
            csv_reader = csv.reader(file, delimiter=",")
            logs = []
            for row in csv_reader:
                logs.append(row)

            logs.reverse()
            player_history = extract_stack_history(logs)
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


    graph_stack_history(all_player_history, "All-time profit history as of " +  date_of_csv(csv_files[-1]).strftime("%Y/%m/%d"), show_event_points=True)

else:
    csv_file = args.input
    print("Graphing single csv", csv_file)
    logs = []
    with open(csv_file) as file:
        csv_reader = csv.reader(file, delimiter=",")
        for row in csv_reader:
            logs.append(row)

    logs.reverse()
    player_history = extract_stack_history(logs)
    graph_stack_history(player_history, "Profit for " + date_of_csv(csv_file).strftime("%Y/%m/%d"))
