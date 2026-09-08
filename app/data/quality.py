from __future__ import annotations

from collections import Counter

from app.data.models import (
    DataQualityIssue,
    MarketBar,
    QualitySeverity,
)


class DataQualityEngine:
    def validate_bar(
        self,
        bar: MarketBar,
    ) -> list[DataQualityIssue]:
        issues: list[
            DataQualityIssue
        ] = []

        common = {
            "symbol": bar.symbol,
            "market_date": (
                bar.timestamp.date()
            ),
        }

        if bar.high < bar.low:
            issues.append(
                DataQualityIssue(
                    severity=(
                        QualitySeverity.FATAL
                    ),
                    code="HIGH_BELOW_LOW",
                    message=(
                        "high price is below "
                        "low price"
                    ),
                    payload={
                        "high": str(bar.high),
                        "low": str(bar.low),
                    },
                    **common,
                )
            )

        if not (
            bar.low
            <= bar.open
            <= bar.high
        ):
            issues.append(
                DataQualityIssue(
                    severity=(
                        QualitySeverity.ERROR
                    ),
                    code="OPEN_OUTSIDE_RANGE",
                    message=(
                        "open price is outside "
                        "the high-low range"
                    ),
                    payload={
                        "open": str(bar.open),
                        "high": str(bar.high),
                        "low": str(bar.low),
                    },
                    **common,
                )
            )

        if not (
            bar.low
            <= bar.close
            <= bar.high
        ):
            issues.append(
                DataQualityIssue(
                    severity=(
                        QualitySeverity.ERROR
                    ),
                    code="CLOSE_OUTSIDE_RANGE",
                    message=(
                        "close price is outside "
                        "the high-low range"
                    ),
                    payload={
                        "close": str(bar.close),
                        "high": str(bar.high),
                        "low": str(bar.low),
                    },
                    **common,
                )
            )

        if (
            bar.volume == 0
            and bar.turnover is not None
            and bar.turnover > 0
        ):
            issues.append(
                DataQualityIssue(
                    severity=(
                        QualitySeverity.WARNING
                    ),
                    code=(
                        "ZERO_VOLUME_WITH_TURNOVER"
                    ),
                    message=(
                        "turnover exists while "
                        "reported volume is zero"
                    ),
                    payload={
                        "volume": str(
                            bar.volume
                        ),
                        "turnover": str(
                            bar.turnover
                        ),
                    },
                    **common,
                )
            )

        return issues

    def validate_batch(
        self,
        bars: list[MarketBar],
    ) -> list[DataQualityIssue]:
        issues: list[
            DataQualityIssue
        ] = []

        for bar in bars:
            issues.extend(
                self.validate_bar(
                    bar
                )
            )

        keys = [
            (
                bar.symbol,
                bar.timestamp,
            )
            for bar in bars
        ]

        counts = Counter(keys)

        for (
            symbol,
            timestamp,
        ), count in counts.items():
            if count <= 1:
                continue

            issues.append(
                DataQualityIssue(
                    severity=(
                        QualitySeverity.ERROR
                    ),
                    code=(
                        "DUPLICATE_BAR_TIMESTAMP"
                    ),
                    symbol=symbol,
                    market_date=(
                        timestamp.date()
                    ),
                    message=(
                        "duplicate bar timestamp "
                        "for symbol"
                    ),
                    payload={
                        "timestamp": (
                            timestamp.isoformat()
                        ),
                        "count": count,
                    },
                )
            )

        return issues
