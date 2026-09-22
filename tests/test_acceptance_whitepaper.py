"""
Acceptance Tests для Whitepaper v2.1.
Каждый тест проверяет конкретное утверждение документа.
Генерируется из формулировок раздела 13 Whitepaper.
"""
import pytest
import re
from connector.models import Role, OperationState, TxStatus, ExpectedPaymentStatus
from connector.services.operation_service import ALLOWED_TRANSITIONS

# --- Раздел 3: Неизменяемость первички ---

def test_wp_3_1_no_private_keys():
    """Whitepaper §3.1: Система не хранит приватные ключи."""
    import os
    import glob
    
    # Поиск по всем .py файлам
    py_files = glob.glob("src/**/*.py", recursive=True)
    forbidden_patterns = [
        r"private_key",
        r"seed_phrase",
        r"mnemonic",
        r"sign_transaction",
        r"send_raw_transaction"
    ]
    
    for file_path in py_files:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            # Игнорируем комментарии и строки документации
            code_lines = [line for line in content.split('\n') if not line.strip().startswith('#')]
            code = '\n'.join(code_lines)
            
            for pattern in forbidden_patterns:
                assert not re.search(pattern, code, re.IGNORECASE), \
                    f"Нарушение Read-Only инварианта в {file_path}: найдено '{pattern}'"

# --- Раздел 5: Регламент оплаты ---

def test_wp_5_2_aml_states():
    """Whitepaper §5.2: Статусы AML должны соответствовать регламенту."""
    required_statuses = {
        ExpectedPaymentStatus.PENDING_AML,
        ExpectedPaymentStatus.AML_APPROVED,
        ExpectedPaymentStatus.AML_REVIEW,
        ExpectedPaymentStatus.AML_REJECTED
    }
    assert required_statuses.issubset(set(ExpectedPaymentStatus))

def test_wp_5_4_timeout_logic():
    """Whitepaper §5.4: Таймер обнаружения транзакции (30 мин)."""
    # Проверяем наличие поля sent_marked_at для отсчета таймера
    from connector.models import ExpectedPayment
    assert hasattr(ExpectedPayment, 'sent_marked_at')
    assert hasattr(ExpectedPayment, 'sent_timeout_alerted')

# --- Раздел 6: Ролевая модель ---

def test_wp_6_1_five_roles():
    """Whitepaper §6.1: Пять ролей (admin, operator, auditor, compliance, treasurer)."""
    required_roles = {
        Role.ADMIN,
        Role.OPERATOR,
        Role.AUDITOR,
        Role.COMPLIANCE,
        Role.TREASURER
    }
    assert required_roles.issubset(set(Role))

# --- Раздел 10: Депозитарная сверка ---

def test_wp_10_2_reconciliation_modes():
    """Whitepaper §10.2: Режимы сверки off/shadow/active."""
    # Проверяем наличие компонента Reconciliation.jsx
    import os
    recon_path = "src/web/src/components/Reconciliation.jsx"
    assert os.path.exists(recon_path), f"Файл {recon_path} отсутствует"
    
    with open(recon_path, 'r') as f:
        content = f.read()
        assert 'off' in content
        assert 'shadow' in content
        assert 'active' in content

# --- Раздел 13: Верификация ---

def test_wp_13_1_state_machine_graph():
    """Whitepaper §13.1: Граф состояний операции должен быть полным."""
    # Проверяем наличие всех канонических состояний
    canonical_states = {
        OperationState.DRAFT,
        OperationState.APPROVED,
        OperationState.SENT,
        OperationState.MATCHED,
        OperationState.CLOSED
    }
    assert canonical_states.issubset(set(OperationState))
    
    # Проверяем, что у каждого состояния есть переходы (кроме тупиков)
    for state in OperationState:
        if state not in [OperationState.CLOSED, OperationState.REJECTED]:
            assert state in ALLOWED_TRANSITIONS, f"Состояние {state} не имеет переходов"
