//! Read a PokerLater session log (the "Download JSON log" file) as PokerNow log lines.
//!
//! The Rust twin of `pokerlater_log.py`; see that file for the full story. In
//! short: the export becomes the exact `entry,at,order` CSV lines PokerNow
//! writes, so a PokerLater night runs through the same parser as a PokerNow
//! one. Buy-ins, rebuys and host corrections are inferred from stacks between
//! hands, the night's final result comes from the export's balanced ledger, and
//! winning hand names are computed from the shown cards and the board.

use anyhow::{anyhow, bail, Result};
use chrono::{TimeZone, Utc};
use serde_json::Value;
use std::collections::{HashMap, HashSet};

const FORMAT: &str = "pokerlater-session-log";
const CATEGORY_NAMES: [&str; 9] = [
    "High Card",
    "Pair",
    "Two Pair",
    "Three of a Kind",
    "Straight",
    "Flush",
    "Full House",
    "Four of a Kind",
    "Straight Flush",
];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Card {
    pub rank: u8,
    pub suit: u8,
}

pub fn parse_export(text: &str) -> Result<Value> {
    let data: Value = serde_json::from_str(text)?;
    if data["format"].as_str() != Some(FORMAT) {
        bail!("Not a PokerLater session log (format is not {FORMAT})");
    }
    if data["version"].as_i64() != Some(1) {
        bail!("Unsupported PokerLater session log version {}", data["version"]);
    }
    Ok(data)
}

fn rank_text(rank: u8) -> String {
    match rank {
        11 => "J".to_string(),
        12 => "Q".to_string(),
        13 => "K".to_string(),
        14 => "A".to_string(),
        _ => rank.to_string(),
    }
}

pub fn card_text(card: Card) -> String {
    let suit = match card.suit {
        0 => '♣',
        1 => '♦',
        2 => '♥',
        _ => '♠',
    };
    format!("{}{suit}", rank_text(card.rank))
}

pub fn cards_text(cards: &[Card]) -> String {
    cards.iter().map(|c| card_text(*c)).collect::<Vec<_>>().join(", ")
}

fn cards_of(value: &Value) -> Vec<Card> {
    value
        .as_array()
        .map(|cards| {
            cards
                .iter()
                .map(|c| Card {
                    rank: c["rank"].as_u64().unwrap_or(0) as u8,
                    suit: c["suit"].as_u64().unwrap_or(0) as u8,
                })
                .collect()
        })
        .unwrap_or_default()
}

/// (category, tiebreaks) for exactly five cards; larger is better.
fn five_card_rank(cards: &[Card]) -> (u8, Vec<u8>) {
    let mut ranks: Vec<u8> = cards.iter().map(|c| c.rank).collect();
    ranks.sort_unstable_by(|a, b| b.cmp(a));
    let mut counts: HashMap<u8, u8> = HashMap::new();
    for rank in &ranks {
        *counts.entry(*rank).or_insert(0) += 1;
    }
    let mut by_count: Vec<(u8, u8)> = counts.into_iter().collect();
    by_count.sort_unstable_by_key(|g| std::cmp::Reverse((g.1, g.0)));
    let flush = cards.iter().map(|c| c.suit).collect::<HashSet<_>>().len() == 1;
    let mut distinct: Vec<u8> = ranks.clone();
    distinct.dedup();
    let straight_high = if distinct.len() == 5 {
        if distinct[0] - distinct[4] == 4 {
            Some(distinct[0])
        } else if distinct == [14, 5, 4, 3, 2] {
            Some(5)
        } else {
            None
        }
    } else {
        None
    };
    let grouped: Vec<u8> = by_count.iter().map(|(rank, _)| *rank).collect();
    match (straight_high, flush, by_count[0].1, by_count.get(1).map(|g| g.1)) {
        (Some(high), true, _, _) => (8, vec![high]),
        (_, _, 4, _) => (7, grouped),
        (_, _, 3, Some(2)) => (6, grouped),
        (_, true, _, _) => (5, ranks),
        (Some(high), _, _, _) => (4, vec![high]),
        (_, _, 3, _) => (3, grouped),
        (_, _, 2, Some(2)) => (2, grouped),
        (_, _, 2, _) => (1, grouped),
        _ => (0, ranks),
    }
}

/// The best five of up to seven cards: (category, tiebreaks, five cards).
pub fn best_hand(cards: &[Card]) -> (u8, Vec<u8>, Vec<Card>) {
    let n = cards.len();
    let mut best: Option<(u8, Vec<u8>, Vec<Card>)> = None;
    for a in 0..n {
        for b in a + 1..n {
            for c in b + 1..n {
                for d in c + 1..n {
                    for e in d + 1..n {
                        let five = vec![cards[a], cards[b], cards[c], cards[d], cards[e]];
                        let (category, tiebreaks) = five_card_rank(&five);
                        let better = match &best {
                            None => true,
                            Some((bc, bt, _)) => (category, &tiebreaks) > (*bc, bt),
                        };
                        if better {
                            best = Some((category, tiebreaks, five));
                        }
                    }
                }
            }
        }
    }
    best.unwrap_or((0, Vec::new(), Vec::new()))
}

/// PokerNow's winning-hand text: "Flush (combination: ...)", "A High (...)".
pub fn hand_description(cards: &[Card]) -> String {
    let (category, tiebreaks, mut five) = best_hand(cards);
    let name = if category == 0 {
        format!("{} High", rank_text(tiebreaks.first().copied().unwrap_or(0)))
    } else if category == 8 && tiebreaks.first() == Some(&14) {
        "Royal Flush".to_string()
    } else {
        CATEGORY_NAMES[category as usize].to_string()
    };
    five.sort_by_key(|c| std::cmp::Reverse(c.rank));
    format!("{name} (combination: {})", cards_text(&five))
}

fn iso(order: i64) -> String {
    Utc.timestamp_millis_opt(order / 100)
        .single()
        .map(|t| t.format("%Y-%m-%dT%H:%M:%S%.3fZ").to_string())
        .unwrap_or_default()
}

/// Collects entries and stamps PokerNow's `order` column (1e-5 s units),
/// giving entries that share a millisecond consecutive orders in log order.
struct Lines {
    rows: Vec<(String, i64)>,
    last_order: i64,
}

impl Lines {
    fn add(&mut self, entry: String, at_ms: i64) {
        let order = (at_ms * 100).max(self.last_order + 1);
        self.last_order = order;
        self.rows.push((entry, order));
    }
}

/// The export as PokerNow CSV lines, newest first, without the header.
///
/// `normalize_name` maps a display name to the group's canonical name; it is
/// applied to names containing whitespace, which the PokerNow name regex
/// cannot see (the line-based fix-up handles every other name).
pub fn log_lines(data: &Value, normalize_name: &dyn Fn(&str) -> Result<String>) -> Result<Vec<String>> {
    let mut merged_into: HashMap<String, String> = HashMap::new();
    for line in data["ledger"]["lines"].as_array().into_iter().flatten() {
        let into = line["name"].as_str().unwrap_or_default().to_string();
        for merged in line["mergedNames"].as_array().into_iter().flatten() {
            if let Some(merged) = merged.as_str() {
                merged_into.insert(merged.to_string(), into.clone());
            }
        }
    }
    let resolve = |name: &str| -> Result<String> {
        let name = merged_into.get(name).cloned().unwrap_or_else(|| name.to_string());
        if name.chars().any(char::is_whitespace) {
            normalize_name(&name)
        } else {
            Ok(name)
        }
    };

    let mut ids: HashMap<String, String> = HashMap::new();
    let mut player = |name: &str| -> Result<String> {
        let resolved = resolve(name)?;
        let next = format!("PL{}", ids.len() + 1);
        let id = ids.entry(resolved.clone()).or_insert(next).clone();
        Ok(format!("\"{resolved} @ {id}\""))
    };

    let mut out = Lines { rows: Vec::new(), last_order: 0 };
    let mut last_stack: HashMap<String, i64> = HashMap::new();
    let hands: Vec<&Value> = data["hands"]
        .as_array()
        .ok_or_else(|| anyhow!("PokerLater log has no hands list"))?
        .iter()
        .filter(|hand| hand["final"]["phase"].as_str() == Some("complete"))
        .collect();

    for (index, hand) in hands.iter().enumerate() {
        let started = hand["startedAt"].as_i64().unwrap_or(0);
        let hand_no = hand["handNo"].as_i64().unwrap_or(0);
        let final_view = &hand["final"];
        let seats: Vec<(i64, String, i64)> = hand["seats"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|seat| {
                let name = seat["name"].as_str().unwrap_or_default();
                (!name.is_empty()).then(|| {
                    (
                        seat["seat"].as_i64().unwrap_or(0),
                        name.to_string(),
                        seat["startingStack"].as_i64().unwrap_or(0),
                    )
                })
            })
            .collect();
        let seat_name: HashMap<i64, String> =
            seats.iter().map(|(seat, name, _)| (*seat, name.clone())).collect();

        let mut movements = Vec::new();
        for (_, name, stack) in &seats {
            let p = player(name)?;
            match last_stack.get(&resolve(name)?) {
                None => movements.push(format!("The player {p} joined the game with a stack of {stack}.")),
                Some(before) if stack > before => {
                    movements.push(format!("The player {p} rebought. New stack {}.", stack - before))
                }
                Some(before) if stack < before => movements.push(format!(
                    "The admin updated the player {p} stack from {before} to {stack}."
                )),
                _ => {}
            }
        }

        // The first hand's buy-ins go inside it, ahead of the stacks line.
        // Later joins, rebuys and adjustments go at the end of the round
        // before, which the parser books after that round's profits.
        let first_movements = if index == 0 {
            movements
        } else {
            for movement in movements {
                out.add(movement, started - 1);
            }
            Vec::new()
        };

        let dealer = match seat_name.get(&final_view["button"].as_i64().unwrap_or(-1)) {
            Some(name) => format!(" (dealer: {})", player(name)?),
            None => String::new(),
        };
        let hand_id = hand["handId"].as_str().unwrap_or_default();
        out.add(
            format!("-- starting hand #{hand_no} (id: {hand_id})  No Limit Texas Hold'em{dealer} --"),
            started,
        );
        for movement in first_movements {
            out.add(movement, started);
        }
        let mut stacks = Vec::new();
        for (seat, name, stack) in &seats {
            stacks.push(format!("#{} {} ({stack})", seat + 1, player(name)?));
        }
        out.add(format!("Player stacks: {}", stacks.join(" | ")), started);

        let mut shown: HashMap<i64, Vec<Card>> = HashMap::new();
        let mut board: Vec<Card> = Vec::new();
        let mut live: HashSet<i64> = seat_name.keys().copied().collect();
        for entry in final_view["entries"].as_array().into_iter().flatten() {
            let kind = entry["kind"].as_str().unwrap_or_default();
            let at = entry["at"].as_i64().unwrap_or(started);
            let seat = entry["seat"].as_i64();
            let name = seat.and_then(|s| seat_name.get(&s)).cloned();
            match (kind, name) {
                ("forcedBet", Some(name)) => {
                    let what = match entry["forced"].as_str().unwrap_or_default() {
                        "smallBlind" => "posts a small blind of",
                        "bigBlind" => "posts a big blind of",
                        "straddle" => "posts a straddle of",
                        _ => "posts an ante of",
                    };
                    out.add(format!("{} {what} {}", player(&name)?, entry["amount"]), at);
                }
                // `timeout` and `hostForceFold` are markers: the fold or check
                // they caused is logged right after them, so they add no line.
                ("fold", Some(name)) => {
                    live.remove(&seat.unwrap_or(-1));
                    out.add(format!("{} folds", player(&name)?), at);
                }
                ("check", Some(name)) => out.add(format!("{} checks", player(&name)?), at),
                ("call" | "bet" | "raise", Some(name)) => {
                    let verb = match kind {
                        "call" => "calls",
                        "bet" => "bets",
                        _ => "raises to",
                    };
                    let all_in = if entry["allIn"].as_bool() == Some(true) { " and go all in" } else { "" };
                    out.add(format!("{} {verb} {}{all_in}", player(&name)?, entry["to"]), at);
                }
                ("uncalledReturn", Some(name)) => out.add(
                    format!("Uncalled bet of {} returned to {}", entry["amount"], player(&name)?),
                    at,
                ),
                ("dealBoard", _) if entry["boardIndex"].as_i64().unwrap_or(0) == 0 => {
                    let new = cards_of(&entry["cards"]);
                    match entry["street"].as_str().unwrap_or_default() {
                        "flop" => out.add(format!("Flop:  [{}]", cards_text(&new)), at),
                        street => {
                            let label = if street == "turn" { "Turn" } else { "River" };
                            out.add(format!("{label}: {} [{}]", cards_text(&board), cards_text(&new)), at);
                        }
                    }
                    board.extend(new);
                }
                ("showdownReveal", Some(name)) => {
                    let cards = cards_of(&entry["cards"]);
                    out.add(format!("{} shows a {}.", player(&name)?, cards_text(&cards)), at);
                    shown.insert(seat.unwrap_or(-1), cards);
                }
                ("potAward", Some(name)) => {
                    let seat = seat.unwrap_or(-1);
                    let with_hand = match shown.get(&seat) {
                        Some(cards) if live.len() > 1 && board.len() == 5 => {
                            let seven: Vec<Card> = cards.iter().chain(board.iter()).copied().collect();
                            format!(" with {}", hand_description(&seven))
                        }
                        _ => String::new(),
                    };
                    out.add(
                        format!("{} collected {} from pot{with_hand}", player(&name)?, entry["amount"]),
                        at,
                    );
                }
                ("handComplete", _) => out.add(format!("-- ending hand #{hand_no} --"), at),
                _ => {}
            }
        }

        for seat in final_view["seats"].as_array().into_iter().flatten() {
            if let Some(name) = seat_name.get(&seat["seat"].as_i64().unwrap_or(-1)) {
                last_stack.insert(resolve(name)?, seat["stack"].as_i64().unwrap_or(0));
            }
        }
    }

    Ok(out
        .rows
        .into_iter()
        .rev()
        .map(|(entry, order)| format!("\"{}\",{},{order}", entry.replace('"', "\"\""), iso(order)))
        .collect())
}

/// Each player's result for the night in cents, from the export's ledger:
/// `netCents` when the host set a chip value, else one chip is one cent.
/// Players whose names normalize to the same person are summed.
pub fn final_profits(
    data: &Value,
    normalize_name: &dyn Fn(&str) -> Result<String>,
) -> Result<Vec<(String, i64)>> {
    let mut profits: Vec<(String, i64)> = Vec::new();
    for line in data["ledger"]["lines"].as_array().into_iter().flatten() {
        let cents = line["netCents"].as_i64().or_else(|| line["net"].as_i64()).unwrap_or(0);
        let name = normalize_name(line["name"].as_str().unwrap_or_default())?;
        match profits.iter_mut().find(|(n, _)| *n == name) {
            Some((_, total)) => *total += cents,
            None => profits.push((name, cents)),
        }
    }
    Ok(profits)
}

pub fn cents_per_chip(data: &Value) -> f64 {
    match (data["chipRate"]["cents"].as_f64(), data["chipRate"]["chips"].as_f64()) {
        (Some(cents), Some(chips)) if chips > 0.0 => cents / chips,
        _ => 1.0,
    }
}

/// One millisecond after the night's last logged moment.
pub fn session_end_ms(data: &Value) -> i64 {
    let mut last = data["startedAt"].as_i64().unwrap_or(0);
    for hand in data["hands"].as_array().into_iter().flatten() {
        last = last.max(hand["startedAt"].as_i64().unwrap_or(0));
        for entry in hand["final"]["entries"].as_array().into_iter().flatten() {
            last = last.max(entry["at"].as_i64().unwrap_or(0));
        }
    }
    last + 1
}
