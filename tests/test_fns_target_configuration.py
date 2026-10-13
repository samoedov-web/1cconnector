"""FNS target selector configuration tests."""

import pytest

from connector.models import FnsMonitoringTarget
from connector.rates.fns_scheduler import validate_fns_target_config


def make_target(
    *,
    asset="BTC",
    source="fns-krptrate",
    organizer="Binance",
    pair="BTCUSDT",
):
    return FnsMonitoringTarget(
        base_asset=asset,
        source_id=source,
        organizer=organizer,
        trading_pair=pair,
        enabled=True,
        refresh_interval_seconds=3600,
    )


def test_valid_target_config():
    validate_fns_target_config(make_target())


@pytest.mark.parametrize(
    "changes, expected",
    [
        ({"organizer": None}, "organizer"),
        ({"organizer": ""}, "organizer"),
        ({"trading_pair": None}, "trading pair"),
        ({"trading_pair": ""}, "trading pair"),
        ({"source_id": "unknown"}, "source"),
        ({"base_asset": ""}, "base asset"),
        ({"base_asset": "btc"}, "uppercase"),
        ({"trading_pair": "btcusdt"}, "uppercase"),
    ],
)
def test_invalid_target_config(changes, expected):
    target = make_target()

    for key, value in changes.items():
        setattr(target, key, value)

    with pytest.raises(ValueError, match=expected):
        validate_fns_target_config(target)
