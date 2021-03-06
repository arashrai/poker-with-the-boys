import csv
import matplotlib.pyplot as plt
import pandas as pd
from datetime import datetime

CSV_FILE = "logs/poker_night_20220707.csv"
logs = []

with open(CSV_FILE) as file:
    csv_reader = csv.reader(file, delimiter=",")
    for row in csv_reader:
        logs.append(row)

logs.reverse()


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
    print(row)
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
    player_to_stack_history = {}
    player_to_buy_ins = {}
    player_to_is_eliminated = {}

    hand_times = []

    for row in logs:
        if "Player stacks" in row[0]:
            player_to_amount, unix_time = extract_data_from_stack_row(row)
            for player, amount in player_to_amount.items():
                if player in player_to_stack_history:
                    player_to_stack_history[player].append(
                        amount - player_to_buy_ins[player] * 1000
                    )
                elif player not in player_to_stack_history:
                    player_to_stack_history[player] = [0] * len(hand_times) + [
                        amount - player_to_buy_ins[player] * 1000
                    ]
            for p in player_to_stack_history:
                if p not in player_to_amount:
                    if player_to_is_eliminated[p]:
                        player_to_stack_history[p].append(
                            0 - player_to_buy_ins[p] * 1000
                        )
                    else:
                        player_to_stack_history[p].append(
                            player_to_stack_history[p][-1]
                        )
            hand_times.append(datetime.fromtimestamp(int(unix_time) / 100000))
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

    return player_to_stack_history, hand_times


def graph_stack_history(logs):
    player_history, hand_times = extract_stack_history(logs)

    df_dict = {}
    df_dict["Time"] = hand_times
    for player, history in player_history.items():
        df_dict[player] = history

    df = pd.DataFrame(df_dict)

    for player in player_history:
        plt.plot("Time", player, data=df)
    plt.legend()
    file_name = CSV_FILE.split(".")[0] + "_profit_graph.png"
    file_name = file_name.replace("logs", "graphs")
    plt.savefig(file_name)
    plt.show()


graph_stack_history(logs)
