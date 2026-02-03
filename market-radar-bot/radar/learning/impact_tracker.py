"""
Market Impact Tracker.

Measures actual price movements after alerts to determine
if predictions were accurate.
"""

from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

import structlog
from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from radar.models import (
    Detection, MarketImpact, ImpactStatus, PriceData,
    Event, WatchItem, Source
)
from radar.learning.price_fetcher import PriceFetcher, PricePoint
from radar.config import get_settings

logger = structlog.get_logger()


# Thresholds for determining "significant" price movement
IMPACT_THRESHOLDS = {
    # symbol_type: {interval_minutes: significant_move_percent}
    "forex": {5: 0.05, 15: 0.10, 60: 0.20, 240: 0.40, 1440: 0.80},
    "index": {5: 0.10, 15: 0.25, 60: 0.50, 240: 1.00, 1440: 2.00},
    "commodity": {5: 0.15, 15: 0.30, 60: 0.60, 240: 1.20, 1440: 2.50},
    "crypto": {5: 0.50, 15: 1.00, 60: 2.00, 240: 4.00, 1440: 8.00},
    "default": {5: 0.10, 15: 0.20, 60: 0.40, 240: 0.80, 1440: 1.50},
}


def get_symbol_type(symbol: str) -> str:
    """Determine the type of symbol for threshold lookup."""
    symbol = symbol.upper()

    forex_symbols = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "DXY"]
    index_symbols = ["SPX", "SPY", "NDX", "QQQ", "DJI", "VIX", "DAX", "FTSE", "N225"]
    commodity_symbols = ["GOLD", "SILVER", "OIL", "WTI", "BRENT", "NATGAS"]
    crypto_symbols = ["BTC", "ETH", "XRP", "SOL"]

    if symbol in forex_symbols:
        return "forex"
    elif symbol in index_symbols:
        return "index"
    elif symbol in commodity_symbols:
        return "commodity"
    elif symbol in crypto_symbols:
        return "crypto"
    else:
        return "default"


class ImpactTracker:
    """
    Tracks and measures market impact after alerts.
    """

    def __init__(self):
        self.settings = get_settings()
        self.price_fetcher = PriceFetcher()
        self.intervals = [5, 15, 60, 240, 1440]  # minutes

    def create_impact_records(
        self,
        db: Session,
        detection: Detection,
    ) -> List[MarketImpact]:
        """
        Create MarketImpact records for a detection.
        One record per affected asset.
        """
        records = []

        # Get assets affected from detection or watch item
        assets = detection.assets_affected or []
        if not assets and detection.watch_item:
            assets = detection.watch_item.assets_affected or []

        if not assets:
            logger.debug("no_assets_for_impact", detection_id=detection.id)
            return records

        alert_time = detection.created_at

        for asset in assets:
            # Get current price
            price_point = self.price_fetcher.get_current_price(asset)
            price_at_alert = price_point.price if price_point else None

            impact = MarketImpact(
                detection_id=detection.id,
                asset_symbol=asset.upper(),
                price_at_alert=price_at_alert,
                alert_timestamp=alert_time,
                status=ImpactStatus.PENDING,
            )

            db.add(impact)
            records.append(impact)

            logger.info(
                "impact_record_created",
                detection_id=detection.id,
                asset=asset,
                price=price_at_alert,
            )

        db.commit()
        return records

    def measure_pending_impacts(self, db: Session) -> int:
        """
        Measure all pending impact records that are ready.
        Returns count of records measured.
        """
        measured_count = 0
        now = datetime.utcnow()

        # Find pending impacts where enough time has passed for at least 5min measurement
        cutoff = now - timedelta(minutes=6)  # 6 min to ensure 5min data is available

        query = select(MarketImpact).where(
            and_(
                MarketImpact.status == ImpactStatus.PENDING,
                MarketImpact.alert_timestamp < cutoff,
            )
        )

        pending = db.execute(query).scalars().all()

        for impact in pending:
            try:
                updated = self._measure_impact(db, impact)
                if updated:
                    measured_count += 1
            except Exception as e:
                logger.error(
                    "impact_measurement_error",
                    impact_id=impact.id,
                    error=str(e),
                )

        return measured_count

    def _measure_impact(self, db: Session, impact: MarketImpact) -> bool:
        """
        Measure price changes for a single impact record.
        """
        if not impact.price_at_alert:
            # Try to get price at alert time retroactively
            price_point = self.price_fetcher.get_price_at_time(
                impact.asset_symbol,
                impact.alert_timestamp,
            )
            if price_point:
                impact.price_at_alert = price_point.price
            else:
                impact.status = ImpactStatus.NO_DATA
                db.commit()
                return False

        base_price = impact.price_at_alert
        base_time = impact.alert_timestamp
        now = datetime.utcnow()

        # Get prices at each interval
        prices = self.price_fetcher.get_prices_for_intervals(
            impact.asset_symbol,
            base_time,
            self.intervals,
        )

        # Calculate changes
        max_up = 0.0
        max_down = 0.0

        for interval, price_point in prices.items():
            if price_point and price_point.price:
                change_pct = ((price_point.price - base_price) / base_price) * 100

                # Set interval-specific fields
                if interval == 5:
                    impact.price_5min = price_point.price
                    impact.change_5min = change_pct
                elif interval == 15:
                    impact.price_15min = price_point.price
                    impact.change_15min = change_pct
                elif interval == 60:
                    impact.price_1hr = price_point.price
                    impact.change_1hr = change_pct
                elif interval == 240:
                    impact.price_4hr = price_point.price
                    impact.change_4hr = change_pct
                elif interval == 1440:
                    impact.price_24hr = price_point.price
                    impact.change_24hr = change_pct

                # Track max moves
                if change_pct > max_up:
                    max_up = change_pct
                if change_pct < max_down:
                    max_down = change_pct

        impact.max_move_up = max_up
        impact.max_move_down = abs(max_down)

        # Calculate actual impact score
        impact.actual_impact_score = self._calculate_impact_score(impact)

        # Determine if prediction was accurate
        detection = db.get(Detection, impact.detection_id)
        if detection:
            impact.was_accurate = self._was_prediction_accurate(
                detection.severity_score,
                impact.actual_impact_score,
            )

        # Check if we have enough data to mark as measured
        # Need at least 5min and 15min data
        if impact.change_5min is not None and impact.change_15min is not None:
            impact.status = ImpactStatus.MEASURED
            impact.measured_at = now
        elif (now - base_time) > timedelta(hours=2):
            # If 2 hours passed and still no data, mark insufficient
            impact.status = ImpactStatus.INSUFFICIENT

        db.commit()

        logger.info(
            "impact_measured",
            impact_id=impact.id,
            asset=impact.asset_symbol,
            actual_score=impact.actual_impact_score,
            accurate=impact.was_accurate,
        )

        return True

    def _calculate_impact_score(self, impact: MarketImpact) -> int:
        """
        Calculate actual impact score (0-100) based on price movements.
        """
        symbol_type = get_symbol_type(impact.asset_symbol)
        thresholds = IMPACT_THRESHOLDS.get(symbol_type, IMPACT_THRESHOLDS["default"])

        scores = []

        # Score each interval
        interval_changes = [
            (5, impact.change_5min),
            (15, impact.change_15min),
            (60, impact.change_1hr),
            (240, impact.change_4hr),
            (1440, impact.change_24hr),
        ]

        for interval, change in interval_changes:
            if change is not None:
                threshold = thresholds.get(interval, 0.5)
                abs_change = abs(change)

                # Score based on how much the move exceeded threshold
                if abs_change >= threshold * 3:
                    scores.append(100)
                elif abs_change >= threshold * 2:
                    scores.append(80)
                elif abs_change >= threshold:
                    scores.append(60)
                elif abs_change >= threshold * 0.5:
                    scores.append(40)
                else:
                    scores.append(20)

        if not scores:
            return 0

        # Weight: earlier intervals matter more for "breaking news" impact
        weights = [0.35, 0.30, 0.20, 0.10, 0.05][:len(scores)]
        total_weight = sum(weights)
        weighted_score = sum(s * w for s, w in zip(scores, weights)) / total_weight

        return int(weighted_score)

    def _was_prediction_accurate(
        self,
        predicted_severity: int,
        actual_impact: int,
    ) -> bool:
        """
        Determine if prediction was accurate.

        Rules:
        - High severity (>=70) should have high impact (>=50)
        - Medium severity (50-69) should have medium+ impact (>=30)
        - Low severity (<50) with high impact is not penalized (pleasant surprise)
        """
        if predicted_severity >= 70:
            # High severity should have meaningful impact
            return actual_impact >= 50
        elif predicted_severity >= 50:
            # Medium severity needs moderate impact
            return actual_impact >= 30
        else:
            # Low severity - any prediction is fine
            return True

    def get_impact_summary(
        self,
        db: Session,
        detection_id: int,
    ) -> Dict[str, Any]:
        """
        Get summary of all impact measurements for a detection.
        """
        query = select(MarketImpact).where(
            MarketImpact.detection_id == detection_id
        )
        impacts = db.execute(query).scalars().all()

        if not impacts:
            return {"status": "no_data", "assets": []}

        summary = {
            "status": "measured" if any(i.status == ImpactStatus.MEASURED for i in impacts) else "pending",
            "assets": [],
            "overall_accurate": None,
        }

        accurate_count = 0
        measured_count = 0

        for impact in impacts:
            asset_summary = {
                "symbol": impact.asset_symbol,
                "status": impact.status.value,
                "price_at_alert": impact.price_at_alert,
                "changes": {
                    "5min": impact.change_5min,
                    "15min": impact.change_15min,
                    "1hr": impact.change_1hr,
                    "4hr": impact.change_4hr,
                    "24hr": impact.change_24hr,
                },
                "max_move_up": impact.max_move_up,
                "max_move_down": impact.max_move_down,
                "actual_impact_score": impact.actual_impact_score,
                "was_accurate": impact.was_accurate,
            }
            summary["assets"].append(asset_summary)

            if impact.was_accurate is not None:
                measured_count += 1
                if impact.was_accurate:
                    accurate_count += 1

        if measured_count > 0:
            summary["overall_accurate"] = accurate_count >= (measured_count / 2)

        return summary

    def store_price_data(
        self,
        db: Session,
        price_point: PricePoint,
    ) -> None:
        """
        Store a price point in the database for historical analysis.
        """
        # Check if already exists
        existing = db.execute(
            select(PriceData).where(
                and_(
                    PriceData.symbol == price_point.symbol,
                    PriceData.timestamp == price_point.timestamp,
                )
            )
        ).scalars().first()

        if existing:
            return

        price_data = PriceData(
            symbol=price_point.symbol,
            timestamp=price_point.timestamp,
            price=price_point.price,
            open=price_point.open,
            high=price_point.high,
            low=price_point.low,
            close=price_point.close,
            volume=price_point.volume,
            data_source=price_point.source,
        )

        db.add(price_data)
        db.commit()
