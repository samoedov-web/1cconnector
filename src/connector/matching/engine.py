"""Matching engine for deterministic tx_hash matching and manual fallback."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from connector.models import Transaction, ExpectedPayment, Match, MatchState


class MatchingService:
    """Deterministic matching by exact tx_hash with manual review fallback.
    
    Path A: Exact tx_hash match → automatic Match creation
    Path B: No unique match → pending state for manual reconciliation
    """
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def attempt_match(self, transaction: Transaction) -> bool:
        """Attempt to match a transaction deterministically.
        
        Returns True if auto-matched, False if manual review needed.
        """
        tx_hash = transaction.tx_hash
        
        # Path A: Deterministic lookup by exact hash equality
        stmt = select(ExpectedPayment).where(ExpectedPayment.expected_tx_hash == tx_hash)
        result = await self.db.execute(stmt)
        candidates = result.scalars().all()
        
        if len(candidates) == 0:
            # No known hash association → manual fallback
            return False
        
        if len(candidates) > 1:
            # Hash matches multiple expectations → conflict → manual fallback
            return False
        
        expected_payment = candidates[0]
        
        # Prerequisite checks before auto-match
        if expected_payment.status.value == "matched":
            return False  # Already matched
        
        if expected_payment.transaction_id is not None:
            return False  # Already linked
        
        # Validate amount matches (with tolerance check if needed)
        tolerance_pct = float(expected_payment.tolerance) if expected_payment.tolerance else 0.0
        if expected_payment.amount:
            diff_pct = abs(float(transaction.amount - expected_payment.amount)) / float(expected_payment.amount)
        else:
            diff_pct = 0
        
        if diff_pct > tolerance_pct:
            return False
        
        # Success: Create deterministic match
        match = Match(
            transaction_id=transaction.id,
            counterparty_id=expected_payment.invoice.counterparty_id if expected_payment.invoice else None,
            contract_id=expected_payment.invoice.contract_id if expected_payment.invoice else None,
            invoice_id=expected_payment.invoice_id,
            allocated_amount=min(transaction.amount, expected_payment.amount),
            state=MatchState.AUTO,
            rule="deterministic_tx_hash",
            matched_by="system"
        )
        self.db.add(match)
        
        # Update expected payment status
        from connector.models import ExpectedPaymentStatus
        expected_payment.status = ExpectedPaymentStatus.MATCHED  # type: ignore
        expected_payment.transaction_id = transaction.id
        
        return True
