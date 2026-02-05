"""
Main pipeline orchestrating: collect -> detect -> score -> notify -> store -> learn.
"""

from datetime import datetime
from typing import List, Optional

import structlog
from sqlalchemy.orm import Session

from radar.config import get_settings
from radar.db import get_db_context
from radar import storage
from radar.models import Source, WatchItem, Event, SourceType, SourceTier
from radar.collectors import RSSCollector, WebCollector
from radar.collectors.telegram import TelegramChannelCollector
from radar.collectors.rss import CollectedItem
from radar.detect.rules import RuleMatcher, WatchItemRules
from radar.detect.llm_openrouter import OpenRouterClient, WatchItemInfo
from radar.detect.scoring import SeverityScorer
from radar.detect.dedup import Deduplicator
from radar.notify.telegram import TelegramNotifier, AlertData
from radar.schemas import CollectionResult, DetectionResult, PipelineRunResult
from radar.learning import (
    ImpactTracker,
    ReliabilityScorer,
    HistoryRecorder,
)

logger = structlog.get_logger()


class Pipeline:
    """
    End-to-end pipeline for market radar.

    Steps:
    1. Collect: Fetch new items from all active sources
    2. Detect: Match items against watch items (rules + LLM)
    3. Score: Calculate severity scores
    4. Notify: Send alerts for high-severity matches
    5. Store: Persist results to database
    """

    def __init__(self):
        self.settings = get_settings()
        self.rss_collector = RSSCollector()
        self.web_collector = WebCollector()
        self.telegram_collector = TelegramChannelCollector()
        self.rule_matcher = RuleMatcher()
        self.llm_client = OpenRouterClient()
        self.scorer = SeverityScorer()
        self.deduplicator = Deduplicator(
            cooldown_minutes=self.settings.default_cooldown_minutes,
        )
        self.notifier = TelegramNotifier()

        # Learning system components
        self.impact_tracker = ImpactTracker()
        self.reliability_scorer = ReliabilityScorer()
        self.history_recorder = HistoryRecorder()

    def run(self, once: bool = False) -> PipelineRunResult:
        """
        Run the full pipeline.

        Args:
            once: If True, run collection once without continuous loop

        Returns:
            PipelineRunResult with statistics
        """
        started_at = datetime.utcnow()
        errors: List[str] = []
        collection_results: List[CollectionResult] = []
        detection_results: List[DetectionResult] = []
        alerts_sent = 0

        logger.info("pipeline_started")

        try:
            with get_db_context() as db:
                # Step 1: Collect from all sources
                collection_results, new_events = self._collect_all(db)

                # Step 2-4: Process each new event
                for event in new_events:
                    try:
                        result = self._process_event(db, event)
                        if result:
                            detection_results.append(result)
                            if result.is_alerted:
                                alerts_sent += 1
                    except Exception as e:
                        error = f"Error processing event {event.id}: {str(e)}"
                        logger.error("event_processing_error", event_id=event.id, error=str(e))
                        errors.append(error)

                # Step 5: Learning system - measure pending impacts
                try:
                    measured = self.impact_tracker.measure_pending_impacts(db)
                    if measured > 0:
                        logger.info("impacts_measured", count=measured)

                        # Update reliability scores for measured impacts
                        self.reliability_scorer.update_all_sources(db)
                except Exception as e:
                    logger.error("learning_system_error", error=str(e))

        except Exception as e:
            error = f"Pipeline error: {str(e)}"
            logger.error("pipeline_error", error=str(e))
            errors.append(error)

        completed_at = datetime.utcnow()

        logger.info(
            "pipeline_completed",
            duration_seconds=(completed_at - started_at).total_seconds(),
            events_collected=sum(r.items_collected for r in collection_results),
            detections=len(detection_results),
            alerts_sent=alerts_sent,
            errors=len(errors),
        )

        return PipelineRunResult(
            started_at=started_at,
            completed_at=completed_at,
            collections=collection_results,
            detections=detection_results,
            alerts_sent=alerts_sent,
            errors=errors,
        )

    def collect_once(self) -> List[CollectionResult]:
        """Run collection only, without detection."""
        with get_db_context() as db:
            results, _ = self._collect_all(db)
            return results

    def _collect_all(
        self,
        db: Session,
    ) -> tuple[List[CollectionResult], List[Event]]:
        """Collect from all active sources."""
        results: List[CollectionResult] = []
        new_events: List[Event] = []

        # Get all active sources
        sources = storage.get_sources(db, active_only=True, limit=1000)
        logger.info("collecting_from_sources", count=len(sources))

        for source in sources:
            try:
                result, events = self._collect_source(db, source)
                results.append(result)
                new_events.extend(events)
            except Exception as e:
                error = f"Error collecting from {source.name}: {str(e)}"
                logger.error("source_collection_error", source_id=source.id, error=str(e))
                results.append(CollectionResult(
                    source_id=source.id,
                    source_name=source.name,
                    items_collected=0,
                    errors=[error],
                ))
                # Update error state
                storage.update_source_state(
                    db,
                    source.id,
                    last_error=str(e),
                    increment_errors=True,
                )

        return results, new_events

    def _collect_source(
        self,
        db: Session,
        source: Source,
    ) -> tuple[CollectionResult, List[Event]]:
        """Collect from a single source."""
        errors: List[str] = []
        new_events: List[Event] = []

        # Get source state
        state = source.state
        etag = state.etag if state else None
        last_modified = state.last_modified if state else None
        last_published = state.last_item_published_at if state else None

        items: List[CollectedItem] = []
        new_etag = etag
        new_modified = last_modified

        if source.source_type == SourceType.RSS:
            items, new_etag, new_modified = self.rss_collector.collect(
                source.url,
                etag=etag,
                last_modified=last_modified,
                last_published_at=last_published,
            )
        elif source.source_type == SourceType.WEB:
            item, new_etag, new_modified = self.web_collector.collect(
                source.url,
                content_selector=source.content_selector,
                etag=etag,
                last_modified=last_modified,
            )
            if item:
                items = [item]
        elif source.source_type == SourceType.TELEGRAM:
            items, new_etag, new_modified = self.telegram_collector.collect(
                source.url,
                last_published_at=last_published,
            )

        # Store new items
        latest_published = last_published
        for item in items:
            # Check for duplicates
            if self.deduplicator.is_duplicate_event(db, item.content_hash):
                continue

            # Create event
            event = storage.create_event(
                db,
                source_id=source.id,
                url=item.url,
                title=item.title,
                content_hash=item.content_hash,
                title_hash=item.title_hash,
                excerpt=item.excerpt,
                raw_text=item.raw_text,
                author=item.author,
                language=item.language,
                published_at=item.published_at,
            )
            new_events.append(event)

            # Track latest published date
            if item.published_at:
                if not latest_published or item.published_at > latest_published:
                    latest_published = item.published_at

        # Update source state
        storage.update_source_state(
            db,
            source.id,
            etag=new_etag,
            last_modified=new_modified,
            last_fetched_at=datetime.utcnow(),
            last_item_published_at=latest_published,
            reset_errors=True,
        )

        logger.info(
            "source_collected",
            source_id=source.id,
            source_name=source.name,
            items=len(items),
            new_events=len(new_events),
        )

        return CollectionResult(
            source_id=source.id,
            source_name=source.name,
            items_collected=len(new_events),
            errors=errors,
        ), new_events

    def _process_event(
        self,
        db: Session,
        event: Event,
    ) -> Optional[DetectionResult]:
        """Process a single event: detect, score, notify."""
        # Get watch items to match against
        watch_items = self._get_relevant_watch_items(db, event)
        if not watch_items:
            storage.mark_event_processed(db, event.id)
            return None

        # Build rules for matching
        rules = [
            WatchItemRules(
                id=wi.id,
                name=wi.name,
                keywords=wi.keywords or [],
                entities=wi.entities or [],
                severity_rules=wi.severity_rules or [],
            )
            for wi in watch_items
        ]

        # Phase A: Rule-based matching
        text = event.raw_text or event.excerpt or ""
        matches = self.rule_matcher.match(text, event.title, rules)

        best_match = None
        match_method = "rules"

        if matches:
            best_match = matches[0]

        # Phase B: LLM fallback if low confidence
        if (not best_match or best_match.confidence < self.settings.llm_confidence_threshold):
            if self.llm_client.is_available:
                llm_items = [
                    WatchItemInfo(
                        id=wi.id,
                        name=wi.name,
                        category=wi.category.value,
                        description=wi.description,
                        assets_affected=wi.assets_affected or [],
                    )
                    for wi in watch_items
                ]

                llm_match = self.llm_client.analyze(event.title, text, llm_items)

                if llm_match and llm_match.watch_item_id:
                    if (llm_match.confidence >= self.settings.llm_confidence_threshold and
                        llm_match.trigger_spans):
                        # Use LLM match if it's better
                        if not best_match or llm_match.confidence > best_match.confidence:
                            best_match = type('Match', (), {
                                'watch_item_id': llm_match.watch_item_id,
                                'watch_item_name': llm_match.watch_item_name,
                                'confidence': llm_match.confidence,
                                'trigger_spans': llm_match.trigger_spans,
                                'llm_reasoning': llm_match.reasoning,
                                'assets_affected': llm_match.assets_affected,
                                'market_impact': getattr(llm_match, 'market_impact', None),
                            })()
                            match_method = "llm"

        # No match found
        if not best_match or not best_match.trigger_spans:
            storage.mark_event_processed(db, event.id)
            return None

        # Get the watch item
        watch_item = next(
            (wi for wi in watch_items if wi.id == best_match.watch_item_id),
            None
        )
        if not watch_item:
            storage.mark_event_processed(db, event.id)
            return None

        # Calculate severity score
        source_tier = event.source.tier if event.source else SourceTier.SECONDARY
        score_breakdown = self.scorer.score(
            match_confidence=best_match.confidence,
            source_tier=source_tier,
            published_at=event.published_at,
            text=text,
            title=event.title,
            severity_rules=watch_item.severity_rules,
        )

        # Apply reliability adjustment from learning system
        if event.source_id:
            reliability_adjustment = self.reliability_scorer.get_tier_adjustment(
                db, event.source_id
            )
            if reliability_adjustment != 0:
                adjusted_total = max(0, min(100, score_breakdown.total + reliability_adjustment))
                score_breakdown.total = adjusted_total
                logger.debug(
                    "reliability_adjustment_applied",
                    source_id=event.source_id,
                    adjustment=reliability_adjustment,
                    new_score=adjusted_total,
                )

        # Get additional attributes from match
        llm_reasoning = getattr(best_match, 'llm_reasoning', None)
        market_impact = getattr(best_match, 'market_impact', None)
        # Use market_impact as reasoning if available (more detailed)
        llm_reasoning = market_impact or llm_reasoning
        assets_from_match = getattr(best_match, 'assets_affected', None)
        assets_affected = assets_from_match or watch_item.assets_affected or []

        # Create detection record
        detection = storage.create_detection(
            db,
            event_id=event.id,
            watch_item_id=watch_item.id,
            match_confidence=best_match.confidence,
            match_method=match_method,
            trigger_spans=best_match.trigger_spans,
            severity_score=score_breakdown.total,
            severity_breakdown=score_breakdown.to_dict(),
            llm_reasoning=llm_reasoning,
            assets_affected=assets_affected,
        )

        # Check if we should alert
        is_alerted = False
        if score_breakdown.total >= self.settings.alert_threshold:
            # First check cooldown
            should_alert, suppression_reason = self.deduplicator.should_alert(
                db,
                watch_item.id,
                score_breakdown.total,
                watch_item.cooldown_minutes,
            )

            # Then check for near-duplicates (same story from different sources)
            if should_alert:
                is_near_dup, dup_reason = self.deduplicator.is_near_duplicate_by_title(
                    db,
                    event.title,
                    watch_item.id,
                    hours=6,  # Look back 6 hours for similar news
                )
                if is_near_dup:
                    should_alert = False
                    suppression_reason = dup_reason

            if should_alert:
                # Send alert
                alert_result = self._send_alert(db, event, detection, watch_item)
                is_alerted = alert_result
            else:
                # Suppress
                storage.suppress_detection(db, detection.id, suppression_reason or "Cooldown")

        # Mark event as processed
        storage.mark_event_processed(db, event.id)

        return DetectionResult(
            event_id=event.id,
            watch_item_id=watch_item.id,
            match_confidence=best_match.confidence,
            severity_score=score_breakdown.total,
            trigger_spans=best_match.trigger_spans,
            is_alerted=is_alerted,
        )

    def _get_relevant_watch_items(
        self,
        db: Session,
        event: Event,
    ) -> List[WatchItem]:
        """Get watch items relevant to this event."""
        # Get watch items directly linked to this source
        source = event.source
        linked_items = list(source.watch_items) if source else []

        # If source is global or has no linked items, get all active watch items
        if source and (source.is_global or not linked_items):
            all_items = storage.get_watch_items(db, active_only=True, limit=1000)
            # Combine and dedupe
            item_ids = {wi.id for wi in linked_items}
            for wi in all_items:
                if wi.id not in item_ids:
                    linked_items.append(wi)

        return linked_items

    def _send_alert(
        self,
        db: Session,
        event: Event,
        detection,
        watch_item: WatchItem,
    ) -> bool:
        """Send alert and record result."""
        alert_data = AlertData(
            title=event.title,
            watch_item_name=watch_item.name,
            watch_item_category=watch_item.category.value,
            severity_score=detection.severity_score,
            match_confidence=detection.match_confidence,
            assets_affected=detection.assets_affected or [],
            trigger_spans=detection.trigger_spans or [],
            source_url=event.url,
            published_at=event.published_at,
            market_impact=detection.llm_reasoning,  # Contains detailed market impact analysis
        )

        # Format message for storage
        message_text = f"""Alert: {event.title}
Watch Item: {watch_item.name}
Severity: {detection.severity_score}
URL: {event.url}"""

        result = self.notifier.send_alert(alert_data)

        # Record alert
        storage.create_alert(
            db,
            detection_id=detection.id,
            message_text=message_text,
            telegram_message_id=result.message_id,
            telegram_chat_id=self.settings.telegram_chat_id,
            is_success=result.success,
            error_message=result.error,
            retry_count=result.retry_count,
        )

        if result.success:
            storage.mark_detection_alerted(db, detection.id)

            # Learning system: create impact tracking records
            try:
                self.impact_tracker.create_impact_records(db, detection)
            except Exception as e:
                logger.error("impact_record_creation_error", detection_id=detection.id, error=str(e))

            # Learning system: record history for ML training
            try:
                self.history_recorder.record_detection(db, detection)
            except Exception as e:
                logger.error("history_record_error", detection_id=detection.id, error=str(e))

        return result.success
