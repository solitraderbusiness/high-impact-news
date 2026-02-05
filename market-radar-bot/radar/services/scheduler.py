"""
APScheduler-based scheduler for running the pipeline periodically.
"""

import signal
import sys
from datetime import datetime
from typing import Optional

import pytz
import structlog
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

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

        # Add daily summary job (checks settings every minute to see if it should run)
        self.scheduler.add_job(
            self._check_daily_summary,
            trigger=IntervalTrigger(minutes=1),
            id='daily_summary_checker',
            name='Daily Summary Checker',
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

    def _check_daily_summary(self) -> None:
        """Check if it's time to send the daily summary."""
        try:
            from radar.db import get_db_context
            from radar import storage

            with get_db_context() as db:
                # Check if daily summary is enabled
                enabled = storage.get_app_setting(db, "daily_summary_enabled")
                if enabled != "1":
                    return

                # Get scheduled time
                scheduled_time = storage.get_app_setting(db, "daily_summary_time") or "18:00"
                last_sent = storage.get_app_setting(db, "daily_summary_last_sent")

                # Get current time in Tehran
                tehran_tz = pytz.timezone("Asia/Tehran")
                now_tehran = datetime.now(tehran_tz)
                current_time = now_tehran.strftime("%H:%M")
                today_date = now_tehran.strftime("%Y-%m-%d")

                # Check if it's time to send (within 1 minute of scheduled time)
                scheduled_hour, scheduled_minute = map(int, scheduled_time.split(":"))
                current_hour, current_minute = map(int, current_time.split(":"))

                # Check if we're within the scheduled minute
                is_scheduled_time = (
                    current_hour == scheduled_hour and
                    current_minute == scheduled_minute
                )

                # Check if already sent today
                already_sent_today = last_sent == today_date

                if is_scheduled_time and not already_sent_today:
                    logger.info(
                        "daily_summary_triggered",
                        scheduled_time=scheduled_time,
                        current_time=current_time,
                    )
                    self._send_daily_summary(db)

                    # Mark as sent today
                    storage.set_app_setting(
                        db,
                        "daily_summary_last_sent",
                        today_date,
                        "Last date daily summary was sent"
                    )

        except Exception as e:
            logger.error("daily_summary_check_error", error=str(e))

    def _send_daily_summary(self, db) -> None:
        """Generate and send the daily summary."""
        try:
            from radar import storage
            from radar.notify.daily_summary import DailySummaryGenerator
            from radar.notify.telegram import TelegramNotifier

            # Get configured hours
            hours_str = storage.get_app_setting(db, "daily_summary_hours") or "24"
            hours = int(hours_str)

            # Generate summary
            generator = DailySummaryGenerator()
            summary = generator.generate_summary(db, hours=hours)

            if not summary:
                logger.info("daily_summary_no_data", hours=hours)
                return

            # Send via Telegram
            notifier = TelegramNotifier()
            if not notifier.is_available:
                logger.warning("daily_summary_telegram_not_configured")
                return

            message_text = generator.format_telegram_message(summary)
            result = notifier.send_raw_message(message_text)

            if result.success:
                logger.info(
                    "daily_summary_sent",
                    alerts=summary.total_alerts,
                    assets=len(summary.assets),
                )
            else:
                logger.error("daily_summary_send_failed", error=result.error)

        except Exception as e:
            logger.error("daily_summary_send_error", error=str(e))


def run_scheduler(blocking: bool = True) -> Scheduler:
    """Create and start a scheduler instance."""
    scheduler = Scheduler()
    scheduler.start(blocking=blocking)
    return scheduler
