# 1C Connector — Canonical v2.1 Integration Manifest

**Status:** planning only — no repository changes  
**Analysis basis:** `16branches.zip` (16 branch snapshots), current `review/legal-methodology` snapshot, Whitepaper v2.1 / implementation TЗ v2.1.  
**Date:** 2026-09-17

## 0. Executive decision

**BASE:** `review/legal-methodology`.

The 16 uploaded items are repository snapshots, not Git histories. Therefore this manifest can establish file-level differences and accumulated functionality, but cannot reconstruct exact commit parentage.

Do **not** merge the donor snapshots wholesale.

Use:

- `review/legal-methodology` as source of truth;
- AML snapshots as history/reference;
- depository phase 2→6 and `depository-methodologist-pack` as the main custody/reconciliation donor;
- Qwen as an architectural donor for regulatory/versioning concepts;
- Claude session and `main` as archive/reference.

The correct integration pattern is **selective transplant into the v2.1 canonical model**, not branch merge.

---

# 1. Branch → GAP matrix

| Branch snapshot | Classification | Primary contribution | GAPs | Action |
|---|---|---|---|---|
| main | ARCHIVE | README only | — | ARCHIVE |
| aml-phase-1 | HISTORY/DONOR | base AML adapter/storage | 06 | KEEP AS HISTORY; baseline supersedes |
| aml-phase-2 | HISTORY/DONOR | ExpectedPayment + AML lifecycle | 02,06 | KEEP/ADAPT only missing behavior |
| aml-phase-3 | HISTORY/DONOR | AML UI/RBAC/review | 14,15 | KEEP/ADAPT |
| aml-phase-4 | HISTORY/DONOR | expected payment ↔ TX linking | 06,14 | KEEP/ADAPT |
| aml-phase-5 | DONOR/HISTORY | Crystal + reporting | 06,13 | baseline already contains most |
| aml-owner-decisions | DECISIONS/DONOR | 48h TTL + treasurer “Sent” workflow | 02,06,14 | baseline already contains it; preserve as decision provenance |
| depository-phase-2 | DONOR/HISTORY | custody statements/entries | 18 | TAKE/ADAPT |
| depository-phase-3 | DONOR/HISTORY | DepositoryAdapter + mock | 18 | TAKE/ADAPT |
| depository-phase-4 | DONOR | reconciliation engine | 05,14 | TAKE/ADAPT |
| depository-phase-5 | DONOR | 1C/API/UI/reporting custody | 12,13,18 | TAKE/ADAPT |
| depository-phase-6 | DONOR | custody modes + ADR | 18,20,22 | TAKE/ADAPT methodology |
| depository-methodologist-pack | DONOR/METHODOLOGY | consolidated custody + 12 control cases | 05,18,21,22 | main donor |
| claude-new-session | ARCHIVE/REFERENCE | alternate session state | — | no direct merge |
| qwen-code | ARCHITECTURE DONOR | RegulatoryRule, AssetLegalProfile, PaymentRoute, Operation concept | 01,02,07,13,17,22 | ADAPT |
| review-legal-methodology | **BASE** | integrated AML/accounting/blockchain/1C/reporting baseline | all | **KEEP / source of truth** |

---

# 2. Baseline facts verified from snapshots

`review/legal-methodology` contains:

- AML adapter/base/mock/store;
- Crystal adapter;
- ExpectedPayment;
- AML API;
- AML linking/reporting/owner-decision tests;
- blockchain ingestion;
- accounting/FIFO/revaluation;
- RateSnapshot;
- 1C HTTP synchronization;
- reports;
- registry source abstraction;
- append-only AuditLog.

It does **not** contain the depository implementation files listed in Section 5 below.

The baseline model classes are:

`Network, Asset, Organization, Wallet, Counterparty, CounterpartyAddress, Contract, Invoice, Transaction, RateSnapshot, Match, Lot, DisposalLine, Revaluation, AmlScreening, ExpectedPayment, OnecDocument, User, Alert, AuditLog`

There is no canonical v2.1 `Operation`, `RegistrySnapshot`, `ComplianceDecision`, `IssuerRiskCheck`, `EvidenceLink`, `RegulatoryVersion`, `ReconciliationCase`, `ReviewTask`, or `ReportPackage` entity in the baseline.

---

# 3. TAKE / ADAPT manifest

## INT-01 — Canonical Operation

**GAP:** GAP-01  
**Action:** ADAPT  
**Priority:** P0

### Sources

Qwen:

- `src/connector/models.py`
  - `Operation`
  - `RegulatoryRule`
  - `AssetLegalProfile`
- `migrations/versions/0fe9b49f63a1_add_assetlegalprofile_and_operation_.py`

Baseline competing concepts:

- `Transaction`
- `ExpectedPayment`
- `Match`
- `OnecDocument`

### Target

Create a true v2.1 canonical aggregate in:

`src/connector/models.py`

or an explicitly separated domain module if that is chosen during implementation.

### Do NOT copy Qwen Operation as-is

Qwen Operation is a reporting/regulatory wrapper around `Transaction`:

- `transaction_id`
- regulatory rule reference/version/status
- asset legal profile
- report status
- operation date
- reported timestamp/channel.

It does not implement the complete Whitepaper lifecycle.

### Required target mapping

Canonical Operation must become the lifecycle owner for:

`draft → compliance_pending → registry_checked → aml_checked → issuer_checked → approved → sent → detected → final → matched → accounted → reported → closed`

with exceptions:

`review, rejected, expired, orphaned, provider_degraded, issuer_freeze, manual_reconciliation`

### Dependencies

- Transaction
- ExpectedPayment/AML
- Match
- RegistrySnapshot
- ComplianceDecision
- IssuerRiskCheck
- EvidenceLink
- RegulatoryVersion
- ReconciliationCase
- AccountingDocument/OnecDocument.

### Migration

Qwen migration **must not be applied unchanged**.

A new canonical migration is required after the target model is approved.

Existing records require a deterministic backfill strategy.

### Tests

Preserve:

- existing AML tests;
- existing pipeline/indexer tests;
- Qwen regulatory tests where relevant.

Add:

- operation creation;
- lifecycle transition;
- illegal transition;
- exception transition;
- idempotency;
- audit linkage;
- backfill compatibility.

**Owner decision:** yes, only for unresolved mapping of legacy states to canonical Operation.

---

## INT-02 — RegulatoryVersion / regulatory rule concept

**GAP:** GAP-17  
**Action:** ADAPT  
**Priority:** P0

### Source

Qwen:

`src/connector/models.py`

- `RegulatoryRuleStatus`
- `RegulatoryRule`

Migration:

`migrations/versions/0fe9b49f63a1_add_assetlegalprofile_and_operation_.py`

### Useful source functionality

- version;
- document reference;
- adoption/publication/effective/superseded dates;
- explicit lifecycle;
- binding rule snapshot to operation.

### Target

Canonical v2.1 `RegulatoryVersion`.

Recommended target structure must preserve:

- version identifier;
- source document;
- effective_from;
- status;
- rule/form identity;
- immutable binding from Operation.

Do not require the entire Qwen `RegulatoryRule` object if the final Whitepaper model uses `RegulatoryVersion`.

### Migration

New migration required.

Existing operations need an explicit policy for binding to a regulatory version.

### Owner decision

**Required:** separate table/version history vs configuration-history implementation.

Also decide how historical operations without a known rule version are backfilled.

---

## INT-03 — AssetLegalProfile

**GAP:** GAP-22 / GAP-17 supporting architecture  
**Action:** ADAPT  
**Priority:** P1

### Source

Qwen:

`src/connector/models.py`

- `AssetLegalCategory`
- `AssetLegalProfileStatus`
- `AssetLegalProfile`

### Useful concepts

- asset + network scope;
- valid_from / valid_to;
- classification source;
- counsel opinion reference;
- accounting/tax/revaluation strategy.

### Target

May be retained as a supporting domain model only if it does not become a substitute for `RegulatoryVersion`.

### Risk

Legal classification is methodology/configuration, not an executable legal conclusion.

### Owner decision

Required if the product is to operationalize client-specific legal profiles rather than only store them.

---

## INT-04 — PaymentRoute

**GAP:** GAP-02 / future routing logic  
**Action:** ADAPT  
**Priority:** P1

### Source

Qwen:

`src/connector/models.py` → `PaymentRoute`

Values:

- direct
- bank
- agent
- exchange_organization
- digital_depository

### Target

Map to `ExpectedPayment` or Operation only if the route is required by v2.1 business rules.

Do not introduce routing behavior merely because the enum exists.

### Owner decision

Required before route-specific compliance behavior is implemented.

---

# 4. AML integration manifest

## INT-05 — AML baseline preservation

**GAP:** GAP-06  
**Action:** KEEP  
**Priority:** P1

The baseline already contains the AML implementation accumulated through the AML line.

Verified baseline files:

- `src/connector/aml/base.py`
- `src/connector/aml/store.py`
- `src/connector/aml/flow.py`
- `src/connector/aml/mock_adapter.py`
- `src/connector/aml/crystal_adapter.py`
- `src/connector/web/aml.py`
- `specs/aml-adapter.md`
- `tests/test_aml_adapter.py`
- `tests/test_aml_api.py`
- `tests/test_aml_linking.py`
- `tests/test_aml_owner_decisions.py`
- `tests/test_aml_reporting.py`

### Donor lineage

AML phase 1→5 progressively introduced the above functionality.

Therefore do **not** merge AML phases back into baseline.

### Tests

Preserve all baseline AML tests.

---

## INT-06 — AML owner decisions

**GAP:** GAP-06 / GAP-14  
**Action:** KEEP / TRACEABILITY  
**Priority:** P1

Source:

`aml-owner-decisions`

The owner-decision test is already identical to the baseline:

`tests/test_aml_owner_decisions.py`

It encodes:

- treasurer “mark sent”;
- 48-hour approval expiry;
- 30-minute sent timeout;
- re-screening after expiry;
- RBAC;
- audit records.

Baseline already contains this functionality.

**No code transplant.**

The owner decision itself must remain documented as a business rule and later be mapped into canonical `Operation` / `ComplianceDecision` rather than lost during refactoring.

---

## INT-07 — AML state → ComplianceDecision

**GAP:** GAP-06 / GAP-14  
**Action:** ADAPT  
**Priority:** P1

Current AML:

`AmlScreening` + `ExpectedPaymentStatus` + audit.

Target Whitepaper:

`ComplianceDecision`

Required fields conceptually:

- decision;
- reason_code;
- severity;
- provider;
- provider timestamp;
- policy/rule version;
- human override actor;
- rationale;
- timestamps;
- evidence links.

### Migration

New migration.

Historical AML screenings remain evidence/history; they must not be destructively rewritten.

### Tests

Port existing AML decision tests into canonical decision tests.

---

# 5. Depository / custody manifest

## INT-08 — CustodyStatement / CustodyEntry

**GAP:** GAP-18  
**Action:** TAKE + ADAPT  
**Priority:** P1

### Source

`depository-phase-2` / consolidated in `depository-methodologist-pack`

Exact files:

- `src/connector/custody/store.py`
- `src/connector/models.py`
  - `CustodyStatement`
  - `CustodyEntry`
  - `CustodyOperationType`
- `migrations/versions/04a06f4ac27c_custody_statements_and_entries.py`
- `tests/test_custody_store.py`

### Good functionality

- immutable raw payload;
- SHA-256 checksum;
- statement/entry IDs;
- period;
- source;
- normalized entries;
- operation type;
- optional network/counterparty/tx hash;
- PostgreSQL immutability triggers.

### Target

Keep the concepts but align them with canonical:

`EvidenceLink` + `Operation` + `ReconciliationCase`.

### Migration

The donor migration can be used as structural input but must be rebased onto the actual baseline Alembic head.

Do not copy migration history blindly.

---

## INT-09 — DepositoryAdapter

**GAP:** GAP-18  
**Action:** TAKE + ADAPT  
**Priority:** P1

### Sources

- `src/connector/custody/base.py`
- `src/connector/custody/mock_adapter.py`
- `src/connector/custody/fixtures.py`
- `tests/test_custody_adapter.py`

### Contract

`DepositoryAdapter` provides:

- `meta()`
- `capabilities()`
- `fetch_statements()`
- `fetch_entries()`
- `health()`

Capabilities:

- `has_tx_hash`
- `has_counterparty`
- `has_network`
- `per_tx / aggregated / mixed`

### Target

Integrate with existing `DataSource` / registry architecture.

No real external depository integration.

---

## INT-10 — Reconciliation engine

**GAP:** GAP-05  
**Action:** TAKE + ADAPT  
**Priority:** P1

### Source files

- `src/connector/custody/reconciliation.py`
- `src/connector/custody/service.py`
- `src/connector/models.py`
  - `ReconciliationRun`
  - `ReconciliationResult`
  - `CustodyAssetMapping`
- `migrations/versions/0462b203d108_reconciliation_tables.py`
- `tests/test_reconciliation.py`

### Existing algorithm

1. duplicate detection;
2. exact hash match;
3. tuple match;
4. mismatch classification;
5. aggregate matching;
6. missing-in-custody / missing-on-chain.

### Target

Map output into canonical:

`ReconciliationCase`

Do not simply rename `ReconciliationResult`.

A run/result is execution history; a Case is an actionable business exception.

### Migration

New canonical `ReconciliationCase` migration required.

Donor tables may remain as technical run history if useful.

### Tests to preserve

All 10+ reconciliation scenarios, especially:

- missing custody;
- missing chain;
- amount mismatch;
- date window;
- aggregates;
- partial aggregates;
- duplicates;
- manual resolution;
- deterministic behavior;
- idempotent run;
- reorg/stale handling.

---

## INT-11 — Custody service/API/UI/report

**GAP:** GAP-05 / GAP-13 / GAP-18  
**Action:** TAKE + ADAPT  
**Priority:** P1/P2

### Sources

- `src/connector/custody/service.py`
- `src/connector/custody/onec.py`
- `src/connector/custody/report.py`
- `src/connector/web/reconciliation.py`
- `templates/reports/custody_reconciliation.html`
- `web/src/pages/Reconciliation.jsx`
- `tests/test_reconciliation_api.py`

### Target

Integrate into existing web/report architecture.

Important:

The donor `/reconciliation` domain is **not** the same as existing `src/connector/reports/reconciliation.py`, which currently generates the counterparty reconciliation act.

Do not overwrite that report.

Use distinct domain names:

- counterparty reconciliation;
- custody reconciliation.

### 1C

`src/connector/custody/onec.py` currently queues `reconciliation_info` into `OnecDocument`.

This must be mapped to the Whitepaper v2.1 register architecture.

---

## INT-12 — custody modes

**GAP:** GAP-18 / GAP-22  
**Action:** TAKE  
**Priority:** P1

### Sources

- `tests/test_custody_mode.py`
- `docs/reconciliation.md`
- `docs/adr/ADR-001-custody-first.md`
- settings changes in depository phase 6.

Modes:

- `off`
- `shadow`
- `active`

`active` is deliberately a stub and must remain so.

Do not implement custody-first accounting merely because the donor contains an ADR describing it.

---

# 6. Depository methodology

## INT-13 — Methodologist control examples

**GAP:** GAP-05 / GAP-21 / GAP-22  
**Action:** TAKE  
**Priority:** P1

Source:

`docs/methodologist-review.md`

and:

`scripts/reconciliation_demo.py`

Control cases include:

- perfect match;
- missing payment;
- extra operation;
- fee inside amount;
- date shift;
- aggregation;
- partial aggregate;
- duplicate;
- manual acceptance;
- relative tolerance.

### Important

These are valuable as executable business/regression specifications.

Preserve them when the reconciliation engine is adapted to `ReconciliationCase`.

---

## INT-14 — Open methodological decisions

Source:

`specs/questions.md`

Questions explicitly requiring owner/methodologist resolution:

1. matching by hash when custody ticker differs;
2. default tolerance/date window;
3. whether internal custody fee must be separated from amount;
4. recognition date: custody statement date vs blockchain finality date.

These are **not implementation decisions**.

---

# 7. 1C extension

## INT-15 — 1C extension prototype

**GAP:** GAP-12 / GAP-21  
**Action:** ADAPT  
**Priority:** P1/P2

Source:

`depository-methodologist-pack`

Exact files:

- `onec-extension/STRUCTURE.md`
- `onec-extension/src/CommonModules/КоннекторОбмен/Module.bsl`

### Important finding

The donor contains a much more substantial 1C extension prototype than the baseline.

It includes:

- scheduled synchronization;
- HTTP exchange;
- directory export;
- document import;
- idempotency;
- acknowledgment;
- crypto asset/network/wallet structures;
- `РезультатыСверкиСДепозитарием`.

### But

This is not evidence of a production-ready 1C extension.

It must be treated as a prototype/reference.

### Target

Adapt it to the v2.1 six-register architecture:

- RegistrySnapshot
- ComplianceDecision
- IssuerRisk
- EvidenceLink
- RegulatoryVersion
- ReconciliationCase

plus operation card/reporting requirements.

### Tests

Requires real 1C E2E later.

No claim of E2E completion from these files.

---

# 8. Qwen regulatory/reporting additions

## INT-16 — BankComplianceProfile

**GAP:** GAP-12 / GAP-13  
**Action:** ADAPT / OPTIONAL  
**Priority:** P1

Source:

`src/connector/models.py`

- `BankComplianceProfile`
- `BankComplianceProfileStatus`

Useful:

- versioned bank profile;
- valid_from/valid_to;
- required UНК/KВВО;
- route-specific document package.

Do not make this a core dependency of Operation unless Whitepaper/owner decision requires it.

---

## INT-17 — Qwen reporting additions

**GAP:** GAP-13  
**Action:** ADAPT  
**Priority:** P1

Sources:

- `reports/acceptance_v18.html`
- `reports/acceptance_v18.json`
- `tests/test_regulation.py`
- changes in `src/connector/pipeline.py`
- changes in `src/connector/reports/*`

Use as regression/reference material.

Do not import generated `egg-info` or `test.db`.

The Qwen branch contains an important regression around UНК/KВВО; preserve the corresponding acceptance behavior from baseline rather than copying the Qwen regression wholesale.

---

# 9. Evidence / Audit

## INT-18 — Evidence Vault

**GAP:** GAP-03  
**Action:** ADAPT  
**Priority:** P0/P1

No donor provides a complete canonical Evidence Vault.

Baseline has:

- `Transaction.raw_response`
- checksums;
- AML raw/checksum;
- append-only `AuditLog`;
- PostgreSQL immutability.

Depository donor adds:

- raw custody payload;
- checksum;
- immutable custody records.

### Target

Build a canonical `EvidenceLink` / Evidence Vault layer with:

- evidence_id;
- operation_id;
- source;
- captured_at;
- adapter_version;
- regulatory_version;
- SHA-256;
- immutable version/correction chain.

Corrections must create new versions; no destructive overwrite.

**Do not copy any donor model as the finished Evidence Vault.**

---

# 10. Registry Intelligence

## INT-19 — RegistrySnapshot

**GAP:** GAP-08  
**Action:** BUILD  
**Priority:** P0

No uploaded donor provides a complete implementation of Whitepaper `RegistrySnapshot`.

Baseline has `src/connector/sources/registry.py`, but this is not the canonical snapshot/register required by v2.1.

Target:

- official registry provider interface;
- immutable snapshot;
- source;
- retrieved_at;
- subject identifier;
- status;
- source document/version;
- operation linkage.

**Owner decision required:** exact registry sources and retrieval mechanism.

---

# 11. Issuer Risk

## INT-20 — IssuerRiskCheck

**GAP:** GAP-07  
**Action:** BUILD  
**Priority:** P0

No donor branch provides a complete implementation.

Required target:

- asset/token;
- network;
- contract/address;
- check timestamp;
- source/provider;
- status;
- freeze/blacklist indicators;
- issuer_freeze event;
- review task.

No auto-booking solely on issuer freeze.

**Owner decision required:** data sources/providers and supported assets/networks.

---

# 12. Human Review

## INT-21 — ReviewTask

**GAP:** GAP-14  
**Action:** ADAPT  
**Priority:** P1

Donor inputs:

- AML review flow/UI;
- depository manual reconciliation;
- AML owner-decision tests.

Target:

single queue for:

- AML;
- issuer freeze;
- registry ambiguity;
- reconciliation ambiguity;
- regulatory exceptions.

Fields:

- reason_code;
- severity;
- SLA;
- assigned_role;
- evidence links;
- decision;
- rationale;
- actor;
- timestamps.

No donor provides this as a complete canonical model.

Build canonical `ReviewTask`.

---

# 13. RBAC / SoD

## INT-22

**GAP:** GAP-15  
**Action:** ADAPT  
**Priority:** P1

Useful donor:

`aml-phase-3`

Baseline already contains roles and AML authorization.

Target must add programmatic SoD, not only UI restrictions.

Required rules include:

- treasury cannot approve own compliance decision;
- accountant cannot edit compliance decision;
- auditor read-only;
- violations are audited.

Add negative authorization tests.

---

# 14. Domain events

## INT-23

**GAP:** GAP-19  
**Action:** BUILD / DECISION  
**Priority:** P1

No uploaded branch provides a complete event bus implementation suitable for canonical v2.1.

First define explicit domain events:

- `OperationCreated`
- `ComplianceDecisionRecorded`
- `RegistryChecked`
- `IssuerRiskChecked`
- `BlockchainTxDetected`
- `BlockchainTxFinalized`
- `PaymentMatched`
- `ReconciliationOpened`
- `IssuerFreezeDetected`
- `AccountingDocumentPrepared`
- `ReportPackageGenerated`

**Owner/architecture decision required:** internal queue vs Redis/Kafka/RabbitMQ vs direct orchestration.

Do not add infrastructure before this decision.

---

# 15. Reporting schema

## INT-24

**GAP:** GAP-13  
**Action:** ADAPT  
**Priority:** P1

Baseline already has reporting infrastructure.

Qwen adds regulatory-rule/report concepts.

Depository adds custody report templates.

Target:

schema-driven reports with:

- schema_version;
- effective_from;
- source_document;
- checksum;
- separate data model and form schema.

Do not claim legal sufficiency until an official format is published and validated.

---

# 16. Security

## INT-25

**GAP:** GAP-20  
**Action:** ADAPT  
**Priority:** P1

Baseline has:

- password hashing;
- JWT;
- RBAC;
- DB immutability.

No donor closes:

- sensitive-data encryption at rest;
- secret management;
- rate limiting;
- detailed access audit.

Build these separately.

---

# 17. Tests / CI

## INT-26

**GAP:** GAP-21  
**Action:** TAKE/ADAPT  
**Priority:** P2

Preserve:

AML tests from baseline.

Add/port:

- `tests/test_custody_store.py`
- `tests/test_custody_adapter.py`
- `tests/test_reconciliation.py`
- `tests/test_reconciliation_api.py`
- `tests/test_custody_mode.py`
- methodologist control cases.

Qwen:

`tests/test_regulation.py`

should be treated as regression/reference material.

Still missing:

**real 1C E2E**.

---

# 18. Migration plan

## Donor migrations that must NOT be copied blindly

### AML

Already represented in baseline:

- `b81d5fbc2c1e_aml_screenings.py`
- `2c241b20686a_aml_roles_and_decision_fields.py`

No duplicate migrations.

### Depository

Potential structural donor migrations:

- `04a06f4ac27c_custody_statements_and_entries.py`
- `0462b203d108_reconciliation_tables.py`
- `1793d82bbca1_onec_doc_type_reconciliation_info.py`

These must be rebased onto the baseline Alembic head and reconciled with existing `OnecDocType`.

### Qwen

Do not apply:

`0fe9b49f63a1_add_assetlegalprofile_and_operation_.py`

unchanged.

It is a donor for design/data fields, not the final canonical schema.

---

# 19. Migration order

Recommended:

1. `Operation` + lifecycle
2. `RegulatoryVersion`
3. `EvidenceLink`
4. `ComplianceDecision`
5. `RegistrySnapshot`
6. `IssuerRiskCheck`
7. `ReviewTask`
8. custody tables
9. reconciliation cases
10. 1C register structures
11. reporting schema metadata.

Each migration must be generated against the actual baseline head.

---

# 20. Owner decisions required

These are genuine gates identified from the uploaded material:

### DEC-01 — Regulatory versioning

Separate `RegulatoryVersion` table vs configuration history.

### DEC-02 — Registry sources

Which official registries and retrieval mechanisms are authoritative.

### DEC-03 — Issuer risk provider

CBR / external provider / manual directory / combination.

### DEC-04 — Domain event architecture

Direct orchestration vs internal async queue vs Redis/Kafka/RabbitMQ.

### DEC-05 — Business Rules Engine

JSON/DSL vs structured Python rules; ownership of rules.

### DEC-06 — Custody ticker/hash conflict

Hash match + different custody ticker: automatic match or manual review.

### DEC-07 — Reconciliation defaults

Default absolute/relative tolerance and date window.

### DEC-08 — Custody fee treatment

Whether fee embedded in amount becomes a separate expense.

### DEC-09 — Recognition date

Custody statement date vs blockchain finality date.

### DEC-10 — Route-specific compliance

Whether Qwen `PaymentRoute` changes actual compliance/reporting behavior.

---

# 21. Final implementation order

## P0

1. Canonical Operation
2. State machine
3. RegulatoryVersion
4. EvidenceLink / Evidence Vault
5. audit ↔ operation ↔ evidence
6. RegistrySnapshot
7. IssuerRiskCheck
8. six-register canonical data model

## P1

9. ComplianceDecision
10. ReviewTask
11. ReconciliationCase
12. DepositoryAdapter
13. CustodyStatement / CustodyEntry
14. reconciliation engine
15. RBAC/SoD
16. schema-driven reporting
17. domain event decision/implementation
18. security hardening
19. legal/methodological rule layer

## P2

20. 1C E2E
21. additional regression tests
22. operational hardening
23. future `active` custody mode only after applicable requirements/API exist.

---

# 22. TAKE / ADAPT / KEEP / ARCHIVE summary

### KEEP — already in baseline

- AML phases 1–5 functional result;
- AML owner-decision workflow;
- blockchain ingestion;
- accounting/FIFO;
- RateSnapshot;
- existing 1C connector;
- existing reporting;
- audit trail.

### TAKE

- DepositoryAdapter contract;
- custody storage concepts;
- custody immutable primary data;
- reconciliation algorithm;
- reconciliation tests/control cases;
- custody mode gates;
- methodologist package;
- selected 1C extension prototype elements.

### ADAPT

- Qwen RegulatoryRule;
- Qwen AssetLegalProfile;
- Qwen PaymentRoute;
- Qwen Operation concept;
- AML → ComplianceDecision;
- AML/manual review → ReviewTask;
- custody results → ReconciliationCase;
- donor 1C extension → v2.1 registers;
- donor reports → schema-driven reporting.

### BUILD

- canonical Operation;
- RegistrySnapshot;
- IssuerRiskCheck;
- EvidenceLink;
- RegulatoryVersion;
- ComplianceDecision;
- ReviewTask;
- ReconciliationCase;
- complete v2.1 1C registers;
- domain event architecture.

### ARCHIVE

- `main`;
- Claude session;
- obsolete intermediate AML/depository snapshots after their functionality has been accounted for.

---

# 23. Machine-readable implementation checklist

| ID | GAP | SOURCE | TARGET | ACTION | MIGRATION | TESTS | DECISION | PRIORITY |
|---|---|---|---|---|---|---|---|---|
| INT-01 | 01/02 | Qwen Operation + baseline models | canonical Operation | ADAPT | new | lifecycle + backfill | DEC-01/legacy mapping | P0 |
| INT-02 | 17 | Qwen RegulatoryRule | RegulatoryVersion | ADAPT | new | version binding | DEC-01 | P0 |
| INT-03 | 03 | baseline + custody raw data | EvidenceLink | BUILD | new | immutability/versioning | — | P0 |
| INT-04 | 08 | baseline registry abstraction | RegistrySnapshot | BUILD | new | provider/snapshot | DEC-02 | P0 |
| INT-05 | 07 | none complete | IssuerRiskCheck | BUILD | new | freeze/review | DEC-03 | P0 |
| INT-06 | 06 | baseline AML | ComplianceDecision | ADAPT | new | decision/override | — | P1 |
| INT-07 | 14 | AML + custody manual flows | ReviewTask | ADAPT | new | queue/SLA/RBAC | — | P1 |
| INT-08 | 18 | depo phase 2 | CustodyStatement/Entry | TAKE/ADAPT | new/rebase | store/immutability | — | P1 |
| INT-09 | 18 | depo phase 3 | DepositoryAdapter | TAKE/ADAPT | none | adapter/capabilities | — | P1 |
| INT-10 | 05 | depo phase 4 | ReconciliationCase | TAKE/ADAPT | new | all control cases | DEC-06..09 | P1 |
| INT-11 | 12/13/18 | depo phase 5 | 1C/report/API | TAKE/ADAPT | new | API + later E2E | — | P1 |
| INT-12 | 18/22 | depo phase 6 | custody mode/methodology | TAKE | config | mode gates | DEC-09 | P1 |
| INT-13 | 21/22 | methodologist pack | executable controls | TAKE | none | 12 cases | DEC-06..09 | P1 |
| INT-14 | 15 | AML phase 3 | SoD | ADAPT | maybe none | negative RBAC | — | P1 |
| INT-15 | 19 | none complete | domain events | BUILD | none initially | event contract | DEC-04 | P1 |
| INT-16 | 13 | Qwen + donor reports | report schemas | ADAPT | schema metadata | schema regression | — | P1 |
| INT-17 | 20 | baseline | security hardening | ADAPT | none | security tests | — | P1 |
| INT-18 | 21 | donor tests + baseline | regression/E2E | TAKE/ADAPT | none | 1C E2E missing | — | P2 |

---

# 24. STOP CONDITION

This manifest is a planning artifact.

**Claude Code must not implement anything from this document until the manifest is reviewed and accepted.**

The next implementation session should consume this manifest in dependency order, starting with P0, and must stop at any `DECISION REQUIRED` gate instead of inventing a decision.
