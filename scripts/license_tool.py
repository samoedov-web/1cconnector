#!/usr/bin/env python3
"""Инструмент вендора: выпуск лицензий коннектора.

НЕ входит в поставку клиенту — закрытый ключ хранится только у вендора.

Команды:
  keygen  — сгенерировать пару ключей (license_private.key / public hex)
  issue   — выпустить лицензию пакета (start | business | holding)
  inspect — показать и проверить существующий license.json

Примеры:
  python scripts/license_tool.py keygen --out-dir keys/
  python scripts/license_tool.py issue \
      --private-key keys/license_private.key \
      --tier business --issued-to 'ООО «Вектор Трейд»' --inn 7701234567 \
      --months 12 --out license.json
  python scripts/license_tool.py issue ... --max-wallets 25   # переопределить лимит пакета
  python scripts/license_tool.py inspect license.json --public-key <hex>
"""

from __future__ import annotations

import argparse
import base64
import json
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

# Пресеты пакетов — единственный источник: connector.license.TIER_PRESETS.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from connector.license import TIER_PRESETS, canonical_payload  # noqa: E402


def cmd_keygen(args: argparse.Namespace) -> None:
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    private = Ed25519PrivateKey.generate()
    private_path = out / "license_private.key"
    private_path.write_bytes(
        private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    )
    private_path.chmod(0o600)
    public_hex = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    (out / "license_public.hex").write_text(public_hex + "\n")
    print(f"Закрытый ключ: {private_path} (хранить только у вендора!)")
    print(f"Открытый ключ (в CONNECTOR_LICENSE_PUBLIC_KEY клиента):\n{public_hex}")


def cmd_issue(args: argparse.Namespace) -> None:
    preset = TIER_PRESETS[args.tier]
    valid_from = datetime.now(timezone.utc)
    payload = {
        "license_id": secrets.token_hex(8),
        "tier": args.tier,
        "issued_to": args.issued_to,
        "inn": args.inn or "",
        "issued_at": valid_from.isoformat(timespec="seconds"),
        "valid_until": (valid_from + timedelta(days=30 * args.months)).isoformat(
            timespec="seconds"
        ),
        "grace_days": args.grace_days,
        "max_organizations": (
            args.max_organizations
            if args.max_organizations is not None
            else preset["max_organizations"]
        ),
        "max_wallets": (
            args.max_wallets if args.max_wallets is not None else preset["max_wallets"]
        ),
        "report_updates": preset["report_updates"],
    }
    private = Ed25519PrivateKey.from_private_bytes(Path(args.private_key).read_bytes())
    signature = private.sign(canonical_payload(payload))
    document = {"payload": payload, "signature": base64.b64encode(signature).decode()}
    Path(args.out).write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Лицензия «{args.tier}» для {args.issued_to} до {payload['valid_until']}")
    print(f"Записано: {args.out}")


def cmd_inspect(args: argparse.Namespace) -> None:
    document = json.loads(Path(args.file).read_text(encoding="utf-8"))
    print(json.dumps(document["payload"], ensure_ascii=False, indent=2))
    if args.public_key:
        public = Ed25519PublicKey.from_public_bytes(bytes.fromhex(args.public_key))
        try:
            public.verify(
                base64.b64decode(document["signature"]),
                canonical_payload(document["payload"]),
            )
            print("Подпись: OK")
        except Exception:
            print("Подпись: НЕВЕРНА")
            sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    keygen = sub.add_parser("keygen", help="сгенерировать пару ключей")
    keygen.add_argument("--out-dir", default="keys")
    keygen.set_defaults(func=cmd_keygen)

    issue = sub.add_parser("issue", help="выпустить лицензию")
    issue.add_argument("--private-key", required=True)
    issue.add_argument("--tier", required=True, choices=sorted(TIER_PRESETS))
    issue.add_argument("--issued-to", required=True)
    issue.add_argument("--inn", default="")
    issue.add_argument("--months", type=int, default=12)
    issue.add_argument("--grace-days", type=int, default=14)
    issue.add_argument("--max-organizations", type=int, default=None,
                       help="переопределить лимит пакета")
    issue.add_argument("--max-wallets", type=int, default=None,
                       help="переопределить лимит пакета")
    issue.add_argument("--out", default="license.json")
    issue.set_defaults(func=cmd_issue)

    inspect = sub.add_parser("inspect", help="показать и проверить лицензию")
    inspect.add_argument("file")
    inspect.add_argument("--public-key", default="")
    inspect.set_defaults(func=cmd_inspect)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
