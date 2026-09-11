# M4 trading engines: research boundary

"Engineering complete" does not mean "strategy validated".

All outputs are RESEARCH/PAPER candidates, UNVALIDATED, execution_allowed=False.
There are no fills, profitability labels, scheduling hooks, broker calls, sizing,
provider requests or schema changes. M5 and M6 are outside this implementation.

## Historical parity and newly authorized contracts

The mission specifies the historical LEGACY_V5_PARITY_SEED formula: ascending
average-tie percentile ranks within the eligible signal-date cohort, weighted
1, .25, .15, .05 for top10, close8, expected_close, turnover, minus the explicit
risk_penalty times stop5 rank. Eligibility requires today_return strictly below
.05. Ties sort by score, top10, turnover descending, then symbol ascending.
These are mission-provided parity facts; no historical model artifact is shipped
or independently authenticated here. PreSurgeV7Engine is a versioned orchestration
contract, not a trained V7 model or executable-profitability model. No training occurs.
Scorer rows require one consistent source snapshot/model identity per date,
source_date equal to signal_date, availability by decision time, and training
strictly before the signal date. Upstream provenance is a trusted offline
attestation, not automatic provider validation. No missing row is imputed.

## Configuration and input assumptions

Every config field is required, including nullable optional gates. None means
explicitly disabled. Configs reject unknown fields, invalid ranges and nonfinite
numbers. Config identity hashes canonical JSON. Malformed inputs raise ValueError;
valid data without a pattern returns NO_CONFIRMATION (or no ranked candidates).

Swing consumes PointInTimeDailyRepository directly at decision_time, retaining
its audit/provenance identity. Swing candidate data_cutoff equals decision_time:
repository.load(as_of=decision_time) is the actual knowledge/query cutoff.
M3 PointInTimeDailyDataset exposes no separate last-evidence timestamp; this
cutoff is not a source timestamp and does not claim the latest observation arrived
at decision_time. No source timestamp is invented. V1 is a minimal EMA trend baseline (fast > slow and
close > fast), optionally requiring a close above prior highs. EMA is seeded by
first-window arithmetic mean on split-adjusted indicator observations. History,
EMA windows and optional breakout lookback are explicit. RSI, ADX, volatility,
liquidity and reward/risk planning are not enabled in this minimal baseline.
WATCH references the original D close as a planning reference, never a fill, for
NEXT_ELIGIBLE_SESSION after D. No actual future session/date is asserted; calendar
resolution remains a later consumer responsibility using verified calendar truth.

Intraday inputs explicitly start sequence 1 at the first continuous bar. The
trusted offline importer must establish that origin and session/source identity;
a truncated mid-session series must not be relabeled as opening data. All consumed
bars must be finalized CONTINUOUS, contiguous in sequence and interval, same
symbol/session/market date/source, with aware timestamps and valid finite OHLCV.
Bars unavailable at the cutoff and unfinalized bars are excluded before sequence
validation; they cannot provide evidence or bridge a gap. Invalid consumed data
rejects the request. No exchange wall-clock times are assumed. Session IDs and
market dates are explicit metadata, not inferred from UTC dates.

First15 uses exact interval coverage; a bar straddling the window boundary cannot
be split. Qualification requires close location at/above the configured threshold
and (minimum_return is None or return >= minimum_return), with no implicit
positive-return rule. An explicit prior bias UP or
DOWN labels continuation or reversal; NONE yields no confirmation. The bias
observation requires provenance and an aware available_at timestamp at or before
the decision cutoff; its availability contributes to the output data cutoff. Explicit
invalidation can reject a close below the opening price or a configured return
floor. A zero-width range yields no confirmation.

ORB uses exactly N opening bars and the current later bar's strict close breakout.
Optional volume compares current volume with the mean of all earlier session bars;
zero reference volume fails the gate. Optional stop references range low.
When traded_value is present, zero volume requires zero value and positive volume
requires positive value. Missing value is allowed only on paths not requiring VWAP.
VWAP is cumulative traded_value / volume with no typical-price substitution.
Extension uses close above its contemporaneous VWAP; a later pullback bar's range
must intersect the configured VWAP tolerance band; a strictly later current bar
must close above its reclaim level. Optional stop uses the configured trailing
bar lows. Search lookback is explicit, while VWAP always accumulates from session
origin. Zero cumulative volume yields no signal.
Momentum compares current close to the close L bars earlier. Relative volume uses
those L prior bars. Optional VWAP maximum extension uses session cumulative VWAP;
optional stop uses trailing lows. No engine infers a fill from a qualifying close.

All thresholds and these pattern interpretations are unvalidated research
assumptions. M8 must assess executable labels, purged rolling walk-forward,
frozen holdout, realistic costs/slippage, regimes, bootstrap uncertainty,
out-of-sample net expectancy, profit factor and drawdown. Synthetic engineering
tests demonstrate contract behavior only. Operational enablement remains gated.
