import pytest
import json
import hashlib
from connector.services.evidence_service import EvidenceService, compute_checksum

@pytest.mark.asyncio
async def test_add_evidence(async_session, sample_operation):
    service = EvidenceService(async_session)
    payload = {"status": "active", "source": "mock_cbr"}
    
    evidence = await service.add_evidence(
        operation=sample_operation,
        evidence_type="registry_check",
        source="mock_cbr",
        source_reference="ref123",
        raw_payload=payload,
    )
    
    assert evidence.id is not None
    assert evidence.sha256_checksum == compute_checksum(payload)
    assert evidence.canonical_payload == json.dumps(payload, sort_keys=True)

@pytest.mark.asyncio
async def test_list_by_operation(async_session, sample_operation):
    service = EvidenceService(async_session)
    payload1 = {"check": 1}
    payload2 = {"check": 2}
    
    await service.add_evidence(sample_operation, "type1", "src", "ref1", payload1)
    await service.add_evidence(sample_operation, "type2", "src", "ref2", payload2)
    
    evidence_list = await service.list_by_operation(sample_operation)
    assert len(evidence_list) == 2
    assert evidence_list[0].evidence_type == "type1"
    assert evidence_list[1].evidence_type == "type2"

def test_checksum_deterministic():
    payload = {"key": "value", "number": 42}
    checksum1 = compute_checksum(payload)
    checksum2 = compute_checksum(payload)
    assert checksum1 == checksum2
    
    # Порядок ключей не влияет благодаря sort_keys
    payload2 = {"number": 42, "key": "value"}
    assert compute_checksum(payload2) == checksum1
