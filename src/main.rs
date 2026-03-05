use anyhow::{anyhow, bail, Context, Result};
use chrono::{DateTime, Local, NaiveDate, TimeZone};
use clap::Parser;
use indexmap::IndexMap;
use once_cell::sync::Lazy;
use plotters::prelude::*;
use regex::Regex;
use std::collections::HashMap;
use std::fs;
use std::path::Path;

const CSV_FILE: &str = "logs/poker_night_20220707.csv";

static START_HAND_REGEX: Lazy<Regex> =
    Lazy::new(|| Regex::new(r#"\"-- starting hand \#(\d+).*,(\d+)"#).unwrap());
static ADMIN_ADJUSTMENT_REGEX: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"\"The admin updated the player \"\"(.*?) @ \S+ stack from (\d+) to (\d+)"#).unwrap()
});
static BUY_IN_REGEX: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"\"The player \"\"(.*?) @ .* joined the game with a stack of (\d+)\.\",[^,]+,(\d+)"#)
        .unwrap()
});
static SIT_DOWN_REGEX: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"\"The player \"\"(.*?) @ \S+ sit back with the stack of (\d+)\.\",[^,]+,(\d+)"#)
        .unwrap()
});
static EXIT_REGEX: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"\"The player \"\"(.*?) @ \S+ quits the game with a stack of (\d+)\.\",[^,]+,(\d+)"#)
        .unwrap()
});
static STANDUP_REGEX: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"\"The player \"\"(.*?) @ \S+ stand up with the stack of (\d+)\.\",[^,]+,(\d+)"#)
        .unwrap()
});
static PLAYER_HAND_REGEX: Lazy<Regex> =
    Lazy::new(|| Regex::new(r#"\"\"(.*?) @ \S+ shows a ([^.]+)"#).unwrap());
static PLAYER_STACK_REGEX: Lazy<Regex> =
    Lazy::new(|| Regex::new(r#"#\d+ \"\"(.*?) @ [^\"\"]+\"\" \((\d+)\)"#).unwrap());
static PLAYER_WINNER_WITH_HAND_REGEX: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"\"\"(.*?) @ \S+ collected (\d+) from pot with (.*?)""#).unwrap()
});
static PLAYER_WINNER_WITHOUT_HAND_REGEX: Lazy<Regex> =
    Lazy::new(|| Regex::new(r#"\"\"(.*?) @ \S+ collected (\d+) from pot"#).unwrap());
static FLOP_CARDS_REGEX: Lazy<Regex> =
    Lazy::new(|| Regex::new(r#"\"Flop:  \[(.*?)]\",[^,]+,(\d+)"#).unwrap());
static TURN_CARD_REGEX: Lazy<Regex> =
    Lazy::new(|| Regex::new(r#"\"Turn: .* \[(.*?)]\",[^,]+,(\d+)"#).unwrap());
static RIVER_CARD_REGEX: Lazy<Regex> =
    Lazy::new(|| Regex::new(r#"\"River: .* \[(.*?)]\",[^,]+,(\d+)"#).unwrap());
static UNDEALT_CARDS_REGEX: Lazy<Regex> =
    Lazy::new(|| Regex::new(r#"\"Undealt cards: .* \[(.*?)]"#).unwrap());
static PLAYER_NAME_REGEX: Lazy<Regex> =
    Lazy::new(|| Regex::new(r#"[\"]{2,3}(\S+) @ "#).unwrap());
static PLAYER_INITIAL_POST_REGEX: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"\"\"(.*?) @ \S+\"\" posts a (straddle|big blind|small blind) of (\d+)\",[^,]+,(\d+)"#)
        .unwrap()
});
static PLAYER_FOLDS_REGEX: Lazy<Regex> =
    Lazy::new(|| Regex::new(r#"\"\"(.*?) @ \S+\"\" folds\",[^,]+,(\d+)"#).unwrap());
static PLAYER_CALLS_REGEX: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"\"\"(.*?) @ \S+\"\" calls (\d+)(?: and go all in)?\",[^,]+,(\d+)"#).unwrap()
});
static PLAYER_CHECKS_REGEX: Lazy<Regex> =
    Lazy::new(|| Regex::new(r#"\"\"(.*?) @ \S+\"\" checks\",[^,]+,(\d+)"#).unwrap());
static PLAYER_BETS_REGEX: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"\"\"(.*?) @ \S+\"\" bets (\d+)(?: and go all in)?\",[^,]+,(\d+)"#).unwrap()
});
static PLAYER_RAISES_REGEX: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"\"\"(.*?) @ \S+\"\" raises to (\d+)(?: and go all in)?\",[^,]+,(\d+)"#)
        .unwrap()
});

static KNOWN_NAME_FIX_UPS: Lazy<HashMap<&'static str, &'static str>> = Lazy::new(|| {
    HashMap::from([
        ("peelic", "Prilik"),
        ("arashh", "Arash"),
        ("ash", "Arash"),
        ("arash", "Arash"),
        ("susan", "Arash"),
        ("annie", "Annie"),
        ("spencer", "Spencer"),
        ("spenner", "Spencer"),
        ("spenny", "Spencer"),
        ("spenny2", "Spencer"),
        ("speny", "Spencer"),
        ("spange", "Ethan"),
        ("biz", "Alex"),
        ("guest", "George"),
        ("stevo-ipad", "Stephen"),
        ("stevo", "Stephen"),
        ("stephen", "Stephen"),
        ("david", "David"),
        ("daveed", "David"),
        ("daveeed", "David"),
        ("daveeeed", "David"),
        ("eveeeda", "David"),
        ("dve-eed", "David"),
        ("jerms", "James"),
        ("jems", "James"),
        ("gems", "James"),
        ("james", "James"),
        ("josh", "Josh"),
        ("jonah", "Jonah"),
        ("jonah dlin", "Jonah"),
        ("max", "Max"),
        ("sam", "Sam"),
        ("daveeeeeed", "David"),
        ("daveeeeeeed", "David"),
        ("dave", "David"),
        ("dave-eed", "David"),
        ("daveod", "David"),
        ("stevo-tesla", "Stephen"),
    ])
});

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum RoundAction {
    PostsSmallBlind,
    PostsBigBlind,
    PostsStraddle,
    Bets,
    Folds,
    Raises,
    Checks,
    Calls,
}

impl RoundAction {
    fn from_initial_post(s: &str) -> Option<Self> {
        match s {
            "small blind" => Some(Self::PostsSmallBlind),
            "big blind" => Some(Self::PostsBigBlind),
            "straddle" => Some(Self::PostsStraddle),
            _ => None,
        }
    }

    fn as_str(&self) -> &'static str {
        match self {
            Self::PostsSmallBlind => "small blind",
            Self::PostsBigBlind => "big blind",
            Self::PostsStraddle => "straddle",
            Self::Bets => "bets",
            Self::Folds => "folds",
            Self::Raises => "raises",
            Self::Checks => "checks",
            Self::Calls => "calls",
        }
    }
}

#[derive(Debug, Clone)]
struct PlayerRoundAction {
    player: String,
    amount: i64,
    action_type: RoundAction,
    time: DateTime<Local>,
    all_in: bool,
}

impl PlayerRoundAction {
    fn to_string(&self) -> String {
        if self.amount > 0 {
            format!(
                "{} {} {} at {}",
                self.player,
                self.action_type.as_str(),
                self.amount,
                py_datetime(self.time)
            )
        } else {
            format!(
                "{} {} at {}",
                self.player,
                self.action_type.as_str(),
                py_datetime(self.time)
            )
        }
    }
}

#[derive(Debug, Clone)]
struct PlayerMovement {
    amount: i64,
    time: DateTime<Local>,
}

#[allow(dead_code)]
#[derive(Debug, Clone)]
struct PokerRound {
    round_number: usize,
    start_time: DateTime<Local>,
    end_time: DateTime<Local>,
    flop_time: Option<DateTime<Local>>,
    turn_time: Option<DateTime<Local>>,
    river_time: Option<DateTime<Local>>,
    table_cards: Vec<String>,
    undealt_cards: Vec<String>,
    winning_amounts: Vec<i64>,
    winning_players: Vec<String>,
    winning_hands: Vec<String>,
    player_to_hand: HashMap<String, String>,
    player_to_hand_order: Vec<String>,
    player_balances: IndexMap<String, i64>,
    players_exited: HashMap<String, PlayerMovement>,
    players_stood_up: HashMap<String, PlayerMovement>,
    players_sat_down: HashMap<String, PlayerMovement>,
    player_game_joins: HashMap<String, Vec<PlayerMovement>>,
    admin_adjustments: HashMap<String, i64>,
    player_actions: Vec<PlayerRoundAction>,
}

impl PokerRound {
    fn new(round_logs: &[String]) -> Result<Self> {
        let start_log = round_logs
            .first()
            .ok_or_else(|| anyhow!("round logs missing start"))?;
        let caps = START_HAND_REGEX
            .captures(start_log)
            .ok_or_else(|| anyhow!("failed parsing start hand: {start_log}"))?;
        let round_number: usize = caps[1].parse()?;

        let end_unix = round_logs
            .last()
            .and_then(|l| l.split(',').last())
            .ok_or_else(|| anyhow!("failed parsing round end timestamp"))?;
        let end_time = from_unix_time(end_unix)?;

        let mut player_balances = IndexMap::new();
        let mut start_time = None;
        for log in round_logs {
            if log.starts_with("\"Player stacks") {
                for caps in PLAYER_STACK_REGEX.captures_iter(log) {
                    player_balances.insert(clean_player_name(&caps[1]), caps[2].parse()?);
                }
                let unix_time = log
                    .split(',')
                    .last()
                    .ok_or_else(|| anyhow!("bad player stack timestamp"))?;
                start_time = Some(from_unix_time(unix_time)?);
                break;
            }
        }
        let start_time = start_time.ok_or_else(|| anyhow!("round is missing player stack line"))?;

        let mut flop_time = None;
        let mut turn_time = None;
        let mut river_time = None;
        let mut table_cards = Vec::new();
        let mut undealt_cards = Vec::new();
        let mut winning_amounts = Vec::new();
        let mut winning_players = Vec::new();
        let mut winning_hands = Vec::new();
        let mut player_to_hand = HashMap::new();
        let mut player_to_hand_order = Vec::new();
        let mut players_exited = HashMap::new();
        let mut players_stood_up = HashMap::new();
        let mut players_sat_down = HashMap::new();
        let mut player_game_joins: HashMap<String, Vec<PlayerMovement>> = HashMap::new();
        let mut admin_adjustments = HashMap::new();
        let mut player_actions = Vec::new();

        for row in round_logs {
            if let Some(caps) = ADMIN_ADJUSTMENT_REGEX.captures(row) {
                let player = clean_player_name(&caps[1]);
                let from_balance: i64 = caps[2].parse()?;
                let to_balance: i64 = caps[3].parse()?;
                admin_adjustments.insert(player, to_balance - from_balance);
            }

            if let Some(caps) = EXIT_REGEX.captures(row) {
                let player = clean_player_name(&caps[1]);
                players_exited.insert(
                    player,
                    PlayerMovement {
                        amount: caps[2].parse()?,
                        time: from_unix_time(&caps[3])?,
                    },
                );
            } else if let Some(caps) = STANDUP_REGEX.captures(row) {
                let player = clean_player_name(&caps[1]);
                players_stood_up.insert(
                    player,
                    PlayerMovement {
                        amount: caps[2].parse()?,
                        time: from_unix_time(&caps[3])?,
                    },
                );
            } else if let Some(caps) = BUY_IN_REGEX.captures(row) {
                let player = clean_player_name(&caps[1]);
                let buy_in = PlayerMovement {
                    amount: caps[2].parse()?,
                    time: from_unix_time(&caps[3])?,
                };
                player_game_joins.entry(player).or_default().push(buy_in);
            } else if let Some(caps) = SIT_DOWN_REGEX.captures(row) {
                let player = clean_player_name(&caps[1]);
                players_sat_down.insert(
                    player,
                    PlayerMovement {
                        amount: caps[2].parse()?,
                        time: from_unix_time(&caps[3])?,
                    },
                );
            }

            if let Some(caps) = PLAYER_HAND_REGEX.captures(row) {
                let player = clean_player_name(&caps[1]);
                if !player_to_hand.contains_key(&player) {
                    player_to_hand_order.push(player.clone());
                }
                player_to_hand.insert(player, caps[2].to_string());
            }

            if let Some(caps) = PLAYER_WINNER_WITH_HAND_REGEX.captures(row) {
                winning_players.push(clean_player_name(&caps[1]));
                winning_amounts.push(caps[2].parse()?);
                winning_hands.push(caps[3].to_string());
            } else if let Some(caps) = PLAYER_WINNER_WITHOUT_HAND_REGEX.captures(row) {
                winning_players.push(clean_player_name(&caps[1]));
                winning_amounts.push(caps[2].parse()?);
            }

            if let Some(caps) = FLOP_CARDS_REGEX.captures(row) {
                table_cards = caps[1].split(", ").map(ToString::to_string).collect();
                flop_time = Some(from_unix_time(&caps[2])?);
            } else if let Some(caps) = UNDEALT_CARDS_REGEX.captures(row) {
                undealt_cards = caps[1].split(", ").map(ToString::to_string).collect();
            } else if let Some(caps) = TURN_CARD_REGEX.captures(row) {
                table_cards.push(caps[1].to_string());
                turn_time = Some(from_unix_time(&caps[2])?);
            } else if let Some(caps) = RIVER_CARD_REGEX.captures(row) {
                table_cards.push(caps[1].to_string());
                river_time = Some(from_unix_time(&caps[2])?);
            }

            let mut round_action = None;
            if let Some(caps) = PLAYER_INITIAL_POST_REGEX.captures(row) {
                if let Some(action_type) = RoundAction::from_initial_post(&caps[2]) {
                    round_action = Some(PlayerRoundAction {
                        player: clean_player_name(&caps[1]),
                        amount: caps[3].parse()?,
                        action_type,
                        time: from_unix_time(&caps[4])?,
                        all_in: row.contains("go all in"),
                    });
                }
            } else if let Some(caps) = PLAYER_FOLDS_REGEX.captures(row) {
                round_action = Some(PlayerRoundAction {
                    player: clean_player_name(&caps[1]),
                    amount: 0,
                    action_type: RoundAction::Folds,
                    time: from_unix_time(&caps[2])?,
                    all_in: row.contains("go all in"),
                });
            } else if let Some(caps) = PLAYER_CHECKS_REGEX.captures(row) {
                round_action = Some(PlayerRoundAction {
                    player: clean_player_name(&caps[1]),
                    amount: 0,
                    action_type: RoundAction::Checks,
                    time: from_unix_time(&caps[2])?,
                    all_in: row.contains("go all in"),
                });
            } else if let Some(caps) = PLAYER_CALLS_REGEX.captures(row) {
                round_action = Some(PlayerRoundAction {
                    player: clean_player_name(&caps[1]),
                    amount: caps[2].parse()?,
                    action_type: RoundAction::Calls,
                    time: from_unix_time(&caps[3])?,
                    all_in: row.contains("go all in"),
                });
            } else if let Some(caps) = PLAYER_BETS_REGEX.captures(row) {
                round_action = Some(PlayerRoundAction {
                    player: clean_player_name(&caps[1]),
                    amount: caps[2].parse()?,
                    action_type: RoundAction::Bets,
                    time: from_unix_time(&caps[3])?,
                    all_in: row.contains("go all in"),
                });
            } else if let Some(caps) = PLAYER_RAISES_REGEX.captures(row) {
                round_action = Some(PlayerRoundAction {
                    player: clean_player_name(&caps[1]),
                    amount: caps[2].parse()?,
                    action_type: RoundAction::Raises,
                    time: from_unix_time(&caps[3])?,
                    all_in: row.contains("go all in"),
                });
            }

            if let Some(action) = round_action {
                player_actions.push(action);
            }
        }

        Ok(Self {
            round_number,
            start_time,
            end_time,
            flop_time,
            turn_time,
            river_time,
            table_cards,
            undealt_cards,
            winning_amounts,
            winning_players,
            winning_hands,
            player_to_hand,
            player_to_hand_order,
            player_balances,
            players_exited,
            players_stood_up,
            players_sat_down,
            player_game_joins,
            admin_adjustments,
            player_actions,
        })
    }

    fn pre_flop_actions(&self) -> Vec<PlayerRoundAction> {
        if let Some(flop_time) = self.flop_time {
            self.player_actions
                .iter()
                .filter(|a| a.time < flop_time)
                .cloned()
                .collect()
        } else {
            self.player_actions.clone()
        }
    }

    fn pre_turn_actions(&self) -> Vec<PlayerRoundAction> {
        let Some(flop_time) = self.flop_time else {
            return Vec::new();
        };

        if let Some(turn_time) = self.turn_time {
            self.player_actions
                .iter()
                .filter(|a| a.time >= flop_time && a.time < turn_time)
                .cloned()
                .collect()
        } else {
            self.player_actions
                .iter()
                .filter(|a| a.time >= flop_time)
                .cloned()
                .collect()
        }
    }

    fn pre_river_actions(&self) -> Vec<PlayerRoundAction> {
        let Some(turn_time) = self.turn_time else {
            return Vec::new();
        };

        if let Some(river_time) = self.river_time {
            self.player_actions
                .iter()
                .filter(|a| a.time >= turn_time && a.time < river_time)
                .cloned()
                .collect()
        } else {
            self.player_actions
                .iter()
                .filter(|a| a.time >= turn_time)
                .cloned()
                .collect()
        }
    }

    fn post_river_actions(&self) -> Vec<PlayerRoundAction> {
        let Some(river_time) = self.river_time else {
            return Vec::new();
        };

        self.player_actions
            .iter()
            .filter(|a| a.time >= river_time)
            .cloned()
            .collect()
    }
}

#[derive(Debug)]
struct PokerNightEvent {
    rounds: Vec<PokerRound>,
}

impl PokerNightEvent {
    fn new(event_logs: Vec<String>) -> Result<Self> {
        let mut round_logs = Vec::new();
        let mut idx = 0;
        while idx < event_logs.len() {
            if START_HAND_REGEX.is_match(&event_logs[idx]) {
                let start_idx = idx;
                let mut end_idx = start_idx + 1;
                while end_idx < event_logs.len() && !START_HAND_REGEX.is_match(&event_logs[end_idx]) {
                    end_idx += 1;
                }
                round_logs.push(event_logs[start_idx..end_idx].to_vec());
                idx = end_idx;
            } else {
                idx += 1;
            }
        }

        let mut rounds = Vec::new();
        for logs in round_logs {
            rounds.push(PokerRound::new(&logs)?);
        }
        Ok(Self { rounds })
    }

    fn player_stack_history(&self) -> IndexMap<String, Vec<(i64, DateTime<Local>)>> {
        let mut player_to_stack_history: IndexMap<String, Vec<(i64, DateTime<Local>)>> =
            IndexMap::new();
        let mut player_buyin_amount: HashMap<String, i64> = HashMap::new();
        let mut player_sitting_at_table: HashMap<String, bool> = HashMap::new();
        let mut player_adjustments: HashMap<String, i64> = HashMap::new();
        let mut player_exit: HashMap<String, PlayerMovement> = HashMap::new();

        for poker_round in &self.rounds {
            for (player, joins_array) in &poker_round.player_game_joins {
                for join in joins_array {
                    if join.time < poker_round.start_time {
                        let mut buyin = player_buyin_amount.get(player).copied().unwrap_or(0) + join.amount;
                        if let Some(previous_exit) = player_exit.get(player) {
                            buyin -= previous_exit.amount;
                        }
                        player_buyin_amount.insert(player.clone(), buyin);
                        player_sitting_at_table.insert(player.clone(), true);
                        player_exit.remove(player);
                    }
                }
            }

            let exited_players: Vec<(String, PlayerMovement)> = player_exit
                .iter()
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect();
            for (player, exit) in exited_players {
                if !player_sitting_at_table.get(&player).copied().unwrap_or(false) {
                    let buyin = player_buyin_amount.get(&player).copied().unwrap_or(0);
                    let exited_profit = exit.amount - buyin;
                    player_to_stack_history
                        .entry(player)
                        .or_default()
                        .push((exited_profit, poker_round.start_time));
                }
            }

            for (player, balance) in &poker_round.player_balances {
                let adjusted_balance = *balance - player_adjustments.get(player).copied().unwrap_or(0);
                let buyin = player_buyin_amount.get(player).copied().unwrap_or(0);
                let profit = adjusted_balance - buyin;
                player_to_stack_history
                    .entry(player.clone())
                    .or_default()
                    .push((profit, poker_round.start_time));
            }

            for player in poker_round.players_sat_down.keys() {
                player_sitting_at_table.insert(player.clone(), true);
                player_exit.remove(player);
            }

            for (player, joins_array) in &poker_round.player_game_joins {
                for join in joins_array {
                    if join.time >= poker_round.start_time
                        && !player_sitting_at_table.get(player).copied().unwrap_or(false)
                    {
                        player_sitting_at_table.insert(player.clone(), true);
                        let mut buyin = player_buyin_amount.get(player).copied().unwrap_or(0) + join.amount;
                        if let Some(previous_exit) = player_exit.get(player) {
                            buyin -= previous_exit.amount;
                        }
                        player_buyin_amount.insert(player.clone(), buyin);
                        player_exit.remove(player);
                    }
                }
            }

            for player in poker_round.players_stood_up.keys() {
                player_sitting_at_table.insert(player.clone(), false);
            }

            for (player, exit) in &poker_round.players_exited {
                player_sitting_at_table.insert(player.clone(), false);
                player_exit.insert(player.clone(), exit.clone());
            }

            for (player, amount) in &poker_round.admin_adjustments {
                let updated = player_adjustments.get(player).copied().unwrap_or(0) + amount;
                player_adjustments.insert(player.clone(), updated);
            }
        }

        player_to_stack_history
    }
}

fn from_unix_time(unix_time: &str) -> Result<DateTime<Local>> {
    let raw: i64 = unix_time
        .trim()
        .parse()
        .with_context(|| format!("invalid unix timestamp: {unix_time}"))?;
    let seconds = raw / 100_000;
    let nanos = ((raw % 100_000).abs() as u32) * 10_000;
    let dt = Local
        .timestamp_opt(seconds, nanos)
        .single()
        .ok_or_else(|| anyhow!("invalid local timestamp from {unix_time}"))?;
    Ok(dt)
}

fn clean_player_name(name: &str) -> String {
    name.trim_matches('"').to_string()
}

fn py_datetime(dt: DateTime<Local>) -> String {
    dt.format("%Y-%m-%d %H:%M:%S%.6f").to_string()
}

fn py_list_str(values: &[String]) -> String {
    let body = values
        .iter()
        .map(|s| format!("'{s}'"))
        .collect::<Vec<_>>()
        .join(", ");
    format!("[{body}]")
}

fn py_list_i64(values: &[i64]) -> String {
    let body = values
        .iter()
        .map(ToString::to_string)
        .collect::<Vec<_>>()
        .join(", ");
    format!("[{body}]")
}

fn py_map_player_cards(order: &[String], cards: &HashMap<String, String>) -> String {
    let mut parts = Vec::new();
    for player in order {
        if let Some(hand) = cards.get(player) {
            parts.push(format!("'{player}': '{hand}'"));
        }
    }
    format!("{{{}}}", parts.join(", "))
}

fn py_money(cents: i64) -> String {
    let mut s = format!("{:.2}", cents as f64 / 100.0);
    while s.ends_with('0') {
        s.pop();
    }
    if s.ends_with('.') {
        s.push('0');
    }
    s
}

fn floor_to_multiple(value: i64, step: i64) -> i64 {
    value.div_euclid(step) * step
}

fn ceil_to_multiple(value: i64, step: i64) -> i64 {
    let rem = value.rem_euclid(step);
    if rem == 0 { value } else { value + (step - rem) }
}

fn date_of_csv(csv_name: &str) -> Result<NaiveDate> {
    let base_name = Path::new(csv_name)
        .file_name()
        .and_then(|s| s.to_str())
        .ok_or_else(|| anyhow!("could not parse file name"))?;
    let date_regex = Regex::new(r"(\d{8})").unwrap();
    let caps = date_regex
        .captures(base_name)
        .ok_or_else(|| anyhow!("Could not find YYYYMMDD date in csv filename: {base_name}"))?;
    Ok(NaiveDate::parse_from_str(&caps[1], "%Y%m%d")?)
}

fn normalize_csv_path(date_arg: &str) -> String {
    if date_arg.ends_with(".csv") {
        return date_arg.to_string();
    }
    if date_arg.starts_with("logs/") {
        return format!("{date_arg}.csv");
    }
    if date_arg.starts_with("poker_night_") {
        return format!("logs/{date_arg}.csv");
    }
    format!("logs/poker_night_{date_arg}.csv")
}

fn is_elusive_greg(name: &str) -> bool {
    let allowed = "george";
    name.to_lowercase().chars().all(|c| allowed.contains(c))
}

fn fix_up_player_names(log_lines: Vec<String>) -> Result<Vec<String>> {
    let mut normalized = Vec::with_capacity(log_lines.len());

    for mut line in log_lines {
        let names: Vec<String> = PLAYER_NAME_REGEX
            .captures_iter(&line)
            .map(|caps| caps[1].to_string())
            .collect();

        for name in names {
            let key = name.to_lowercase();
            let normalized_name = if let Some(mapped) = KNOWN_NAME_FIX_UPS.get(key.as_str()) {
                (*mapped).to_string()
            } else if is_elusive_greg(&name) {
                "George".to_string()
            } else {
                bail!(
                    "Not sure if this person is already normalized: {name}. Add them to KNOWN_NAME_FIX_UPS and run again"
                );
            };

            let existing_double = format!("\"\"{} @ ", name);
            let replacement_double = format!("\"\"{} @ ", normalized_name);
            let existing_triple = format!("\"\"\"{} @ ", name);
            let replacement_triple = format!("\"\"\"{} @ ", normalized_name);
            line = line.replace(&existing_triple, &replacement_triple);
            line = line.replace(&existing_double, &replacement_double);
        }

        normalized.push(line);
    }

    Ok(normalized)
}

fn read_and_prepare_logs(path: &str) -> Result<Vec<String>> {
    let content = fs::read_to_string(path).with_context(|| format!("failed reading {path}"))?;
    let raw_lines: Vec<String> = content.lines().map(ToString::to_string).collect();
    let mut logs = fix_up_player_names(raw_lines)?;
    if logs.first().map(|l| l == "entry,at,order").unwrap_or(false) {
        logs.remove(0);
    }
    logs.reverse();
    Ok(logs)
}

fn graph_stack_history(
    player_history: &IndexMap<String, Vec<(i64, DateTime<Local>)>>,
    title: &str,
    last_file: &str,
    show_event_points: bool,
) -> Result<()> {
    if player_history.is_empty() {
        println!("No player history to graph");
        return Ok(());
    }

    let names: Vec<&String> = player_history.keys().collect();

    let (mut x_min, mut x_max) = player_history
        .values()
        .flat_map(|v| v.iter().map(|(_, time)| time.timestamp()))
        .fold((i64::MAX, i64::MIN), |(min_v, max_v), v| {
            (min_v.min(v), max_v.max(v))
        });

    if x_min == x_max {
        x_min -= 1;
        x_max += 1;
    }
    let x_step_raw = ((x_max - x_min) as f64 / 12.0).ceil() as i64;
    let x_major_step = x_step_raw.max(1);
    let x_minor_step = (x_major_step / 5).max(1);
    x_max += x_minor_step;

    let (mut y_min, mut y_max) = player_history
        .values()
        .flat_map(|v| v.iter().map(|(profit, _)| *profit))
        .fold((i64::MAX, i64::MIN), |(min_v, max_v), v| {
            (min_v.min(v), max_v.max(v))
        });

    if y_min == y_max {
        y_min -= 100;
        y_max += 100;
    }
    y_min = y_min.min(0);
    y_max = y_max.max(0);
    let raw_step = ((y_max - y_min) as f64 / 8.0).ceil() as i64;
    let y_step = raw_step.max(5);
    let y_step = ceil_to_multiple(y_step, 5);
    let y_minor_step = (y_step / 5).max(1);
    y_min = floor_to_multiple(y_min, y_step) - y_minor_step;
    y_max = ceil_to_multiple(y_max, y_step) + y_minor_step;
    let y_label_count = (((y_max - y_min) / y_step) + 1).clamp(6, 14) as usize;

    let mut file_name = if show_event_points {
        format!("{}_all_time_profit_graph.png", last_file.trim_end_matches(".csv"))
    } else {
        format!("{}_profit_graph.png", last_file.trim_end_matches(".csv"))
    };
    file_name = file_name.replacen("logs", "graphs", 1);

    if let Some(parent) = Path::new(&file_name).parent() {
        if !parent.as_os_str().is_empty() {
            fs::create_dir_all(parent)?;
        }
    }

    let root = BitMapBackend::new(&file_name, (1920, 1080)).into_drawing_area();
    root.fill(&WHITE)?;

    let mut chart = ChartBuilder::on(&root)
        .caption(title, ("sans-serif", 56))
        .margin(20)
        .x_label_area_size(120)
        .y_label_area_size(110)
        .build_cartesian_2d(x_min..x_max, y_min..y_max)?;

    chart
        .configure_mesh()
        .x_desc("Time")
        .y_desc("Profit in cents")
        .x_labels(12)
        .y_labels(y_label_count)
        .label_style(("sans-serif", 32))
        .x_label_style(("sans-serif", 24))
        .y_label_style(("sans-serif", 26))
        .axis_desc_style(("sans-serif", 38))
        .light_line_style(TRANSPARENT)
        .bold_line_style(BLACK.mix(0.18))
        .x_label_formatter(&|x| {
            Local
                .timestamp_opt(*x, 0)
                .single()
                .map(|d| d.format("%H:%M").to_string())
                .unwrap_or_else(|| x.to_string())
        })
        .draw()?;

    let x_range = (x_max - x_min).max(1);
    let y_range = (y_max - y_min).max(1);
    let left_x_threshold = x_min + x_range / 3;
    let upper_y_threshold = y_max - y_range / 3;
    let lower_y_threshold = y_min + y_range / 3;
    let mut upper_left_points = 0usize;
    let mut lower_left_points = 0usize;

    for (idx, player) in names.iter().enumerate() {
        let history = &player_history[*player];
        let points: Vec<(i64, i64)> = history
            .iter()
            .map(|(profit, time)| (time.timestamp(), *profit))
            .collect();
        for (x, y) in &points {
            if *x <= left_x_threshold {
                if *y >= upper_y_threshold {
                    upper_left_points += 1;
                }
                if *y <= lower_y_threshold {
                    lower_left_points += 1;
                }
            }
        }
        let color = Palette99::pick(idx);
        let last_profit = history.last().map(|p| p.0).unwrap_or(0);

        chart
            .draw_series(LineSeries::new(
                points.clone(),
                ShapeStyle::from(&color).stroke_width(4),
            ))?
            .label(format!("{}: ${:.2}", player, last_profit as f64 / 100.0))
            .legend({
                let legend_color = Palette99::pick(idx);
                move |(x, y)| {
                    PathElement::new(
                        vec![(x, y), (x + 30, y)],
                        ShapeStyle::from(&legend_color).stroke_width(4),
                    )
                }
            });

        if show_event_points {
            chart.draw_series(points.into_iter().map(|p| Circle::new(p, 4, color.filled())))?;
        }
    }

    chart.plotting_area().draw(&Rectangle::new(
        [(x_min, y_min), (x_max, y_max)],
        ShapeStyle::from(&BLACK).stroke_width(2),
    ))?;
    let legend_position = if upper_left_points <= lower_left_points {
        SeriesLabelPosition::UpperLeft
    } else {
        SeriesLabelPosition::LowerLeft
    };

    chart
        .configure_series_labels()
        .position(legend_position)
        .label_font(("sans-serif", 30))
        .background_style(WHITE.mix(0.8))
        .border_style(BLACK)
        .draw()?;

    root.present()?;
    Ok(())
}

fn print_splitwise_instructions(player_history: &IndexMap<String, Vec<(i64, DateTime<Local>)>>) {
    let mut splitwise_sum = 0i64;
    let mut checksum = 0i64;

    for entries in player_history.values() {
        if let Some((last_profit, _)) = entries.last() {
            checksum += last_profit;
            if *last_profit > 0 {
                splitwise_sum += last_profit;
            }
        }
    }

    println!("{}", "=".repeat(20));
    println!("=== Splitwise Instructions ===");
    if checksum != 0 {
        println!("Uh oh! We summed the profits, expected zero, but got {checksum}. This means the script has a bug and parsed the records wrong. You should calculate the profits from a screenshot of the last hand for player chip counts.");
        println!("{}", "=".repeat(20));
        return;
    }

    println!("1. Open Splitwise to the current season groups and create a new expense.");
    println!("2. For the title add the current date");
    println!("3. For the total, enter {}", py_money(splitwise_sum));
    println!("4. For the Paid By Section, select multiple people and enter per person:");

    for (player, entry) in player_history {
        let Some((amount, _)) = entry.last() else {
            continue;
        };
        if *amount > 0 {
            println!(" {player}: {}", py_money(*amount));
        }
    }

    println!("5. For the Split By / Owes Section, enter per person:");
    for (player, entry) in player_history {
        let Some((amount, _)) = entry.last() else {
            continue;
        };
        if *amount > 0 {
            continue;
        }
        println!(" {player}: {}", py_money(-*amount));
    }

    println!("{}", "=".repeat(20));
}

fn poker_round_for_timestamp<'a>(rounds: &'a [PokerRound], time: DateTime<Local>) -> Option<&'a PokerRound> {
    rounds
        .iter()
        .find(|round| round.start_time <= time && round.end_time >= time)
}

fn most_wins(rounds: &[PokerRound]) -> Option<(String, Vec<PokerRound>, HashMap<String, Vec<PokerRound>>)> {
    let mut player_wins: HashMap<String, Vec<PokerRound>> = HashMap::new();
    for round in rounds {
        for player in &round.winning_players {
            player_wins.entry(player.clone()).or_default().push(round.clone());
        }
    }

    let (winner, winning_rounds) = player_wins
        .iter()
        .max_by_key(|(_, wins)| wins.len())
        .map(|(p, r)| (p.clone(), r.clone()))?;

    Some((winner, winning_rounds, player_wins))
}

fn biggest_win(rounds: &[PokerRound]) -> Option<PokerRound> {
    let mut biggest_amount = i64::MIN;
    let mut biggest_round = None;
    for round in rounds {
        for amount in &round.winning_amounts {
            if *amount > biggest_amount {
                biggest_amount = *amount;
                biggest_round = Some(round.clone());
            }
        }
    }
    biggest_round
}

fn rounds_played_by_players(rounds: &[PokerRound]) -> HashMap<String, usize> {
    let mut counts = HashMap::new();
    for round in rounds {
        for player in round.player_balances.keys() {
            *counts.entry(player.clone()).or_insert(0) += 1;
        }
    }
    counts
}

fn largest_raise_or_bet_for_round_actions(round_actions: &[PlayerRoundAction]) -> Option<PlayerRoundAction> {
    round_actions
        .iter()
        .filter(|a| a.action_type == RoundAction::Raises || a.action_type == RoundAction::Bets)
        .max_by_key(|a| a.amount)
        .cloned()
}

fn number_of_folds_per_player(round_actions: &[PlayerRoundAction]) -> IndexMap<String, usize> {
    let mut folds = IndexMap::new();
    for action in round_actions {
        if action.action_type == RoundAction::Folds {
            *folds.entry(action.player.clone()).or_insert(0) += 1;
        }
    }
    folds
}

fn all_ins_per_player(round_actions: &[PlayerRoundAction]) -> IndexMap<String, Vec<PlayerRoundAction>> {
    let mut all_ins: IndexMap<String, Vec<PlayerRoundAction>> = IndexMap::new();
    for action in round_actions {
        if action.all_in {
            all_ins.entry(action.player.clone()).or_default().push(action.clone());
        }
    }
    all_ins
}

fn player_wins_for_round_actions(
    all_rounds: &[PokerRound],
    player_to_round_actions: &IndexMap<String, Vec<PlayerRoundAction>>,
) -> HashMap<String, Vec<PokerRound>> {
    let mut out: HashMap<String, Vec<PokerRound>> = HashMap::new();
    for (player, actions) in player_to_round_actions {
        for action in actions {
            if let Some(round) = poker_round_for_timestamp(all_rounds, action.time) {
                if round.winning_players.contains(player) {
                    out.entry(player.clone()).or_default().push(round.clone());
                }
            }
        }
    }
    out
}

fn gentleman_scores_by_player(rounds: &[PokerRound]) -> IndexMap<String, usize> {
    let mut scores = IndexMap::new();
    for round in rounds.iter().filter(|r| r.winning_hands.is_empty()) {
        for winning_player in &round.winning_players {
            if round.player_to_hand.contains_key(winning_player) {
                *scores.entry(winning_player.clone()).or_insert(0) += 1;
            }
        }
    }
    scores
}

fn winning_hand_types_by_player(rounds: &[PokerRound]) -> (HashMap<String, HashMap<String, usize>>, usize) {
    let mut player_to_hand_type_to_wins: HashMap<String, HashMap<String, usize>> = HashMap::new();
    let mut player_order = Vec::new();
    let mut hands_with_known_win = 0usize;

    for round in rounds {
        for (i, winning_player) in round.winning_players.iter().enumerate() {
            if round.winning_hands.is_empty() || i >= round.winning_hands.len() {
                continue;
            }

            hands_with_known_win += 1;
            let hand = &round.winning_hands[i];
            let prefix = hand.split("(combination:").next().unwrap_or(hand);
            let mut hand_type = prefix.split(',').next().unwrap_or(prefix).trim().to_string();
            if hand_type.contains("High") {
                hand_type = "High Card".to_string();
            }
            if !player_to_hand_type_to_wins.contains_key(winning_player) {
                player_order.push(winning_player.clone());
            }
            *player_to_hand_type_to_wins
                .entry(winning_player.clone())
                .or_default()
                .entry(hand_type)
                .or_insert(0) += 1;
        }
    }

    let hand_display_sort_order = HashMap::from([
        ("High Card", 0),
        ("Pair", 1),
        ("Two Pair", 2),
        ("Three of a Kind", 3),
        ("Straight", 4),
        ("Flush", 5),
        ("Full House", 6),
        ("Four of a Kind", 7),
        ("Straight Flush", 8),
        ("Royal Flush", 9),
    ]);

    for player in player_order {
        let Some(counts) = player_to_hand_type_to_wins.get(&player) else {
            continue;
        };
        println!("{player}:");
        let mut sorted: Vec<(&String, &usize)> = counts.iter().collect();
        sorted.sort_by(|(h1, _), (h2, _)| {
            let r1 = hand_display_sort_order.get(h1.trim()).copied().unwrap_or(999);
            let r2 = hand_display_sort_order.get(h2.trim()).copied().unwrap_or(999);
            r1.cmp(&r2)
        });

        for (hand, count) in sorted {
            let pct = if hands_with_known_win > 0 {
                *count as f64 / hands_with_known_win as f64 * 100.0
            } else {
                0.0
            };
            println!("  {hand}: {count}/{hands_with_known_win} ({pct:.2}%)");
        }
    }

    (player_to_hand_type_to_wins, hands_with_known_win)
}

fn print_fold_stats(title: &str, data: &[(String, usize)], rounds_per_player: &HashMap<String, usize>) {
    println!("--- {title}");
    for (player, fold_count) in data {
        if let Some(num_rounds) = rounds_per_player.get(player) {
            let pct = *fold_count as f64 / *num_rounds as f64 * 100.0;
            println!("{player: >10} {fold_count: >10} ({pct:.2}%)");
        }
    }
}

fn print_all_in_stats(
    title: &str,
    data: &[(String, Vec<PlayerRoundAction>)],
    rounds_per_player: &HashMap<String, usize>,
    wins_for_all_ins: &HashMap<String, Vec<PokerRound>>,
) {
    if data.is_empty() {
        return;
    }
    println!("--- {title}");
    for (player, all_ins) in data {
        let rounds = rounds_per_player.get(player).copied().unwrap_or(1);
        let all_in_pct = all_ins.len() as f64 / rounds as f64 * 100.0;
        let wins = wins_for_all_ins.get(player).map(|v| v.len()).unwrap_or(0);
        let win_pct = if all_ins.is_empty() {
            0.0
        } else {
            wins as f64 / all_ins.len() as f64 * 100.0
        };
        println!("{player: >10} {: >10} {all_in_pct:.2}% (Won {win_pct:.2}%)", all_ins.len());
    }
}

fn print_core_stats(rounds: &[PokerRound]) {
    if rounds.is_empty() {
        println!("No rounds found");
        return;
    }

    let num_rounds_with_player = rounds_played_by_players(rounds);
    let Some((most_wins_player, winning_rounds, mut all_player_wins)) = most_wins(rounds) else {
        println!("No winning rounds found");
        return;
    };

    let mut all_player_wins_list: Vec<(String, Vec<PokerRound>)> = all_player_wins.drain().collect();
    all_player_wins_list.sort_by(|a, b| {
        b.1.len()
            .cmp(&a.1.len())
            .then_with(|| {
                let a_first = a.1.first().map(|r| r.start_time.timestamp_micros()).unwrap_or(i64::MAX);
                let b_first = b.1.first().map(|r| r.start_time.timestamp_micros()).unwrap_or(i64::MAX);
                a_first.cmp(&b_first)
            })
    });

    println!("\n------- Winning Hands of Hands Played");
    if let Some(played) = num_rounds_with_player.get(&most_wins_player).copied() {
        let pct = winning_rounds.len() as f64 / played as f64 * 100.0;
        println!(
            "{} won the most rounds at {} rounds out of {} played rounds ({pct:.2}%).\n",
            most_wins_player,
            winning_rounds.len(),
            played
        );
    }

    for (player, rounds_won) in &all_player_wins_list {
        if let Some(played) = num_rounds_with_player.get(player).copied() {
            let pct = rounds_won.len() as f64 / played as f64 * 100.0;
            println!("{player} won {}/{played} ({pct:.2}%)", rounds_won.len());
        }
    }

    if let Some(biggest_win_round) = biggest_win(rounds) {
        println!("\n------- Biggest Winning Hand");
        println!(
            "{} won the most at {} on {}.\nTable cards: {}.\nWinning hands: {}.\nAll player's cards: {}",
            biggest_win_round.winning_players.join(", "),
            py_list_i64(&biggest_win_round.winning_amounts),
            py_datetime(biggest_win_round.start_time),
            py_list_str(&biggest_win_round.table_cards),
            biggest_win_round.winning_hands.join(", "),
            py_map_player_cards(
                &biggest_win_round.player_to_hand_order,
                &biggest_win_round.player_to_hand
            )
        );
    }

    let mut gent_scores_by_player: Vec<(String, usize)> = gentleman_scores_by_player(rounds).into_iter().collect();
    gent_scores_by_player.sort_by(|a, b| b.1.cmp(&a.1));

    println!("\n------- Gentleman Scores (Showing Hidden Hand After Win)");
    for (player, score) in &gent_scores_by_player {
        let wins = all_player_wins_list
            .iter()
            .find(|(p, _)| p == player)
            .map(|(_, rounds)| rounds.len())
            .unwrap_or(1);
        let pct = *score as f64 / wins as f64 * 100.0;
        println!("{player: >10} {score: >5} ({pct:.2}%)");
    }

    let mut all_pre_flop_actions = Vec::new();
    let mut all_pre_turn_actions = Vec::new();
    let mut all_pre_river_actions = Vec::new();
    let mut all_post_river_actions = Vec::new();

    for round in rounds {
        all_pre_flop_actions.extend(round.pre_flop_actions());
        all_pre_turn_actions.extend(round.pre_turn_actions());
        all_pre_river_actions.extend(round.pre_river_actions());
        all_post_river_actions.extend(round.post_river_actions());
    }

    println!("\n------- Biggest Raises/Bets");
    let biggest_raise_pre_turn = largest_raise_or_bet_for_round_actions(&all_pre_turn_actions);
    let biggest_raise_pre_river = largest_raise_or_bet_for_round_actions(&all_pre_river_actions);
    let biggest_raise_post_river = largest_raise_or_bet_for_round_actions(&all_post_river_actions);

    if let Some(action) = biggest_raise_pre_turn {
        println!("--- Pre-turn");
        println!("{}", action.to_string());
        if let Some(round) = poker_round_for_timestamp(rounds, action.time) {
            println!(
                "  {} won {} this round\n  Table cards: {}\n  Winning hands: {}\n  All player's cards: {}",
                round.winning_players.join(", "),
                py_list_i64(&round.winning_amounts),
                py_list_str(&round.table_cards),
                round.winning_hands.join(", "),
                py_map_player_cards(&round.player_to_hand_order, &round.player_to_hand)
            );
        }
    }

    if let Some(action) = biggest_raise_pre_river {
        println!("--- Pre-river");
        println!("{}", action.to_string());
        if let Some(round) = poker_round_for_timestamp(rounds, action.time) {
            println!(
                "  {} won {} this round\n  Table cards: {}\n  Winning hands: {}\n  All player's cards: {}",
                round.winning_players.join(", "),
                py_list_i64(&round.winning_amounts),
                py_list_str(&round.table_cards),
                round.winning_hands.join(", "),
                py_map_player_cards(&round.player_to_hand_order, &round.player_to_hand)
            );
        }
    }

    if let Some(action) = biggest_raise_post_river {
        println!("--- Post-river");
        println!("{}", action.to_string());
        if let Some(round) = poker_round_for_timestamp(rounds, action.time) {
            println!(
                "  {} won {} this round\n  Table cards: {}\n  Winning hands: {}\n  All player's cards: {}",
                round.winning_players.join(", "),
                py_list_i64(&round.winning_amounts),
                py_list_str(&round.table_cards),
                round.winning_hands.join(", "),
                py_map_player_cards(&round.player_to_hand_order, &round.player_to_hand)
            );
        }
    }

    let mut player_pre_flop_folds: Vec<(String, usize)> = number_of_folds_per_player(&all_pre_flop_actions).into_iter().collect();
    let mut player_pre_turn_folds: Vec<(String, usize)> = number_of_folds_per_player(&all_pre_turn_actions).into_iter().collect();
    let mut player_pre_river_folds: Vec<(String, usize)> = number_of_folds_per_player(&all_pre_river_actions).into_iter().collect();
    let mut player_post_river_folds: Vec<(String, usize)> = number_of_folds_per_player(&all_post_river_actions).into_iter().collect();
    player_pre_flop_folds.sort_by(|a, b| b.1.cmp(&a.1));
    player_pre_turn_folds.sort_by(|a, b| b.1.cmp(&a.1));
    player_pre_river_folds.sort_by(|a, b| b.1.cmp(&a.1));
    player_post_river_folds.sort_by(|a, b| b.1.cmp(&a.1));

    println!("\n------- Folds Per Player of Hands Played");
    print_fold_stats("Pre-flop", &player_pre_flop_folds, &num_rounds_with_player);
    print_fold_stats("Pre-turn", &player_pre_turn_folds, &num_rounds_with_player);
    print_fold_stats("Pre-river", &player_pre_river_folds, &num_rounds_with_player);
    print_fold_stats("Post-river", &player_post_river_folds, &num_rounds_with_player);

    let mut player_pre_flop_all_ins: Vec<(String, Vec<PlayerRoundAction>)> =
        all_ins_per_player(&all_pre_flop_actions).into_iter().collect();
    let mut player_pre_turn_all_ins: Vec<(String, Vec<PlayerRoundAction>)> =
        all_ins_per_player(&all_pre_turn_actions).into_iter().collect();
    let mut player_pre_river_all_ins: Vec<(String, Vec<PlayerRoundAction>)> =
        all_ins_per_player(&all_pre_river_actions).into_iter().collect();
    let mut player_post_river_all_ins: Vec<(String, Vec<PlayerRoundAction>)> =
        all_ins_per_player(&all_post_river_actions).into_iter().collect();

    player_pre_flop_all_ins.sort_by(|a, b| b.1.len().cmp(&a.1.len()));
    player_pre_turn_all_ins.sort_by(|a, b| b.1.len().cmp(&a.1.len()));
    player_pre_river_all_ins.sort_by(|a, b| b.1.len().cmp(&a.1.len()));
    player_post_river_all_ins.sort_by(|a, b| b.1.len().cmp(&a.1.len()));

    let wins_for_pre_flop = player_wins_for_round_actions(rounds, &all_ins_per_player(&all_pre_flop_actions));
    let wins_for_pre_turn = player_wins_for_round_actions(rounds, &all_ins_per_player(&all_pre_turn_actions));
    let wins_for_pre_river = player_wins_for_round_actions(rounds, &all_ins_per_player(&all_pre_river_actions));
    let wins_for_post_river = player_wins_for_round_actions(rounds, &all_ins_per_player(&all_post_river_actions));

    println!("\n------- All-ins Per Player of Hands Played");
    print_all_in_stats(
        "Pre-flop",
        &player_pre_flop_all_ins,
        &num_rounds_with_player,
        &wins_for_pre_flop,
    );
    print_all_in_stats(
        "Pre-turn",
        &player_pre_turn_all_ins,
        &num_rounds_with_player,
        &wins_for_pre_turn,
    );
    print_all_in_stats(
        "Pre-river",
        &player_pre_river_all_ins,
        &num_rounds_with_player,
        &wins_for_pre_river,
    );
    print_all_in_stats(
        "Post-river",
        &player_post_river_all_ins,
        &num_rounds_with_player,
        &wins_for_post_river,
    );

    println!("\n------- Winning Hand Breakdown By Player");
    let _ = winning_hand_types_by_player(rounds);
}

#[derive(Parser, Debug)]
#[command(version, about = "Poker night graphing and stats in Rust")]
struct Args {
    #[arg(short, long, help = "graph all csvs in one chart")]
    all: bool,

    #[arg(
        short,
        long,
        help = "graph the logs/poker_night_YYYYMMDD.csv on a chart",
        default_value = CSV_FILE
    )]
    date: String,
}

fn run() -> Result<()> {
    let args = Args::parse();

    if args.all {
        println!("Graphing all csvs in single chart");
        let mut csv_files: Vec<String> = fs::read_dir("logs")?
            .filter_map(|entry| entry.ok())
            .filter_map(|entry| {
                let path = entry.path();
                if path.is_file() && path.extension().and_then(|x| x.to_str()) == Some("csv") {
                    path.file_name()
                        .and_then(|n| n.to_str())
                        .map(ToString::to_string)
                } else {
                    None
                }
            })
            .collect();
        csv_files.sort();

        let last_csv = csv_files.last().ok_or_else(|| anyhow!("no csv files in logs/"))?;
        let event_date = date_of_csv(last_csv)?.format("%Y/%m/%d").to_string();

        let mut all_player_history: IndexMap<String, Vec<(i64, DateTime<Local>)>> = IndexMap::new();
        let mut all_poker_rounds = Vec::new();

        for filename in &csv_files {
            let full_path = format!("logs/{filename}");
            let logs = read_and_prepare_logs(&full_path)?;
            let event = PokerNightEvent::new(logs)?;
            all_poker_rounds.extend(event.rounds.clone());

            let player_history = event.player_stack_history();
            for (player, current_event_stack) in player_history {
                let Some(last_event_point) = current_event_stack.last().copied() else {
                    continue;
                };

                if let Some(existing) = all_player_history.get_mut(&player) {
                    let last_profit = existing.last().map(|(p, _)| *p).unwrap_or(0);
                    existing.push((last_event_point.0 + last_profit, last_event_point.1));
                } else {
                    all_player_history.insert(player, vec![last_event_point]);
                }
            }
        }

        print_core_stats(&all_poker_rounds);
        graph_stack_history(
            &all_player_history,
            &format!("All-time profit history as of {event_date}"),
            last_csv,
            true,
        )?;
    } else {
        let csv_file = normalize_csv_path(&args.date);
        println!("Graphing single csv {csv_file}");
        let event_date = date_of_csv(&csv_file)?.format("%Y/%m/%d").to_string();

        let logs = read_and_prepare_logs(&csv_file)?;
        let event = PokerNightEvent::new(logs)?;
        let player_history = event.player_stack_history();

        print_core_stats(&event.rounds);
        print_splitwise_instructions(&player_history);
        graph_stack_history(&player_history, &format!("Profit for {event_date}"), &csv_file, false)?;
    }

    Ok(())
}

fn main() {
    if let Err(err) = run() {
        eprintln!("Error: {err:#}");
        std::process::exit(1);
    }
}
