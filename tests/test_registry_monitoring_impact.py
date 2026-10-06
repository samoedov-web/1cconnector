from decimal import Decimal

import pytest

from connector.models import (
    ExpectedPayment,
    ExpectedPaymentStatus,
    Operation,
    OperationState,
    RegistryMonitoringTarget,
)
from connector.services.registry_monitoring import (
    RegistryDiff,
    RegistryFieldChange,
    analyze_registry_impact,
)


def deterioration(subject_reference="7707083893"):
    return RegistryDiff(
        previous_snapshot_id=1,
        current_snapshot_id=2,
        subject_reference=subject_reference,
        previous_status="active",
        current_status="excluded",
        classification="deterioration",
        changes=(
            RegistryFieldChange(
                field="status_result",
                before="active",
                after="excluded",
            ),
        ),
    )


def improvement(subject_reference="7707083893"):
    return RegistryDiff(
        previous_snapshot_id=1,
        current_snapshot_id=2,
        subject_reference=subject_reference,
        previous_status="not_found",
        current_status="active",
        classification="improvement",
        changes=(
            RegistryFieldChange(
                field="status_result",
                before="not_found",
                after="active",
            ),
        ),
    )


@pytest.mark.asyncio
async def test_deterioration_impacts_active_operation_and_payment(
    async_session,
    sample_operation,
    sample_invoice,
):
    sample_operation.state = OperationState.APPROVED

    payment = ExpectedPayment(
        operation_id=sample_operation.id,
        invoice_id=sample_invoice.id,
        to_address="TRegistryImpact1",
        network="tron",
        amount=Decimal("100"),
        currency="USDT",
        tolerance=Decimal("0.005"),
        status=ExpectedPaymentStatus.AML_APPROVED,
    )

    target = RegistryMonitoringTarget(
        counterparty_id=sample_operation.counterparty_id,
        subject_reference="7707083893",
        registry_type="ois",
        source_id="cbr-ois",
    )

    async_session.add_all([payment, target])
    await async_session.flush()

    original_operation_state = sample_operation.state
    original_payment_status = payment.status

    impact = await analyze_registry_impact(
        async_session,
        target=target,
        diff=deterioration(),
    )

    assert impact.has_impact is True
    assert impact.operation_ids == (sample_operation.id,)
    assert impact.expected_payment_ids == (payment.id,)

    # R3D is strictly read-only.
    assert sample_operation.state == original_operation_state
    assert payment.status == original_payment_status


@pytest.mark.asyncio
async def test_payment_without_operation_id_is_still_found_via_invoice_contract(
    async_session,
    sample_operation,
    sample_invoice,
):
    payment = ExpectedPayment(
        operation_id=None,
        invoice_id=sample_invoice.id,
        to_address="TRegistryImpact2",
        network="tron",
        amount=Decimal("200"),
        currency="USDT",
        tolerance=Decimal("0.005"),
        status=ExpectedPaymentStatus.PENDING_AML,
    )

    target = RegistryMonitoringTarget(
        counterparty_id=sample_operation.counterparty_id,
        subject_reference="7707083893",
        registry_type="ois",
        source_id="cbr-ois",
    )

    async_session.add_all([payment, target])
    await async_session.flush()

    impact = await analyze_registry_impact(
        async_session,
        target=target,
        diff=deterioration(),
    )

    assert payment.id in impact.expected_payment_ids


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        ExpectedPaymentStatus.PENDING_AML,
        ExpectedPaymentStatus.AML_REVIEW,
        ExpectedPaymentStatus.AML_APPROVED,
        ExpectedPaymentStatus.SENT,
    ],
)
async def test_active_payment_states_are_impacted(
    async_session,
    sample_operation,
    sample_invoice,
    status,
):
    payment = ExpectedPayment(
        operation_id=sample_operation.id,
        invoice_id=sample_invoice.id,
        to_address=f"TRegistry-{status.value}",
        network="tron",
        amount=Decimal("10"),
        currency="USDT",
        tolerance=Decimal("0.005"),
        status=status,
    )

    target = RegistryMonitoringTarget(
        counterparty_id=sample_operation.counterparty_id,
        subject_reference="7707083893",
        registry_type="ois",
        source_id="cbr-ois",
    )

    async_session.add_all([payment, target])
    await async_session.flush()

    impact = await analyze_registry_impact(
        async_session,
        target=target,
        diff=deterioration(),
    )

    assert payment.id in impact.expected_payment_ids


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        ExpectedPaymentStatus.MATCHED,
        ExpectedPaymentStatus.AML_REJECTED,
        ExpectedPaymentStatus.EXPIRED,
    ],
)
async def test_closed_payment_states_are_not_impacted(
    async_session,
    sample_operation,
    sample_invoice,
    status,
):
    payment = ExpectedPayment(
        operation_id=sample_operation.id,
        invoice_id=sample_invoice.id,
        to_address=f"TClosed-{status.value}",
        network="tron",
        amount=Decimal("10"),
        currency="USDT",
        tolerance=Decimal("0.005"),
        status=status,
    )

    target = RegistryMonitoringTarget(
        counterparty_id=sample_operation.counterparty_id,
        subject_reference="7707083893",
        registry_type="ois",
        source_id="cbr-ois",
    )

    async_session.add_all([payment, target])
    await async_session.flush()

    impact = await analyze_registry_impact(
        async_session,
        target=target,
        diff=deterioration(),
    )

    assert payment.id not in impact.expected_payment_ids


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    [
        OperationState.CLOSED,
        OperationState.REJECTED,
        OperationState.EXPIRED,
        OperationState.ORPHANED,
    ],
)
async def test_terminal_operations_are_not_impacted(
    async_session,
    sample_operation,
    state,
):
    sample_operation.state = state

    target = RegistryMonitoringTarget(
        counterparty_id=sample_operation.counterparty_id,
        subject_reference="7707083893",
        registry_type="ois",
        source_id="cbr-ois",
    )

    async_session.add(target)
    await async_session.flush()

    impact = await analyze_registry_impact(
        async_session,
        target=target,
        diff=deterioration(),
    )

    assert sample_operation.id not in impact.operation_ids


@pytest.mark.asyncio
async def test_improvement_does_not_create_adverse_impact(
    async_session,
    sample_operation,
):
    target = RegistryMonitoringTarget(
        counterparty_id=sample_operation.counterparty_id,
        subject_reference="7707083893",
        registry_type="ois",
        source_id="cbr-ois",
    )

    async_session.add(target)
    await async_session.flush()

    impact = await analyze_registry_impact(
        async_session,
        target=target,
        diff=improvement(),
    )

    assert impact.has_impact is False
    assert impact.operation_ids == ()
    assert impact.expected_payment_ids == ()


@pytest.mark.asyncio
async def test_unlinked_monitoring_target_has_no_canonical_impact(
    async_session,
):
    target = RegistryMonitoringTarget(
        counterparty_id=None,
        subject_reference="7707083893",
        registry_type="ois",
        source_id="cbr-ois",
    )

    async_session.add(target)
    await async_session.flush()

    impact = await analyze_registry_impact(
        async_session,
        target=target,
        diff=deterioration(),
    )

    assert impact.has_impact is False
    assert impact.counterparty_id is None


@pytest.mark.asyncio
async def test_subject_mismatch_is_rejected(
    async_session,
    sample_operation,
):
    target = RegistryMonitoringTarget(
        counterparty_id=sample_operation.counterparty_id,
        subject_reference="7707083893",
        registry_type="ois",
        source_id="cbr-ois",
    )

    async_session.add(target)
    await async_session.flush()

    with pytest.raises(
        ValueError,
        match="subject does not match monitoring target",
    ):
        await analyze_registry_impact(
            async_session,
            target=target,
            diff=deterioration("1234567890"),
        )


@pytest.mark.asyncio
async def test_impact_does_not_include_other_counterparty(
    async_session,
    sample_operation,
    sample_invoice,
    sample_asset,
):
    from connector.models import Counterparty, Contract, Invoice

    other_counterparty = Counterparty(
        name="Other Counterparty",
        onec_ref="other-cp",
    )
    async_session.add(other_counterparty)
    await async_session.flush()

    other_contract = Contract(
        counterparty_id=other_counterparty.id,
        number="OTHER-CONTRACT",
        registration_number="OTHER-REG",
        currency="USD",
    )
    async_session.add(other_contract)
    await async_session.flush()

    other_invoice = Invoice(
        contract_id=other_contract.id,
        number="OTHER-INVOICE",
        amount=Decimal("500"),
        currency="USD",
        crypto_address="TOtherAddress",
    )
    async_session.add(other_invoice)
    await async_session.flush()

    other_operation = Operation(
        invoice_id=other_invoice.id,
        contract_ref="OTHER-CONTRACT",
        counterparty_id=other_counterparty.id,
        asset_id=sample_asset.id,
        network_id=sample_asset.network_id,
        wallet_address="TOtherWallet",
        state=OperationState.APPROVED,
    )

    other_payment = ExpectedPayment(
        operation_id=None,
        invoice_id=other_invoice.id,
        to_address="TOtherPaymentAddress",
        network="tron",
        amount=Decimal("500"),
        currency="USDT",
        tolerance=Decimal("0.005"),
        status=ExpectedPaymentStatus.AML_APPROVED,
    )

    target = RegistryMonitoringTarget(
        counterparty_id=sample_operation.counterparty_id,
        subject_reference="7707083893",
        registry_type="ois",
        source_id="cbr-ois",
    )

    async_session.add_all(
        [
            other_operation,
            other_payment,
            target,
        ]
    )
    await async_session.flush()

    impact = await analyze_registry_impact(
        async_session,
        target=target,
        diff=deterioration(),
    )

    assert other_operation.id not in impact.operation_ids
    assert other_payment.id not in impact.expected_payment_ids


@pytest.mark.asyncio
async def test_impact_results_are_deterministically_sorted(
    async_session,
    sample_operation,
    sample_invoice,
):
    first = ExpectedPayment(
        operation_id=sample_operation.id,
        invoice_id=sample_invoice.id,
        to_address="TSorted-1",
        network="tron",
        amount=Decimal("10"),
        currency="USDT",
        tolerance=Decimal("0.005"),
        status=ExpectedPaymentStatus.PENDING_AML,
    )

    # Second payment needs another invoice because of the
    # (invoice_id, to_address) uniqueness contract and canonical ownership
    # must still resolve to the same counterparty.
    from connector.models import Invoice

    second_invoice = Invoice(
        contract_id=sample_invoice.contract_id,
        number="SECOND-IMPACT-INVOICE",
        amount=Decimal("20"),
        currency="USD",
        crypto_address="TSortedInvoice2",
    )
    async_session.add(second_invoice)
    await async_session.flush()

    second = ExpectedPayment(
        operation_id=None,
        invoice_id=second_invoice.id,
        to_address="TSorted-2",
        network="tron",
        amount=Decimal("20"),
        currency="USDT",
        tolerance=Decimal("0.005"),
        status=ExpectedPaymentStatus.SENT,
    )

    target = RegistryMonitoringTarget(
        counterparty_id=sample_operation.counterparty_id,
        subject_reference="7707083893",
        registry_type="ois",
        source_id="cbr-ois",
    )

    async_session.add_all([second, first, target])
    await async_session.flush()

    impact = await analyze_registry_impact(
        async_session,
        target=target,
        diff=deterioration(),
    )

    assert impact.operation_ids == tuple(sorted(impact.operation_ids))
    assert impact.expected_payment_ids == tuple(
        sorted(impact.expected_payment_ids)
    )
