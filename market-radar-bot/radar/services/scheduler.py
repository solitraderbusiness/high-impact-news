"""
APScheduler-based scheduler for running the pipeline periodically.
"""

import signal
import sys
from typing import Optional

import structlog
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from radar.config import get_settings
from radar.services.pipeline import Pipeline

logger = structlog.get_logger()


class Scheduler:
    """
    Manages periodic execution of the radar pipeline.
    Uses APScheduler for reliable job scheduling.
    """

    def __init__(self):
        self.settings = get_settings()
        self.pipeline = Pipeline()
        self.scheduler: Optional[BackgroundScheduler] = None
        self._running = False

    def start(self, blocking: bool = True) -> None:
        """
        Start the scheduler.

        Args:
            blocking: If True, block and run until interrupted
        """
        logger.info(
            "scheduler_starting",
            poll_interval=self.settings.poll_interval_seconds,
        )

        # Create scheduler
        self.scheduler = BackgroundScheduler(
            job_defaults={
                'coalesce': True,  # Combine missed runs into one
                'max_instances': 1,  # Only one instance of each job at a time
                'misfire_grace_time': 60,  # Allow 60s late execution
            }
        )

        # Add the main pipeline job
        self.scheduler.add_job(
            self._run_pipeline,
            trigger=IntervalTrigger(seconds=self.settings.poll_interval_seconds),
            id='radar_pipeline',
            name='Market Radar Pipeline',
            replace_existing=True,
        )

        # Set up signal handlers
        if blocking:
            signal.signal(signal.SIGINT, self._handle_shutdown)
            signal.signal(signal.SIGTERM, self._handle_shutdown)

        # Start the scheduler
        self.scheduler.start()
        self._running = True

        logger.info("scheduler_started")

        # Schedule immediate run (in background thread, not blocking)
        self.scheduler.add_job(
            self._run_pipeline,
            id='radar_pipeline_initial',
            name='Initial Pipeline Run',
        )

        if blocking:
            try:
                # Keep the main thread alive
                while self._running:
                    signal.pause()
            except (KeyboardInterrupt, SystemExit):
                self.stop()

    def stop(self) -> None:
        """Stop the scheduler gracefully."""
        logger.info("scheduler_stopping")
        self._running = False

        if self.scheduler:
            self.scheduler.shutdown(wait=True)
            self.scheduler = None

        logger.info("scheduler_stopped")

    def _run_pipeline(self) -> None:
        """Execute the pipeline."""
        try:
            logger.info("pipeline_job_starting")
            result = self.pipeline.run()
            logger.info(
                "pipeline_job_completed",
                events_collected=sum(r.items_collected for r in result.collections),
                detections=len(result.detections),
                alerts_sent=result.alerts_sent,
            )
        except Exception as e:
            logger.error("pipeline_job_error", error=str(e))

    def _handle_shutdown(self, signum, frame) -> None:
        """Handle shutdown signals."""
        logger.info("shutdown_signal_received", signal=signum)
        self.stop()
        sys.exit(0)


def run_scheduler(blocking: bool = True) -> Scheduler:
    """Create and start a scheduler instance."""
    scheduler = Scheduler()
    scheduler.start(blocking=blocking)
    return scheduler
