# KuCoin new-entry regime gate and ecosystem metadata

Reviewed 2026-09-11. This is a pure source module and a maintained classification
file. It does not place orders, close bots, query a database, or fetch market data.

## Gate interface

```python
from trader.features.regime import gate_entry

decision = gate_entry(
    pair="RAYUSDTM",
    chart_reads={
        "XBTUSDTM": {"4h": btc_4h, "1d": btc_1d},
        "ETHUSDTM": {"4h": eth_4h, "1d": eth_1d},
        "SOLUSDTM": {"4h": sol_4h, "1d": sol_1d},
    },
    asof_ms=asof_ms,
    membership=loaded_solana_ecosystem_json,
    minimum_confidence=0.75,
    requested_direction="long",
)
```

A symbol's value may instead be the chart reader's complete report containing
`five_cells`. Only its `4h` and `1d` reads enter this gate. Each read supplies
`timeframe`, `available`, `direction` (`up`, `down`, `neutral`), `regime`
(`up`, `down`, `range`, `unknown`), `confidence`, `asof_ms`, and `last_closed_ms`.

A usable read must be available, have finite confidence at or above the stated
threshold, match the gate's as-of timestamp, and identify the most recent fully
closed UTC bucket. Direction and regime must agree. Confidence is a heuristic
chart-agreement score, not a calibrated probability. Missing, stale, future,
inconsistent, or low-confidence reads are explicitly unknown.

For each benchmark, the 4h and 1d directions must agree. Otherwise its usable
interpretation is Neutral; missing evidence remains separately marked unknown.
BTC means the canonical KuCoin contract `XBTUSDTM`.

| BTC and ETH readings | Permitted new-entry directions |
| --- | --- |
| Both agree on up | Long, Neutral |
| Both agree on down | Short, Neutral |
| Mixed, ranging, or timeframe disagreement | Neutral |
| Any required read unknown | Neutral, with unknown evidence recorded |

For a known Solana member, intersect that allowlist with SOL's own 4h/1d
allowlist. Opposing macro and SOL directions therefore leave Neutral. SOL itself
always receives its own overlay because it is Solana's native asset.

An unclassified contract retains the BTC/ETH allowlist. It records
`membership.status="unclassified"`, `unknown=true`, and a warning that the SOL
gate was not applied. Absence from a partial list does not prove non-membership
and does not introduce an additional Neutral-only restriction.

The return value records `macro_gate`, `sol_gate`, membership sources and basis,
each underlying `raw_read`, acceptance reasons, unknown reasons, the final
`allowed_directions`, and optional `requested_allowed`. It is detached from the
input dictionaries; nonfinite input numbers are preserved as explicit
`invalid_numeric` evidence markers so unknown decisions remain valid JSON. `scope="new_entries_only"` and
`existing_positions_action="none"` make the integration boundary explicit.
Neutral permission still requires a valid Neutral setup and lower-timeframe
entry trigger. It is not an unconditional instruction to open a bot. Existing
inside-range bots remain governed by their separate stop and replacement rules.

## What official contract metadata establishes

KuCoin's documented public contract endpoint is
`GET https://api-futures.kucoin.com/api/v1/contracts/active`. The response includes
instrument identity, trading increments, contract multiplier, quote currency,
listing time and funding fields. Its example also contains `marketType="CRYPTO"`,
`assetClass="CRYPTO"`, and `subMarketType=null`. The reviewed schema does not
establish a Solana-ecosystem enumeration or a mapping to the application's Solana
tab. These broad classification fields are not evidence of an ecosystem tag.
[Official Get All Symbols documentation](https://www.kucoin.com/docs-new/rest/futures-trading/market-data/get-all-symbols).

The parent task separately inspected the explicitly detached data copy and
reported the `universe` columns as `symbol`, `updated_at_ms`, `first_candle_ms`,
`listed_at_ms`, `turnover_30d`, `atr_pct`, `multiplier`, `lot_size`, `active`, and
`coverage_json`. Its sampled `coverage_json.contract_metadata` contained
`observed_at_ms=null` and `source="supplied_contract_metadata"`, with no ecosystem
tag. This is supplied local evidence; this module's implementation did not open
the data copy.

## Maintained membership file

`config/solana-ecosystem.json` is a deliberately partial operational list, not an
exchange-tab replica. It initially classifies SOL, RAY, JUP, JTO and ORCA as
members. Each record includes source URLs and a short membership basis:

- SOL is the network's native asset. [KuCoin Solana glossary](https://www.kucoin.com/learn/glossary/solana-sol).
- Raydium and Orca are Solana DEX projects. [KuCoin Solana DEX overview](https://www.kucoin.com/learn/crypto/top-decentralized-exchanges-dexs-in-the-solana-ecosystem).
- Jupiter is a Solana exchange aggregator with the JUP token. [KuCoin Jupiter guide](https://www.kucoin.com/learn/web3/what-is-jupiter-jup-solana-dex-and-how-to-use-it).
- Jito/JTO belongs to Solana staking infrastructure. [KuCoin JTO project description](https://www.kucoin.com/en-au/price/JTO), [KuCoin Solana restaking guide](https://www.kucoin.com/learn/crypto/restaking-on-solana-comprehensive-guide).

Native BTC and ETH are explicitly classified as non-members under this operational
network-origin convention; wrapped representations on other networks are outside
that classification. [KuCoin Bitcoin description](https://www.kucoin.com/knowledge-base/Knowledge/what-is-bitcoin-btc),
[KuCoin Ethereum glossary](https://www.kucoin.com/learn/glossary/ethereum-eth).

Exact contract identifiers are applied only when the independently validated
contract universe supplies them. The file does not establish present or past
tradability. Current educational descriptions are retrospective evidence, not
point-in-time historical exchange membership. Preserve this distinction in any
holdout report. `coverage_complete` stays false; unlisted assets remain
unclassified. Conflicting or unsourced entries also remain unclassified. Additions
require a sourced classification and separate verification of instrument identity.

## Public funding-history feasibility note

The Classic public funding-history endpoint accepts `symbol`, `from`, and `to`
with millisecond timestamps:
`GET https://api-futures.kucoin.com/api/v1/contract/funding-rates`.
Its records contain `symbol`, numeric `fundingRate`, and `timepoint` in
milliseconds. It uses the public rate-limit pool with weight 5.
[Official Public Funding History documentation](https://www.kucoin.com/docs-new/rest/futures-trading/funding-fees/get-public-funding-history).

The current UTA V2 public alternative is
`GET https://api.kucoin.com/api/ua/v2/market/funding-rate-history`, accepting
`symbol`, `startAt`, and `endAt`. Its example returns `data.symbol` and
`data.list` containing string `fundingRate` values and millisecond `ts` values.
[Official UTA V2 funding-history documentation](https://www.kucoin.com/docs-new/v2/rest/ua/get-history-funding-rate).

Neither reviewed history schema promises 90-day retention or supplies a
historical settlement-period field. A 90-day backfill therefore needs a separate
bounded public-data verification; no such backfill was performed here. The
contract response's `fundingRateGranularity`, `currentFundingRateGranularity`,
and `effectiveFundingRateCycleStartTime` are current contract metadata, not proof
of the period at every old settlement. Official announcements show settlement
frequencies changing between one, four, and eight hours.
[KuCoin May 2026 funding-frequency changes](https://www.kucoin.com/announcement/en-changes-to-funding-rates-for-several-perpetual-contracts-05-14).

The parent reports that the existing detached funding copy covers only September
10–11 and has null `period_ms` throughout. Those nulls must not become an assumed
8-hour verified history. Adjacent settlement timestamps can support an explicitly
inferred interval only after checking continuity; a missing record can otherwise
look like a longer funding period. Actual settlement cash and interval-normalized
features require separately labeled provenance and coverage.
