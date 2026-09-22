"""Тесты статусной модели финальности seen → confirmed(N) → final (п. 3 ТЗ)."""

from connector.indexer.service import resolve_status
from connector.models import TxStatus


def test_seen_before_first_confirmation():
    change = resolve_status(tx_block=101, latest_block=100, finality_depth=12)
    assert change.status == TxStatus.SEEN
    assert change.confirmations == 0


def test_confirmed_below_threshold():
    change = resolve_status(tx_block=100, latest_block=105, finality_depth=12)
    assert change.status == TxStatus.CONFIRMED
    assert change.confirmations == 6


def test_final_at_exact_threshold():
    change = resolve_status(tx_block=100, latest_block=111, finality_depth=12)
    assert change.status == TxStatus.FINAL
    assert change.confirmations == 12


def test_finality_depth_is_per_network_setting():
    # Тот же прогресс цепочки, разные пороги сетей — разные статусы.
    assert resolve_status(100, 118, finality_depth=19).status == TxStatus.FINAL
    assert resolve_status(100, 118, finality_depth=20).status == TxStatus.CONFIRMED


def test_reorged_transaction_becomes_orphaned():
    change = resolve_status(100, 120, finality_depth=12, present_in_chain=False)
    assert change.status == TxStatus.ORPHANED
