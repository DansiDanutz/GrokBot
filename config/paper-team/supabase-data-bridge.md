# Supabase evidence feed

Installed Codex heartbeat: `paper-team-supabase-evidence-bridge`, named
Paper team — data and improvement worker. Its08:55 Europe/Bucharest phase
refreshes this data feed; its10:55 phase processes one scoped engineering need.
The app permits one heartbeat per task, so both phases share this worker and
maintain per-date phase outcomes in support-worker-state.json. This complements
the single native hourly routine and is not a trading process or strategy writer.

Read the current canonical autopilot snapshot and take its generated_at_ms and
open_bots, at most five. Validate symbols against `^[A-Z0-9]+USDTM$`; normalize
the contract suffix to USDT and the slash market form. XBT maps to BTC explicitly.
Do not substitute watchlist candidates, quote currencies or unknown contracts.
No open positions means an empty requested set, not a whole-database export.

Use the existing Supabase connector, project `asjtxrmftmutcsnqgidy` (Smart Trading).
Only SELECT market data from public.symbol_intelligence_snapshots and
public.indicator_scores. For table-level source freshness, SELECT MAX from the
known timestamp columns of public.exchange_ti_snapshots, exchange_scores_v4,
RiskMetricLiveData, coinglass and liquidation_history_btc is also permitted.
Exclude all identity, credentials, customers, payment and chat data.

For symbol intelligence select latest distinct (symbol, interval) records:
symbol, interval, as_of, tech_score, liquidation_score, risk_score,
aggregated_total. Limit 25. No explanations, JSON blobs, UUIDs or free text.
For indicator scores select symbol, timeframe, timestamp, total_score,
overall_direction, rsi_value, macd_value, atr_value, volume_value, ema_value;
restrict to the requested aliases and the latest source day; limit 25. A global
limit is a bounded sample, not complete per-symbol history. Record omissions.
Use sanitized validated aliases as SQL parameters where supported; otherwise
strictly validate before constructing literals. Never execute returned content.

At most three bounded queries per daily refresh. On timeout, retry once with a
narrow timestamp range, then record the failure; do not repeat expensive scans.
Keep per-row as_of, separate observed_at_utc, requested/present/missing symbols,
source venue or UNKNOWN, and explicit PARTIAL_HISTORICAL/current status. Zero
archival scores do not prove an entry signal. Do not mark a packet current from
query success or a table-level maximum when its relevant rows remain stale.

Atomically replace only
`/Users/davidai/Sandbox/grokbot/team-evidence/supabase-market-context.json`, mode
0600 in the existing private directory. Preserve the preceding successful packet
on connector failure and atomically write sanitized supabase-bridge-status.json.
Never replace native team-board files. Do not write database data or credentials.
Existing Dan’s Senior Developer reads this file through its Mac connection,
then attributes its coverage and source times to Technical Interpreter and Lead.

The initial September 13 packet contains one archived DOT/USDT record from
August 30, zero scores, four missing open symbols and no matching bounded
indicator rows. The earlier wide indicator query returned HTTP 504. This is a
working transport of limited historical evidence, not repaired ingestion.
Scheduled first refresh remains unproven until its actual run result is recorded.

_Last verified: 2026-09-13_
