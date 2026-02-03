"""
Source Discovery.

Automatically discovers and validates new news sources.
Promotes validated sources to the active source pool.
"""

from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse
import re

import structlog
import httpx
from sqlalchemy import select, and_, func
from sqlalchemy.orm import Session

from radar.models import (
    Source, SourcePool, SourceReliability, SourcePoolStatus
)
from radar.config import get_settings

logger = structlog.get_logger()


# Known RSS feed URL patterns for common news sites
RSS_PATTERNS = {
    "reuters.com": [
        "/rssFeed/{section}/",
        "/feed/news/{section}/",
    ],
    "bbc.com": [
        "/news/{section}/rss.xml",
    ],
    "cnbc.com": [
        "/id/{id}/device/rss/rss.html",
    ],
    "bloomberg.com": [
        "/feed/{section}/",
    ],
    "ft.com": [
        "/rss/home/{section}",
    ],
    "wsj.com": [
        "/xml/rss/{section}.xml",
    ],
}


# Sections commonly relevant for market impact
RELEVANT_SECTIONS = [
    "markets", "business", "economy", "finance", "politics",
    "world", "money", "investing", "stocks", "forex",
]


# Known high-quality financial news domains
TRUSTED_DOMAINS = [
    "reuters.com", "bloomberg.com", "ft.com", "wsj.com",
    "cnbc.com", "bbc.com", "nytimes.com", "economist.com",
    "marketwatch.com", "investing.com", "forexlive.com",
    "fxstreet.com", "dailyfx.com", "tradingview.com",
]


class SourceDiscovery:
    """
    Discovers and validates new news sources.
    """

    def __init__(self):
        self.settings = get_settings()
        self.http_client = httpx.Client(
            timeout=15.0,
            follow_redirects=True,
            headers={
                "User-Agent": "MarketRadarBot/1.0 (News Aggregator)",
            }
        )

    def __del__(self):
        if hasattr(self, "http_client"):
            self.http_client.close()

    def add_candidate(
        self,
        db: Session,
        url: str,
        source_type: str = "rss",
        suggested_tier: int = 3,
        discovered_via: str = "manual",
    ) -> Optional[SourcePool]:
        """
        Add a candidate source to the pool.
        """
        # Normalize URL
        url = url.strip().rstrip("/")

        # Check if already exists in pool or active sources
        existing_pool = db.execute(
            select(SourcePool).where(SourcePool.url == url)
        ).scalars().first()

        if existing_pool:
            logger.info("source_already_in_pool", url=url)
            return existing_pool

        existing_source = db.execute(
            select(Source).where(Source.url == url)
        ).scalars().first()

        if existing_source:
            logger.info("source_already_active", url=url)
            return None

        # Create candidate
        candidate = SourcePool(
            url=url,
            source_type=source_type,
            suggested_tier=suggested_tier,
            discovered_via=discovered_via,
            status=SourcePoolStatus.CANDIDATE,
            validation_results={},
        )

        db.add(candidate)
        db.commit()

        logger.info(
            "candidate_added",
            url=url,
            source_type=source_type,
            discovered_via=discovered_via,
        )

        return candidate

    def validate_candidate(
        self,
        db: Session,
        candidate_id: int,
    ) -> Dict[str, Any]:
        """
        Validate a candidate source.
        Checks accessibility, content quality, relevance.
        """
        candidate = db.get(SourcePool, candidate_id)
        if not candidate:
            return {"status": "error", "message": "Candidate not found"}

        results = {
            "accessible": False,
            "has_content": False,
            "content_relevant": False,
            "content_fresh": False,
            "response_time_ms": None,
            "error": None,
            "sample_items": [],
        }

        candidate.status = SourcePoolStatus.VALIDATING
        db.commit()

        try:
            # Test accessibility
            start = datetime.utcnow()
            response = self.http_client.get(candidate.url)
            end = datetime.utcnow()

            results["response_time_ms"] = (end - start).total_seconds() * 1000
            results["accessible"] = response.status_code == 200

            if not results["accessible"]:
                results["error"] = f"HTTP {response.status_code}"
                candidate.status = SourcePoolStatus.INVALID
                candidate.validation_results = results
                db.commit()
                return results

            content = response.text

            # Check if it has RSS/feed content
            if candidate.source_type == "rss":
                results["has_content"] = self._check_rss_content(content, results)
            else:
                results["has_content"] = len(content) > 1000

            if not results["has_content"]:
                results["error"] = "No valid content found"
                candidate.status = SourcePoolStatus.INVALID
                candidate.validation_results = results
                db.commit()
                return results

            # Check relevance
            results["content_relevant"] = self._check_relevance(content)

            # Check freshness (if RSS)
            if candidate.source_type == "rss":
                results["content_fresh"] = self._check_freshness(content)

            # Determine final status
            if results["content_relevant"] and results["content_fresh"]:
                candidate.status = SourcePoolStatus.VALIDATED
            else:
                candidate.status = SourcePoolStatus.INVALID

            candidate.validation_results = results
            candidate.last_validated = datetime.utcnow()
            db.commit()

            logger.info(
                "candidate_validated",
                url=candidate.url,
                status=candidate.status.value,
                results=results,
            )

            return results

        except Exception as e:
            results["error"] = str(e)
            candidate.status = SourcePoolStatus.INVALID
            candidate.validation_results = results
            db.commit()

            logger.error(
                "validation_error",
                url=candidate.url,
                error=str(e),
            )

            return results

    def _check_rss_content(
        self,
        content: str,
        results: Dict,
    ) -> bool:
        """Check if content is valid RSS/Atom feed."""
        # Simple checks for RSS/Atom markers
        is_rss = "<rss" in content or "<feed" in content or "<channel>" in content

        if is_rss:
            # Try to extract sample items
            import re
            items = re.findall(r"<title>([^<]+)</title>", content)[:3]
            results["sample_items"] = items

        return is_rss

    def _check_relevance(self, content: str) -> bool:
        """Check if content is relevant to financial markets."""
        content_lower = content.lower()

        # Keywords that indicate financial relevance
        financial_keywords = [
            "market", "stock", "forex", "economy", "fed", "central bank",
            "interest rate", "inflation", "gdp", "unemployment", "trade",
            "earnings", "investor", "treasury", "bond", "commodity",
            "currency", "dollar", "euro", "tariff", "policy",
        ]

        # Count keyword occurrences
        keyword_count = sum(
            1 for kw in financial_keywords
            if kw in content_lower
        )

        # Need at least 3 different keywords
        return keyword_count >= 3

    def _check_freshness(self, content: str) -> bool:
        """Check if RSS feed has recent content."""
        import re

        # Look for date patterns
        date_patterns = [
            r"<pubDate>([^<]+)</pubDate>",
            r"<updated>([^<]+)</updated>",
            r"<published>([^<]+)</published>",
        ]

        for pattern in date_patterns:
            matches = re.findall(pattern, content)
            if matches:
                # Try to parse the first date
                try:
                    from email.utils import parsedate_to_datetime
                    date = parsedate_to_datetime(matches[0])
                    # Fresh if within last 7 days
                    return (datetime.utcnow() - date.replace(tzinfo=None)) < timedelta(days=7)
                except Exception:
                    pass

        # If we can't parse dates, assume it might be fresh
        return True

    def promote_to_active(
        self,
        db: Session,
        candidate_id: int,
        name: str,
        tier: Optional[int] = None,
    ) -> Optional[Source]:
        """
        Promote a validated candidate to an active source.
        """
        candidate = db.get(SourcePool, candidate_id)
        if not candidate:
            return None

        if candidate.status != SourcePoolStatus.VALIDATED:
            logger.warning(
                "cannot_promote_unvalidated",
                candidate_id=candidate_id,
                status=candidate.status.value,
            )
            return None

        # Create active source
        source = Source(
            name=name,
            url=candidate.url,
            source_type=candidate.source_type,
            tier=tier or candidate.suggested_tier,
            is_enabled=True,
        )

        db.add(source)
        db.flush()  # Get the ID

        # Update candidate
        candidate.status = SourcePoolStatus.PROMOTED
        candidate.promoted_source_id = source.id

        db.commit()

        logger.info(
            "source_promoted",
            candidate_id=candidate_id,
            source_id=source.id,
            name=name,
        )

        return source

    def discover_from_domain(
        self,
        db: Session,
        domain: str,
    ) -> List[SourcePool]:
        """
        Discover potential RSS feeds from a domain.
        """
        discovered = []

        # Try common RSS URL patterns
        patterns = [
            f"https://{domain}/feed",
            f"https://{domain}/rss",
            f"https://{domain}/feed.xml",
            f"https://{domain}/rss.xml",
            f"https://{domain}/feeds/all.rss",
            f"https://feeds.{domain}/",
            f"https://{domain}/news/rss",
        ]

        # Add section-specific patterns
        for section in RELEVANT_SECTIONS:
            patterns.extend([
                f"https://{domain}/feed/{section}",
                f"https://{domain}/rss/{section}",
                f"https://{domain}/{section}/rss",
                f"https://{domain}/{section}/feed",
            ])

        for url in patterns:
            try:
                response = self.http_client.head(url, timeout=5.0)
                if response.status_code == 200:
                    candidate = self.add_candidate(
                        db=db,
                        url=url,
                        source_type="rss",
                        suggested_tier=2 if domain in TRUSTED_DOMAINS else 3,
                        discovered_via=f"domain_scan:{domain}",
                    )
                    if candidate:
                        discovered.append(candidate)
            except Exception:
                continue

        logger.info(
            "domain_discovery_complete",
            domain=domain,
            discovered_count=len(discovered),
        )

        return discovered

    def find_replacement_sources(
        self,
        db: Session,
        failed_source: Source,
    ) -> List[SourcePool]:
        """
        Find replacement candidates when a source fails.
        """
        # Get domain from failed source
        parsed = urlparse(failed_source.url)
        domain = parsed.netloc

        # Try to find validated candidates from same or similar domains
        candidates = db.execute(
            select(SourcePool).where(
                and_(
                    SourcePool.status == SourcePoolStatus.VALIDATED,
                    SourcePool.promoted_source_id.is_(None),
                )
            )
        ).scalars().all()

        # Score candidates by domain similarity
        scored = []
        for candidate in candidates:
            candidate_domain = urlparse(candidate.url).netloc
            score = 0

            # Same domain = high score
            if candidate_domain == domain:
                score = 100
            # Same TLD = medium score
            elif candidate_domain.split(".")[-1] == domain.split(".")[-1]:
                score = 50
            # Trusted domain = base score
            elif any(td in candidate_domain for td in TRUSTED_DOMAINS):
                score = 30

            scored.append((candidate, score))

        # Sort by score descending
        scored.sort(key=lambda x: x[1], reverse=True)

        return [c for c, _ in scored[:5]]  # Return top 5

    def auto_discover(
        self,
        db: Session,
    ) -> int:
        """
        Automatically discover new sources from trusted domains.
        Returns count of new candidates added.
        """
        added = 0

        # Get domains we don't have sources for yet
        existing_domains = set()
        sources = db.execute(select(Source)).scalars().all()
        for source in sources:
            parsed = urlparse(source.url)
            existing_domains.add(parsed.netloc)

        # Try trusted domains we don't have
        for domain in TRUSTED_DOMAINS:
            if domain not in existing_domains and f"www.{domain}" not in existing_domains:
                discovered = self.discover_from_domain(db, domain)
                added += len(discovered)

        logger.info(
            "auto_discovery_complete",
            candidates_added=added,
        )

        return added

    def validate_all_candidates(
        self,
        db: Session,
    ) -> Dict[str, int]:
        """
        Validate all pending candidates.
        Returns counts by status.
        """
        candidates = db.execute(
            select(SourcePool).where(
                SourcePool.status == SourcePoolStatus.CANDIDATE
            )
        ).scalars().all()

        results = {
            "validated": 0,
            "invalid": 0,
            "error": 0,
        }

        for candidate in candidates:
            validation = self.validate_candidate(db, candidate.id)

            if candidate.status == SourcePoolStatus.VALIDATED:
                results["validated"] += 1
            elif candidate.status == SourcePoolStatus.INVALID:
                results["invalid"] += 1
            else:
                results["error"] += 1

        return results

    def get_candidates_for_review(
        self,
        db: Session,
    ) -> List[Dict[str, Any]]:
        """
        Get validated candidates ready for promotion.
        """
        candidates = db.execute(
            select(SourcePool).where(
                and_(
                    SourcePool.status == SourcePoolStatus.VALIDATED,
                    SourcePool.promoted_source_id.is_(None),
                )
            ).order_by(SourcePool.created_at.desc())
        ).scalars().all()

        return [
            {
                "id": c.id,
                "url": c.url,
                "source_type": c.source_type,
                "suggested_tier": c.suggested_tier,
                "discovered_via": c.discovered_via,
                "validation_results": c.validation_results,
                "last_validated": c.last_validated,
            }
            for c in candidates
        ]

    def cleanup_old_candidates(
        self,
        db: Session,
        days_old: int = 30,
    ) -> int:
        """
        Remove old invalid candidates.
        """
        cutoff = datetime.utcnow() - timedelta(days=days_old)

        candidates = db.execute(
            select(SourcePool).where(
                and_(
                    SourcePool.status == SourcePoolStatus.INVALID,
                    SourcePool.created_at < cutoff,
                )
            )
        ).scalars().all()

        count = len(candidates)
        for candidate in candidates:
            db.delete(candidate)

        db.commit()

        logger.info(
            "old_candidates_cleaned",
            removed_count=count,
        )

        return count
