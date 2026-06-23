"""Market data module — longitudinal market data collection and analysis."""

from .collector import MarketCollector, CollectionReport, CollectorOptions
from .sales_collector import SalesCollector
from .sales_store import SalesStore
from .snapshots import SnapshotStore, MarketSnapshotRow, SnapshotQuery
from .aggregators import (
    aggregate_listings,
    aggregate_sales,
    build_snapshot,
    build_snapshots,
    ListingAggregates,
    SalesAggregates,
    MarketSnapshot,
)
from .fetchers import (
    fetch_market_data_for_card,
    fetch_market_data_for_variant,
    fetch_market_data_batch,
    MarketFetchResult,
)

__all__ = [
    "MarketCollector",
    "CollectionReport",
    "CollectorOptions",
    "SalesCollector",
    "SalesStore",
    "SnapshotStore",
    "MarketSnapshotRow",
    "SnapshotQuery",
    "aggregate_listings",
    "aggregate_sales",
    "build_snapshot",
    "build_snapshots",
    "ListingAggregates",
    "SalesAggregates",
    "MarketSnapshot",
    "fetch_market_data_for_card",
    "fetch_market_data_for_variant",
    "fetch_market_data_batch",
    "MarketFetchResult",
]
