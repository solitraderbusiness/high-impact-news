"""
Price data fetcher for market impact measurement.

Supports multiple data sources with fallback:
- Yahoo Finance (free, delayed)
- Alpha Vantage (free tier available)
- Custom API endpoints

Add your preferred data source by implementing a fetcher class.
"""

import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

import httpx
import structlog

from radar.config import get_settings

logger = structlog.get_logger()


@dataclass
class PricePoint:
    """A single price data point."""
    symbol: str
    timestamp: datetime
    price: float
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[float] = None
    source: str = "unknown"


class BasePriceFetcher(ABC):
    """Base class for price data fetchers."""

    @abstractmethod
    def get_current_price(self, symbol: str) -> Optional[PricePoint]:
        """Get current price for a symbol."""
        pass

    @abstractmethod
    def get_historical_prices(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> List[PricePoint]:
        """Get historical prices for a symbol."""
        pass

    @abstractmethod
    def supports_symbol(self, symbol: str) -> bool:
        """Check if this fetcher supports the given symbol."""
        pass


class YahooFinanceFetcher(BasePriceFetcher):
    """
    Fetches price data from Yahoo Finance.
    Free, but may have rate limits and delays.
    """

    # Symbol mappings from our format to Yahoo format
    SYMBOL_MAP = {
        # Forex
        "EURUSD": "EURUSD=X",
        "GBPUSD": "GBPUSD=X",
        "USDJPY": "USDJPY=X",
        "USDCHF": "USDCHF=X",
        "AUDUSD": "AUDUSD=X",
        "USDCAD": "USDCAD=X",
        "DXY": "DX-Y.NYB",
        # Indices
        "SPX": "^GSPC",
        "SPY": "SPY",
        "NDX": "^NDX",
        "QQQ": "QQQ",
        "DJI": "^DJI",
        "VIX": "^VIX",
        "DAX": "^GDAXI",
        "FTSE": "^FTSE",
        "N225": "^N225",
        # Commodities
        "GOLD": "GC=F",
        "SILVER": "SI=F",
        "OIL": "CL=F",
        "WTI": "CL=F",
        "BRENT": "BZ=F",
        "NATGAS": "NG=F",
        # Crypto
        "BTC": "BTC-USD",
        "ETH": "ETH-USD",
        # Bonds
        "US10Y": "^TNX",
        "US2Y": "^IRX",
    }

    def __init__(self):
        self.timeout = httpx.Timeout(10.0)
        self.base_url = "https://query1.finance.yahoo.com/v8/finance/chart"

    def _get_yahoo_symbol(self, symbol: str) -> Optional[str]:
        """Convert our symbol to Yahoo format."""
        return self.SYMBOL_MAP.get(symbol.upper(), symbol)

    def supports_symbol(self, symbol: str) -> bool:
        """Check if symbol is supported."""
        return symbol.upper() in self.SYMBOL_MAP or symbol.upper().endswith("=X")

    def get_current_price(self, symbol: str) -> Optional[PricePoint]:
        """Get current price from Yahoo Finance."""
        yahoo_symbol = self._get_yahoo_symbol(symbol)

        try:
            url = f"{self.base_url}/{yahoo_symbol}"
            params = {
                "interval": "1m",
                "range": "1d",
            }

            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(url, params=params)
                response.raise_for_status()

            data = response.json()
            result = data.get("chart", {}).get("result", [])

            if not result:
                logger.warning("yahoo_no_data", symbol=symbol)
                return None

            quote = result[0].get("meta", {})
            price = quote.get("regularMarketPrice")

            if price is None:
                return None

            return PricePoint(
                symbol=symbol.upper(),
                timestamp=datetime.utcnow(),
                price=float(price),
                source="yahoo_finance",
            )

        except Exception as e:
            logger.error("yahoo_fetch_error", symbol=symbol, error=str(e))
            return None

    def get_historical_prices(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> List[PricePoint]:
        """Get historical prices from Yahoo Finance."""
        yahoo_symbol = self._get_yahoo_symbol(symbol)
        prices = []

        try:
            url = f"{self.base_url}/{yahoo_symbol}"
            params = {
                "period1": int(start.timestamp()),
                "period2": int(end.timestamp()),
                "interval": "5m",  # 5-minute intervals
            }

            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(url, params=params)
                response.raise_for_status()

            data = response.json()
            result = data.get("chart", {}).get("result", [])

            if not result:
                return prices

            timestamps = result[0].get("timestamp", [])
            quotes = result[0].get("indicators", {}).get("quote", [{}])[0]

            opens = quotes.get("open", [])
            highs = quotes.get("high", [])
            lows = quotes.get("low", [])
            closes = quotes.get("close", [])
            volumes = quotes.get("volume", [])

            for i, ts in enumerate(timestamps):
                if closes[i] is not None:
                    prices.append(PricePoint(
                        symbol=symbol.upper(),
                        timestamp=datetime.fromtimestamp(ts),
                        price=closes[i],
                        open=opens[i] if i < len(opens) else None,
                        high=highs[i] if i < len(highs) else None,
                        low=lows[i] if i < len(lows) else None,
                        close=closes[i],
                        volume=volumes[i] if i < len(volumes) else None,
                        source="yahoo_finance",
                    ))

            return prices

        except Exception as e:
            logger.error("yahoo_historical_error", symbol=symbol, error=str(e))
            return prices


class AlphaVantageFetcher(BasePriceFetcher):
    """
    Fetches price data from Alpha Vantage.
    Requires API key. Free tier: 5 calls/minute, 500/day.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self.timeout = httpx.Timeout(10.0)
        self.base_url = "https://www.alphavantage.co/query"

    def supports_symbol(self, symbol: str) -> bool:
        """Alpha Vantage supports most symbols."""
        return self.api_key is not None

    def get_current_price(self, symbol: str) -> Optional[PricePoint]:
        """Get current price from Alpha Vantage."""
        if not self.api_key:
            return None

        try:
            # Determine function based on symbol type
            if symbol.upper() in ["EURUSD", "GBPUSD", "USDJPY"]:
                # Forex
                from_currency = symbol[:3]
                to_currency = symbol[3:]
                params = {
                    "function": "CURRENCY_EXCHANGE_RATE",
                    "from_currency": from_currency,
                    "to_currency": to_currency,
                    "apikey": self.api_key,
                }
            else:
                # Stock/ETF
                params = {
                    "function": "GLOBAL_QUOTE",
                    "symbol": symbol,
                    "apikey": self.api_key,
                }

            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(self.base_url, params=params)
                response.raise_for_status()

            data = response.json()

            # Parse based on response type
            if "Realtime Currency Exchange Rate" in data:
                rate_data = data["Realtime Currency Exchange Rate"]
                price = float(rate_data.get("5. Exchange Rate", 0))
            elif "Global Quote" in data:
                quote_data = data["Global Quote"]
                price = float(quote_data.get("05. price", 0))
            else:
                return None

            if price == 0:
                return None

            return PricePoint(
                symbol=symbol.upper(),
                timestamp=datetime.utcnow(),
                price=price,
                source="alpha_vantage",
            )

        except Exception as e:
            logger.error("alphavantage_error", symbol=symbol, error=str(e))
            return None

    def get_historical_prices(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> List[PricePoint]:
        """Get historical prices from Alpha Vantage."""
        # Alpha Vantage intraday has limited history
        # Implementation depends on specific needs
        return []


class PriceFetcher:
    """
    Main price fetcher with multiple data source fallback.
    """

    def __init__(self):
        self.settings = get_settings()
        self.fetchers: List[BasePriceFetcher] = []

        # Initialize available fetchers
        self.fetchers.append(YahooFinanceFetcher())

        # Add Alpha Vantage if API key available
        alpha_key = getattr(self.settings, 'alpha_vantage_api_key', None)
        if alpha_key:
            self.fetchers.append(AlphaVantageFetcher(alpha_key))

    def get_current_price(self, symbol: str) -> Optional[PricePoint]:
        """
        Get current price, trying multiple sources.
        """
        for fetcher in self.fetchers:
            if fetcher.supports_symbol(symbol):
                price = fetcher.get_current_price(symbol)
                if price:
                    return price

        logger.warning("no_price_data", symbol=symbol)
        return None

    def get_historical_prices(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> List[PricePoint]:
        """
        Get historical prices, trying multiple sources.
        """
        for fetcher in self.fetchers:
            if fetcher.supports_symbol(symbol):
                prices = fetcher.get_historical_prices(symbol, start, end)
                if prices:
                    return prices

        return []

    def get_price_at_time(
        self,
        symbol: str,
        target_time: datetime,
        tolerance_minutes: int = 5,
    ) -> Optional[PricePoint]:
        """
        Get price at or near a specific time.
        """
        start = target_time - timedelta(minutes=tolerance_minutes)
        end = target_time + timedelta(minutes=tolerance_minutes)

        prices = self.get_historical_prices(symbol, start, end)

        if not prices:
            return None

        # Find closest price to target time
        closest = min(prices, key=lambda p: abs((p.timestamp - target_time).total_seconds()))
        return closest

    def get_prices_for_intervals(
        self,
        symbol: str,
        base_time: datetime,
        intervals_minutes: List[int] = [5, 15, 60, 240, 1440],
    ) -> Dict[int, Optional[PricePoint]]:
        """
        Get prices at multiple intervals from a base time.
        Returns dict mapping interval_minutes -> PricePoint.
        """
        results = {}

        for interval in intervals_minutes:
            target_time = base_time + timedelta(minutes=interval)

            # Don't fetch future prices
            if target_time > datetime.utcnow():
                results[interval] = None
                continue

            price = self.get_price_at_time(symbol, target_time)
            results[interval] = price

        return results
