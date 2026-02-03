"""
History Recorder.

Records detection and impact data for ML training.
Stores structured data for future price prediction models.
"""

from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import json

import structlog
from sqlalchemy import select, and_, func
from sqlalchemy.orm import Session

from radar.models import (
    Detection, MarketImpact, WatchItemHistory, WatchItem,
    Source, Event, ImpactStatus
)
from radar.config import get_settings

logger = structlog.get_logger()


# Market hours for major markets (UTC)
MARKET_HOURS = {
    "US": {"open": 14, "close": 21},  # NYSE/NASDAQ 9:30-16:00 ET
    "EU": {"open": 7, "close": 16},   # European markets
    "ASIA": {"open": 0, "close": 8},   # Asian markets
}


def is_market_hours(timestamp: datetime, market: str = "US") -> bool:
    """Check if timestamp is during market hours."""
    hours = MARKET_HOURS.get(market, MARKET_HOURS["US"])
    hour = timestamp.hour
    return hours["open"] <= hour < hours["close"]


def get_market_session(timestamp: datetime) -> str:
    """Determine which market session a timestamp falls into."""
    hour = timestamp.hour

    if MARKET_HOURS["ASIA"]["open"] <= hour < MARKET_HOURS["ASIA"]["close"]:
        return "ASIA"
    elif MARKET_HOURS["EU"]["open"] <= hour < MARKET_HOURS["EU"]["close"]:
        return "EU"
    elif MARKET_HOURS["US"]["open"] <= hour < MARKET_HOURS["US"]["close"]:
        return "US"
    else:
        return "CLOSED"


class HistoryRecorder:
    """
    Records detection history for ML training.
    """

    def __init__(self):
        self.settings = get_settings()

    def record_detection(
        self,
        db: Session,
        detection: Detection,
    ) -> Optional[WatchItemHistory]:
        """
        Create a history record for a detection.
        Called when a detection is created.
        """
        if not detection.watch_item_id:
            return None

        # Check if already recorded
        existing = db.execute(
            select(WatchItemHistory).where(
                WatchItemHistory.detection_id == detection.id
            )
        ).scalars().first()

        if existing:
            return existing

        # Get event details
        event = db.get(Event, detection.event_id) if detection.event_id else None

        history = WatchItemHistory(
            watch_item_id=detection.watch_item_id,
            detection_id=detection.id,
            event_title=event.title if event else None,
            event_excerpt=event.content[:500] if event and event.content else None,
            severity_score=detection.severity_score,
            match_confidence=detection.match_confidence,
            trigger_quotes=detection.trigger_spans,
            created_at=detection.created_at,
            day_of_week=detection.created_at.weekday(),  # 0=Monday
            hour_of_day=detection.created_at.hour,
            is_market_hours=is_market_hours(detection.created_at),
            market_session=get_market_session(detection.created_at),
        )

        db.add(history)
        db.commit()

        logger.info(
            "history_recorded",
            detection_id=detection.id,
            watch_item_id=detection.watch_item_id,
        )

        return history

    def update_with_impact(
        self,
        db: Session,
        detection_id: int,
    ) -> Optional[WatchItemHistory]:
        """
        Update history record with impact measurements.
        Called after impacts are measured.
        """
        history = db.execute(
            select(WatchItemHistory).where(
                WatchItemHistory.detection_id == detection_id
            )
        ).scalars().first()

        if not history:
            return None

        # Get all impacts for this detection
        impacts = db.execute(
            select(MarketImpact).where(
                and_(
                    MarketImpact.detection_id == detection_id,
                    MarketImpact.status == ImpactStatus.MEASURED,
                )
            )
        ).scalars().all()

        if not impacts:
            return history

        # Build impact data structure
        market_impacts = []
        max_impact = 0
        had_significant = False
        directions = []

        for impact in impacts:
            impact_data = {
                "asset": impact.asset_symbol,
                "price_at_alert": impact.price_at_alert,
                "changes": {
                    "5min": impact.change_5min,
                    "15min": impact.change_15min,
                    "1hr": impact.change_1hr,
                    "4hr": impact.change_4hr,
                    "24hr": impact.change_24hr,
                },
                "max_up": impact.max_move_up,
                "max_down": impact.max_move_down,
                "actual_score": impact.actual_impact_score,
                "was_accurate": impact.was_accurate,
            }
            market_impacts.append(impact_data)

            if impact.actual_impact_score and impact.actual_impact_score > max_impact:
                max_impact = impact.actual_impact_score

            if impact.actual_impact_score and impact.actual_impact_score >= 50:
                had_significant = True

            # Determine direction based on max move
            if impact.max_move_up and impact.max_move_down:
                if impact.max_move_up > impact.max_move_down:
                    directions.append("up")
                elif impact.max_move_down > impact.max_move_up:
                    directions.append("down")
                else:
                    directions.append("neutral")

        history.market_impacts = market_impacts
        history.had_significant_impact = had_significant

        # Determine overall direction
        if directions:
            up_count = directions.count("up")
            down_count = directions.count("down")
            if up_count > down_count:
                history.impact_direction = "up"
            elif down_count > up_count:
                history.impact_direction = "down"
            else:
                history.impact_direction = "mixed"

        db.commit()

        logger.info(
            "history_updated_with_impact",
            detection_id=detection_id,
            had_significant_impact=had_significant,
            direction=history.impact_direction,
        )

        return history

    def get_training_data(
        self,
        db: Session,
        watch_item_id: Optional[int] = None,
        min_date: Optional[datetime] = None,
        max_date: Optional[datetime] = None,
        only_with_impact: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Get historical data formatted for ML training.
        """
        query = select(WatchItemHistory)

        filters = []
        if watch_item_id:
            filters.append(WatchItemHistory.watch_item_id == watch_item_id)
        if min_date:
            filters.append(WatchItemHistory.created_at >= min_date)
        if max_date:
            filters.append(WatchItemHistory.created_at <= max_date)
        if only_with_impact:
            filters.append(WatchItemHistory.market_impacts.isnot(None))

        if filters:
            query = query.where(and_(*filters))

        query = query.order_by(WatchItemHistory.created_at.desc())

        histories = db.execute(query).scalars().all()

        training_data = []
        for h in histories:
            # Get watch item name
            watch_item = db.get(WatchItem, h.watch_item_id)

            record = {
                "id": h.id,
                "watch_item": watch_item.name if watch_item else None,
                "watch_item_id": h.watch_item_id,
                "event_title": h.event_title,
                "event_excerpt": h.event_excerpt,
                "severity_score": h.severity_score,
                "match_confidence": h.match_confidence,
                "trigger_quotes": h.trigger_quotes,
                "timestamp": h.created_at.isoformat(),
                "day_of_week": h.day_of_week,
                "hour_of_day": h.hour_of_day,
                "is_market_hours": h.is_market_hours,
                "market_session": h.market_session,
                "market_impacts": h.market_impacts,
                "had_significant_impact": h.had_significant_impact,
                "impact_direction": h.impact_direction,
            }
            training_data.append(record)

        return training_data

    def export_training_data(
        self,
        db: Session,
        filepath: str,
        **kwargs,
    ) -> int:
        """
        Export training data to a JSON file.
        Returns count of records exported.
        """
        data = self.get_training_data(db, **kwargs)

        with open(filepath, "w") as f:
            json.dump(data, f, indent=2, default=str)

        logger.info(
            "training_data_exported",
            filepath=filepath,
            record_count=len(data),
        )

        return len(data)

    def get_watch_item_stats(
        self,
        db: Session,
        watch_item_id: int,
    ) -> Dict[str, Any]:
        """
        Get statistics for a watch item's historical performance.
        """
        histories = db.execute(
            select(WatchItemHistory).where(
                WatchItemHistory.watch_item_id == watch_item_id
            )
        ).scalars().all()

        if not histories:
            return {"status": "no_data"}

        total = len(histories)
        with_impact = [h for h in histories if h.market_impacts]
        significant = [h for h in histories if h.had_significant_impact]

        # Calculate average severity
        avg_severity = sum(h.severity_score for h in histories) / total

        # Direction stats
        up_count = sum(1 for h in histories if h.impact_direction == "up")
        down_count = sum(1 for h in histories if h.impact_direction == "down")
        mixed_count = sum(1 for h in histories if h.impact_direction == "mixed")

        # Time-based stats
        weekday_counts = [0] * 7
        hour_counts = [0] * 24
        market_hour_count = 0

        for h in histories:
            if h.day_of_week is not None:
                weekday_counts[h.day_of_week] += 1
            if h.hour_of_day is not None:
                hour_counts[h.hour_of_day] += 1
            if h.is_market_hours:
                market_hour_count += 1

        watch_item = db.get(WatchItem, watch_item_id)

        return {
            "watch_item": watch_item.name if watch_item else None,
            "total_detections": total,
            "detections_with_impact": len(with_impact),
            "significant_impact_count": len(significant),
            "significant_impact_rate": len(significant) / len(with_impact) if with_impact else 0,
            "avg_severity": avg_severity,
            "direction_stats": {
                "up": up_count,
                "down": down_count,
                "mixed": mixed_count,
            },
            "weekday_distribution": weekday_counts,
            "hour_distribution": hour_counts,
            "market_hours_percentage": market_hour_count / total if total > 0 else 0,
        }

    def get_correlation_analysis(
        self,
        db: Session,
        watch_item_id: int,
    ) -> Dict[str, Any]:
        """
        Analyze correlation between severity scores and actual impact.
        Useful for understanding prediction accuracy.
        """
        histories = db.execute(
            select(WatchItemHistory).where(
                and_(
                    WatchItemHistory.watch_item_id == watch_item_id,
                    WatchItemHistory.market_impacts.isnot(None),
                )
            )
        ).scalars().all()

        if not histories:
            return {"status": "no_data"}

        # Group by severity buckets
        buckets = {
            "low": {"min": 0, "max": 49, "total": 0, "significant": 0},
            "medium": {"min": 50, "max": 69, "total": 0, "significant": 0},
            "high": {"min": 70, "max": 100, "total": 0, "significant": 0},
        }

        for h in histories:
            for bucket_name, bucket in buckets.items():
                if bucket["min"] <= h.severity_score <= bucket["max"]:
                    bucket["total"] += 1
                    if h.had_significant_impact:
                        bucket["significant"] += 1
                    break

        # Calculate rates
        for bucket in buckets.values():
            if bucket["total"] > 0:
                bucket["significant_rate"] = bucket["significant"] / bucket["total"]
            else:
                bucket["significant_rate"] = 0

        return {
            "buckets": buckets,
            "interpretation": self._interpret_correlation(buckets),
        }

    def _interpret_correlation(self, buckets: Dict) -> str:
        """Interpret correlation results."""
        high_rate = buckets["high"]["significant_rate"]
        low_rate = buckets["low"]["significant_rate"]

        if buckets["high"]["total"] < 5:
            return "Not enough high-severity data for analysis"

        if high_rate > 0.6 and low_rate < 0.3:
            return "Strong positive correlation: high severity predicts significant impact"
        elif high_rate > 0.4 and high_rate > low_rate:
            return "Moderate correlation: severity is a useful but imperfect predictor"
        elif high_rate <= low_rate:
            return "Weak or inverse correlation: severity may not predict impact well"
        else:
            return "Unclear correlation pattern"

    def get_recent_history(
        self,
        db: Session,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Get recent history for admin display.
        """
        query = (
            select(WatchItemHistory, WatchItem)
            .join(WatchItem, WatchItemHistory.watch_item_id == WatchItem.id)
            .order_by(WatchItemHistory.created_at.desc())
            .limit(limit)
        )

        results = db.execute(query).all()

        return [
            {
                "id": h.id,
                "watch_item_name": w.name,
                "event_title": h.event_title,
                "severity_score": h.severity_score,
                "had_significant_impact": h.had_significant_impact,
                "impact_direction": h.impact_direction,
                "created_at": h.created_at,
                "market_session": h.market_session,
            }
            for h, w in results
        ]
