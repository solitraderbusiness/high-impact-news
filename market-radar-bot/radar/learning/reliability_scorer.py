"""
Source Reliability Scorer.

Tracks and updates source reliability based on prediction accuracy.
Adjusts severity scoring based on historical performance.
"""

from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple

import structlog
from sqlalchemy import select, and_, func
from sqlalchemy.orm import Session

from radar.models import (
    Source, SourceReliability, MarketImpact, Detection,
    ImpactStatus, AlertSent
)
from radar.config import get_settings

logger = structlog.get_logger()


# Configuration for reliability scoring
RELIABILITY_CONFIG = {
    # Minimum alerts before we start adjusting scores
    "min_alerts_for_adjustment": 10,

    # Weight for recent vs historical performance (higher = more weight on recent)
    "recency_weight": 0.7,

    # False positive threshold (above this, source is unreliable)
    "false_positive_threshold": 0.6,

    # Tier adjustment ranges
    "max_positive_adjustment": 20,
    "max_negative_adjustment": -20,

    # Decay rate for old measurements (per day)
    "decay_rate": 0.99,

    # Hours to group for hourly statistics
    "stat_hours": [0, 4, 8, 12, 16, 20],  # 4-hour buckets
}


class ReliabilityScorer:
    """
    Scores source reliability based on prediction accuracy.
    """

    def __init__(self):
        self.settings = get_settings()
        self.config = RELIABILITY_CONFIG

    def get_or_create_reliability(
        self,
        db: Session,
        source_id: int,
    ) -> SourceReliability:
        """
        Get or create a SourceReliability record for a source.
        """
        reliability = db.execute(
            select(SourceReliability).where(
                SourceReliability.source_id == source_id
            )
        ).scalars().first()

        if not reliability:
            reliability = SourceReliability(
                source_id=source_id,
                total_alerts=0,
                high_severity_alerts=0,
                accurate_predictions=0,
                inaccurate_predictions=0,
                reliability_score=0.5,  # Start neutral
                tier_adjustment=0,
                avg_actual_impact=0.0,
                false_positive_rate=0.0,
                hourly_stats={},
            )
            db.add(reliability)
            db.commit()

        return reliability

    def update_from_impact(
        self,
        db: Session,
        impact: MarketImpact,
    ) -> Optional[SourceReliability]:
        """
        Update source reliability based on a measured impact.
        """
        if impact.status != ImpactStatus.MEASURED:
            return None

        # Get the detection and source
        detection = db.get(Detection, impact.detection_id)
        if not detection or not detection.source_id:
            return None

        reliability = self.get_or_create_reliability(db, detection.source_id)

        # Update counters
        reliability.total_alerts += 1

        if detection.severity_score >= 70:
            reliability.high_severity_alerts += 1

        if impact.was_accurate is not None:
            if impact.was_accurate:
                reliability.accurate_predictions += 1
            else:
                reliability.inaccurate_predictions += 1

        # Update average actual impact (exponential moving average)
        if impact.actual_impact_score is not None:
            if reliability.avg_actual_impact == 0:
                reliability.avg_actual_impact = float(impact.actual_impact_score)
            else:
                alpha = 0.2  # Smoothing factor
                reliability.avg_actual_impact = (
                    alpha * impact.actual_impact_score +
                    (1 - alpha) * reliability.avg_actual_impact
                )

        # Update hourly stats
        hour = impact.alert_timestamp.hour
        bucket = (hour // 4) * 4  # 4-hour buckets
        bucket_key = str(bucket)

        hourly_stats = reliability.hourly_stats or {}
        if bucket_key not in hourly_stats:
            hourly_stats[bucket_key] = {
                "total": 0,
                "accurate": 0,
                "avg_impact": 0.0,
            }

        hourly_stats[bucket_key]["total"] += 1
        if impact.was_accurate:
            hourly_stats[bucket_key]["accurate"] += 1

        # Update average impact for this bucket
        bucket_total = hourly_stats[bucket_key]["total"]
        bucket_avg = hourly_stats[bucket_key]["avg_impact"]
        hourly_stats[bucket_key]["avg_impact"] = (
            (bucket_avg * (bucket_total - 1) + (impact.actual_impact_score or 0))
            / bucket_total
        )

        reliability.hourly_stats = hourly_stats

        # Recalculate reliability score and tier adjustment
        self._recalculate_reliability(db, reliability)

        reliability.updated_at = datetime.utcnow()
        db.commit()

        logger.info(
            "reliability_updated",
            source_id=detection.source_id,
            reliability_score=reliability.reliability_score,
            tier_adjustment=reliability.tier_adjustment,
            false_positive_rate=reliability.false_positive_rate,
        )

        return reliability

    def _recalculate_reliability(
        self,
        db: Session,
        reliability: SourceReliability,
    ) -> None:
        """
        Recalculate reliability score and tier adjustment.
        """
        total_predictions = (
            reliability.accurate_predictions +
            reliability.inaccurate_predictions
        )

        if total_predictions == 0:
            return

        # Calculate base accuracy rate
        accuracy_rate = reliability.accurate_predictions / total_predictions

        # Calculate false positive rate (high severity alerts with low impact)
        if reliability.high_severity_alerts > 0:
            # Get count of high severity alerts that had low actual impact
            false_positives = db.execute(
                select(func.count(MarketImpact.id)).join(
                    Detection, MarketImpact.detection_id == Detection.id
                ).where(
                    and_(
                        Detection.source_id == reliability.source_id,
                        Detection.severity_score >= 70,
                        MarketImpact.actual_impact_score < 30,
                        MarketImpact.status == ImpactStatus.MEASURED,
                    )
                )
            ).scalar() or 0

            reliability.false_positive_rate = false_positives / reliability.high_severity_alerts
        else:
            reliability.false_positive_rate = 0.0

        # Calculate reliability score (0.0 to 1.0)
        # Weight: accuracy (60%), inverse false positive rate (40%)
        reliability.reliability_score = (
            0.6 * accuracy_rate +
            0.4 * (1 - reliability.false_positive_rate)
        )

        # Calculate tier adjustment (-20 to +20)
        if reliability.total_alerts >= self.config["min_alerts_for_adjustment"]:
            # Map reliability score to tier adjustment
            # 0.5 = neutral (0 adjustment)
            # 1.0 = max positive (+20)
            # 0.0 = max negative (-20)
            adjustment = (reliability.reliability_score - 0.5) * 40

            # Clamp to allowed range
            reliability.tier_adjustment = max(
                self.config["max_negative_adjustment"],
                min(self.config["max_positive_adjustment"], int(adjustment))
            )
        else:
            # Not enough data yet
            reliability.tier_adjustment = 0

    def get_tier_adjustment(
        self,
        db: Session,
        source_id: int,
    ) -> int:
        """
        Get the tier adjustment for a source.
        Returns 0 if no reliability data exists.
        """
        reliability = db.execute(
            select(SourceReliability).where(
                SourceReliability.source_id == source_id
            )
        ).scalars().first()

        if reliability and reliability.total_alerts >= self.config["min_alerts_for_adjustment"]:
            return reliability.tier_adjustment

        return 0

    def get_adjusted_severity(
        self,
        db: Session,
        source_id: int,
        base_severity: int,
    ) -> int:
        """
        Get severity score adjusted for source reliability.
        """
        adjustment = self.get_tier_adjustment(db, source_id)
        adjusted = base_severity + adjustment

        # Clamp to 0-100
        return max(0, min(100, adjusted))

    def update_all_sources(self, db: Session) -> int:
        """
        Recalculate reliability for all sources.
        Returns count of sources updated.
        """
        updated = 0

        sources = db.execute(select(Source)).scalars().all()

        for source in sources:
            reliability = db.execute(
                select(SourceReliability).where(
                    SourceReliability.source_id == source.id
                )
            ).scalars().first()

            if reliability:
                self._recalculate_reliability(db, reliability)
                updated += 1

        db.commit()
        return updated

    def get_source_stats(
        self,
        db: Session,
        source_id: int,
    ) -> Dict[str, Any]:
        """
        Get detailed statistics for a source.
        """
        reliability = db.execute(
            select(SourceReliability).where(
                SourceReliability.source_id == source_id
            )
        ).scalars().first()

        if not reliability:
            return {"status": "no_data"}

        source = db.get(Source, source_id)

        return {
            "source_name": source.name if source else "Unknown",
            "total_alerts": reliability.total_alerts,
            "high_severity_alerts": reliability.high_severity_alerts,
            "accurate_predictions": reliability.accurate_predictions,
            "inaccurate_predictions": reliability.inaccurate_predictions,
            "reliability_score": reliability.reliability_score,
            "tier_adjustment": reliability.tier_adjustment,
            "avg_actual_impact": reliability.avg_actual_impact,
            "false_positive_rate": reliability.false_positive_rate,
            "hourly_stats": reliability.hourly_stats,
            "is_mature": reliability.total_alerts >= self.config["min_alerts_for_adjustment"],
        }

    def get_all_source_rankings(
        self,
        db: Session,
    ) -> List[Dict[str, Any]]:
        """
        Get all sources ranked by reliability.
        """
        query = (
            select(SourceReliability, Source)
            .join(Source, SourceReliability.source_id == Source.id)
            .where(SourceReliability.total_alerts >= self.config["min_alerts_for_adjustment"])
            .order_by(SourceReliability.reliability_score.desc())
        )

        results = db.execute(query).all()

        rankings = []
        for reliability, source in results:
            rankings.append({
                "source_id": source.id,
                "source_name": source.name,
                "source_url": source.url,
                "reliability_score": reliability.reliability_score,
                "tier_adjustment": reliability.tier_adjustment,
                "total_alerts": reliability.total_alerts,
                "false_positive_rate": reliability.false_positive_rate,
            })

        return rankings

    def should_demote_source(
        self,
        db: Session,
        source_id: int,
    ) -> Tuple[bool, str]:
        """
        Check if a source should be demoted due to poor performance.
        Returns (should_demote, reason).
        """
        reliability = db.execute(
            select(SourceReliability).where(
                SourceReliability.source_id == source_id
            )
        ).scalars().first()

        if not reliability:
            return False, "No reliability data"

        if reliability.total_alerts < self.config["min_alerts_for_adjustment"]:
            return False, "Not enough data"

        # Check for high false positive rate
        if reliability.false_positive_rate > self.config["false_positive_threshold"]:
            return True, f"High false positive rate: {reliability.false_positive_rate:.1%}"

        # Check for very low reliability
        if reliability.reliability_score < 0.3:
            return True, f"Low reliability score: {reliability.reliability_score:.2f}"

        return False, "Source performing adequately"

    def get_best_hours_for_source(
        self,
        db: Session,
        source_id: int,
    ) -> List[Dict[str, Any]]:
        """
        Get the best performing hours for a source.
        Useful for understanding when source is most reliable.
        """
        reliability = db.execute(
            select(SourceReliability).where(
                SourceReliability.source_id == source_id
            )
        ).scalars().first()

        if not reliability or not reliability.hourly_stats:
            return []

        hours = []
        for bucket_key, stats in reliability.hourly_stats.items():
            if stats["total"] >= 3:  # Need minimum samples
                accuracy = stats["accurate"] / stats["total"] if stats["total"] > 0 else 0
                hours.append({
                    "hour_bucket": int(bucket_key),
                    "hour_range": f"{bucket_key}:00 - {int(bucket_key)+4}:00",
                    "total_alerts": stats["total"],
                    "accuracy_rate": accuracy,
                    "avg_impact": stats["avg_impact"],
                })

        # Sort by accuracy
        hours.sort(key=lambda x: x["accuracy_rate"], reverse=True)
        return hours
