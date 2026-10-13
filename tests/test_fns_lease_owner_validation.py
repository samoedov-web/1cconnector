"""Validation tests for FNS lease owner tokens."""

from datetime import datetime, timezone

import pytest

from connector.rates.fns_scheduler import (
    acquire_fns_lease,
    complete_fns_lease,
    release_fns_lease,
)


NOW = datetime(2026, 10, 13, 10, tzinfo=timezone.utc)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "owner",
    [
        "",
        "worker-1",
        "not-a-uuid",
        "11111111-1111-1111-8111-111111111111",
        "11111111-1111-4111-8111-11111111111Z",
    ],
)
@pytest.mark.parametrize(
    "operation",
    [
        acquire_fns_lease,
        complete_fns_lease,
        release_fns_lease,
    ],
)
async def test_invalid_owner_rejected(
    async_session,
    owner,
    operation,
):
    with pytest.raises(ValueError):
        await operation(
            async_session,
            target_id=1,
            owner=owner,
            now=NOW,
        )
