import json
from decimal import Decimal

from .config import settings
from .db import db
from .money import D, money, percent, calculate, calculate_with_fees, calculate_allocations


PROFILE_LIFECYCLES = {
    "DRAFT",
    "VALIDATED",
    "ACTIVE",
    "ARCHIVED",
}

STATUSES = {
    "RECEIVED",
    "VALIDATING",
    "HELD",
    "PAYOUT_PENDING",
    "PAYOUT_PROCESSING",
    "COMPLETED",
    "FAILED",
    "UNKNOWN",
    "REFUNDED",
    "CANCELLED",
}


class MockProvider:
    def create_payout(self, email, amount, idempotency_key):
        return {
            "status": "COMPLETED",
            "external_id": f"MOCK-{idempotency_key}",
        }

    def get_payout_status(self, idempotency_key, external_id=None):
        return {
            "status": "COMPLETED",
            "external_id": external_id or f"MOCK-{idempotency_key}",
        }


provider = MockProvider()


def audit(
    admin,
    action,
    entity_type=None,
    entity_id=None,
    before=None,
    after=None,
):
    with db() as c:
        c.execute(
            """
            INSERT INTO audit_log(
                admin_id,
                action,
                entity_type,
                entity_id,
                before_json,
                after_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(admin),
                action,
                entity_type,
                str(entity_id) if entity_id is not None else None,
                json.dumps(before, default=str)
                if before is not None else None,
                json.dumps(after, default=str)
                if after is not None else None,
            ),
        )


def automation_enabled():
    with db() as c:
        row = c.execute(
            "SELECT value FROM system_state WHERE key='automation_enabled'"
        ).fetchone()

    return bool(row and row["value"] == "1")


def set_automation(enabled, admin):
    with db() as c:
        c.execute(
            """
            INSERT INTO system_state(key, value)
            VALUES('automation_enabled', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            ("1" if enabled else "0",),
        )

    audit(
        admin,
        "SYSTEM_RESUME" if enabled else "SYSTEM_STOP",
    )


def add_sender(email, name=None, profile_id=None, admin="system"):
    email = email.strip().lower()

    with db() as c:
        existing = c.execute(
            "SELECT id FROM senders WHERE lower(email)=?",
            (email,),
        ).fetchone()

        if existing:
            raise ValueError("Sender email already exists.")

        if profile_id is not None:
            profile = c.execute(
                """
                SELECT id, lifecycle, active
                FROM profiles
                WHERE id=?
                """,
                (profile_id,),
            ).fetchone()

            if not profile:
                raise ValueError("Profile not found.")

            if profile["lifecycle"] != "ACTIVE" or not profile["active"]:
                raise ValueError("Sender can only be assigned to an active profile.")

        cur = c.execute(
            """
            INSERT INTO senders(email, name, profile_id)
            VALUES (?, ?, ?)
            """,
            (email, name, profile_id),
        )

        sender_id = cur.lastrowid

    audit(
        admin,
        "CREATE",
        "sender",
        sender_id,
        after={
            "email": email,
            "profile_id": profile_id,
        },
    )

    return sender_id


def add_recipient(name, email, provider_id=None, admin="system"):
    email = email.strip().lower()

    with db() as c:
        existing = c.execute(
            "SELECT id FROM recipients WHERE lower(email)=?",
            (email,),
        ).fetchone()

        if existing:
            raise ValueError("Recipient email already exists.")

        cur = c.execute(
            """
            INSERT INTO recipients(name, email, provider_id)
            VALUES (?, ?, ?)
            """,
            (name.strip(), email, provider_id),
        )

        recipient_id = cur.lastrowid

    audit(
        admin,
        "CREATE",
        "recipient",
        recipient_id,
        after={
            "name": name.strip(),
            "email": email,
            "provider_id": provider_id,
        },
    )

    return recipient_id


def _validate_members(c, members):
    """
    members:
        [(recipient_id, allocation_type, value), ...]

    allocation_type:
        PERCENT -> value is a percentage.
        FEE -> value is a fixed recipient amount.

    Returns normalized members.
    """
    normalized = []
    seen = set()
    percentage_total = D("0")

    for recipient_id, allocation_type, value in members:
        recipient_id = int(recipient_id)

        if recipient_id in seen:
            raise ValueError(
                "A recipient cannot appear twice in a profile."
            )

        seen.add(recipient_id)

        recipient = c.execute(
            """
            SELECT id
            FROM recipients
            WHERE id=? AND active=1
            """,
            (recipient_id,),
        ).fetchone()

        if not recipient:
            raise ValueError(
                f"Recipient {recipient_id} does not exist or is inactive."
            )

        allocation_type = str(
            allocation_type
        ).strip().upper()

        if allocation_type == "PERCENT":
            normalized_value = str(percent(value))
            percentage_total += D(normalized_value)

        elif allocation_type == "FEE":
            normalized_value = str(money(value))

        else:
            raise ValueError(
                "Allocation type must be PERCENT or FEE."
            )

        normalized.append(
            (
                recipient_id,
                allocation_type,
                normalized_value,
            )
        )

    if not normalized:
        raise ValueError(
            "A profile must contain at least one recipient."
        )

    if percentage_total > D("100"):
        raise ValueError(
            "Recipient percentages cannot exceed 100%."
        )

    return normalized


def _validate_fees(fees):
    """
    fees:
        [(name, amount), ...]
    """
    normalized = []
    names = set()

    for name, amount in fees:
        name = str(name).strip()

        if not name:
            raise ValueError("Fee name cannot be empty.")

        if name.lower() in names:
            raise ValueError(
                "Fee names must be unique within a profile."
            )

        names.add(name.lower())

        amount = money(amount)

        normalized.append(
            (name, str(amount))
        )

    return normalized


def create_profile(
    name,
    members,
    fees=(),
    admin="system",
):
    name = name.strip()

    if not name:
        raise ValueError("Profile name cannot be empty.")

    with db() as c:
        existing = c.execute(
            "SELECT id FROM profiles WHERE lower(name)=lower(?)",
            (name,),
        ).fetchone()

        if existing:
            raise ValueError("Profile name already exists.")

        members = _validate_members(c, members)
        fees = _validate_fees(fees)

        cur = c.execute(
            """
            INSERT INTO profiles(
                name,
                active,
                version,
                lifecycle
            )
            VALUES (?, 0, 1, 'DRAFT')
            """,
            (name,),
        )

        profile_id = cur.lastrowid

        for recipient_id, allocation_type, value in members:
            c.execute(
                """
                INSERT INTO profile_members(
                    profile_id,
                    recipient_id,
                    percent,
                    version,
                    allocation_type,
                    value
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    profile_id,
                    recipient_id,
                    value,
                    1,
                    allocation_type,
                    value,
                ),
            )

        for fee_name, fee_amount in fees:
            c.execute(
                """
                INSERT INTO profile_fees(
                    profile_id,
                    name,
                    amount,
                    version
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    profile_id,
                    fee_name,
                    fee_amount,
                    1,
                ),
            )

    audit(
        admin,
        "CREATE",
        "profile",
        profile_id,
        before=None,
        after={
            "name": name,
            "version": 1,
            "lifecycle": "DRAFT",
        },
    )

    return profile_id


def update_profile(
    profile_id,
    name,
    members,
    fees=(),
    admin="system",
):
    name = name.strip()

    if not name:
        raise ValueError("Profile name cannot be empty.")

    with db() as c:
        profile = c.execute(
            """
            SELECT *
            FROM profiles
            WHERE id=?
            """,
            (profile_id,),
        ).fetchone()

        if not profile:
            raise ValueError("Profile not found.")

        if profile["lifecycle"] == "ARCHIVED":
            raise ValueError("Archived profiles cannot be edited.")

        duplicate = c.execute(
            """
            SELECT id
            FROM profiles
            WHERE lower(name)=lower(?)
              AND id != ?
            """,
            (name, profile_id),
        ).fetchone()

        if duplicate:
            raise ValueError("Profile name already exists.")

        members = _validate_members(c, members)
        fees = _validate_fees(fees)

        new_version = profile["version"] + 1

        c.execute(
            """
            UPDATE profiles
            SET name=?,
                version=?,
                lifecycle='DRAFT',
                active=0,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                name,
                new_version,
                profile_id,
            ),
        )

        for recipient_id, allocation_type, value in members:
            c.execute(
                """
                INSERT INTO profile_members(
                    profile_id,
                    recipient_id,
                    percent,
                    version,
                    allocation_type,
                    value
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    profile_id,
                    recipient_id,
                    value,
                    new_version,
                    allocation_type,
                    value,
                ),
            )

        for fee_name, fee_amount in fees:
            c.execute(
                """
                INSERT INTO profile_fees(
                    profile_id,
                    name,
                    amount,
                    version
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    profile_id,
                    fee_name,
                    fee_amount,
                    new_version,
                ),
            )

    audit(
        admin,
        "UPDATE",
        "profile",
        profile_id,
        before={
            "version": profile["version"],
            "lifecycle": profile["lifecycle"],
        },
        after={
            "name": name,
            "version": new_version,
            "lifecycle": "DRAFT",
        },
    )

    return new_version


def get_profile(profile_id, version=None):
    with db() as c:
        profile = c.execute(
            """
            SELECT *
            FROM profiles
            WHERE id=?
            """,
            (profile_id,),
        ).fetchone()

        if not profile:
            raise ValueError("Profile not found.")

        version = (
            profile["version"]
            if version is None
            else int(version)
        )

        members = c.execute(
            """
            SELECT
                pm.recipient_id,
                pm.percent,
                pm.allocation_type,
                pm.value,
                r.name,
                r.email,
                r.provider_id
            FROM profile_members pm
            JOIN recipients r
              ON r.id = pm.recipient_id
            WHERE pm.profile_id=?
              AND pm.version=?
            ORDER BY pm.recipient_id
            """,
            (profile_id, version),
        ).fetchall()

        fees = c.execute(
            """
            SELECT id, name, amount
            FROM profile_fees
            WHERE profile_id=?
              AND version=?
            ORDER BY id
            """,
            (profile_id, version),
        ).fetchall()

    return profile, members, fees


def validate_profile(profile_id):
    profile, members, fees = get_profile(profile_id)

    if profile["lifecycle"] == "ARCHIVED":
        raise ValueError(
            "Archived profiles cannot be activated."
        )

    if not members:
        raise ValueError(
            "Profile must contain at least one recipient."
        )

    percentage_total = D("0")

    for row in members:
        allocation_type = str(
            row["allocation_type"]
        ).strip().upper()

        value = row["value"]

        if allocation_type == "PERCENT":
            percentage_total += percent(value)

        elif allocation_type == "FEE":
            money(value)

        else:
            raise ValueError(
                f"Unsupported allocation type: {allocation_type}"
            )

        if not row["email"]:
            raise ValueError(
                f"Recipient {row['recipient_id']} has no email."
            )

    if percentage_total > D("100"):
        raise ValueError(
            "Recipient percentages exceed 100%."
        )

    for fee in fees:
        if D(fee["amount"]) < D("0"):
            raise ValueError(
                "Profile fee cannot be negative."
            )

    return True

def activate_profile(profile_id, admin="system"):
    validate_profile(profile_id)

    with db() as c:
        profile = c.execute(
            """
            SELECT *
            FROM profiles
            WHERE id=?
            """,
            (profile_id,),
        ).fetchone()

        if not profile:
            raise ValueError("Profile not found.")

        # Prevent multiple active profiles from owning the same sender.
        # Existing sender assignments are checked below when assigning.
        c.execute(
            """
            UPDATE profiles
            SET lifecycle='ACTIVE',
                active=1,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (profile_id,),
        )

    audit(
        admin,
        "PROFILE_ACTIVATE",
        "profile",
        profile_id,
        before={
            "lifecycle": profile["lifecycle"],
            "active": profile["active"],
        },
        after={
            "lifecycle": "ACTIVE",
            "active": 1,
            "version": profile["version"],
        },
    )

    return profile["version"]


def archive_profile(profile_id, admin="system"):
    with db() as c:
        profile = c.execute(
            "SELECT * FROM profiles WHERE id=?",
            (profile_id,),
        ).fetchone()

        if not profile:
            raise ValueError("Profile not found.")

        c.execute(
            """
            UPDATE profiles
            SET lifecycle='ARCHIVED',
                active=0,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (profile_id,),
        )

        # Remove future routing while retaining historical transactions.
        c.execute(
            """
            UPDATE senders
            SET profile_id=NULL,
                updated_at=CURRENT_TIMESTAMP
            WHERE profile_id=?
            """,
            (profile_id,),
        )

    audit(
        admin,
        "PROFILE_ARCHIVE",
        "profile",
        profile_id,
        before={
            "lifecycle": profile["lifecycle"],
            "active": profile["active"],
        },
        after={
            "lifecycle": "ARCHIVED",
            "active": 0,
        },
    )


def assign_sender_profile(sender_id, profile_id, admin="system"):
    with db() as c:
        sender = c.execute(
            """
            SELECT *
            FROM senders
            WHERE id=? AND active=1
            """,
            (sender_id,),
        ).fetchone()

        if not sender:
            raise ValueError("Sender not found or inactive.")

        profile = c.execute(
            """
            SELECT *
            FROM profiles
            WHERE id=?
              AND active=1
              AND lifecycle='ACTIVE'
            """,
            (profile_id,),
        ).fetchone()

        if not profile:
            raise ValueError("Profile must be ACTIVE before assignment.")

        c.execute(
            """
            UPDATE senders
            SET profile_id=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (profile_id, sender_id),
        )

    audit(
        admin,
        "ASSIGN_PROFILE",
        "sender",
        sender_id,
        before={
            "profile_id": sender["profile_id"],
        },
        after={
            "profile_id": profile_id,
        },
    )


def simulate_profile(profile_id, amount):
    amount = money(amount)

    profile, members, fees = get_profile(profile_id)

    percentage_items = [
        (row["recipient_id"], row["percent"])
        for row in members
    ]

    flat_fees = [
        (row["id"], row["amount"])
        for row in fees
    ]

    shares, fee_results, business = calculate_with_fees(
        amount,
        percentage_items,
        flat_fees,
    )

    return {
        "profile_id": profile_id,
        "profile_version": profile["version"],
        "amount": str(amount),
        "shares": [
            {
                "recipient_id": rid,
                "amount": str(value),
            }
            for rid, value in shares
        ],
        "fees": [
            {
                "fee_id": fee_id,
                "amount": str(value),
            }
            for fee_id, value in fee_results
        ],
        "business": str(business),
        "reconciles": (
            sum((value for _, value in shares), Decimal("0.00"))
            + sum((value for _, value in fee_results), Decimal("0.00"))
            + business
            == amount
        ),
    }


def process_incoming(
    external_id,
    sender_email,
    amount,
    currency="USD",
    raw=None,
):
    amount = money(amount)
    currency = currency.upper()
    sender_email = sender_email.strip().lower()

    with db() as c:
        old = c.execute(
            """
            SELECT *
            FROM transactions
            WHERE external_id=?
            """,
            (external_id,),
        ).fetchone()

        if old:
            return {
                "duplicate": True,
                "transaction_id": old["id"],
                "status": old["status"],
            }

        cur = c.execute(
            """
            INSERT INTO transactions(
                external_id,
                provider,
                sender_email,
                amount,
                currency,
                status,
                raw_json
            )
            VALUES (?, 'airtm', ?, ?, ?, 'VALIDATING', ?)
            """,
            (
                external_id,
                sender_email,
                str(amount),
                currency,
                json.dumps(raw or {}),
            ),
        )

        transaction_id = cur.lastrowid

        sender = c.execute(
            """
            SELECT *
            FROM senders
            WHERE lower(email)=?
              AND active=1
            """,
            (sender_email,),
        ).fetchone()

        if not sender or not sender["profile_id"]:
            c.execute(
                """
                UPDATE transactions
                SET status='HELD',
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (transaction_id,),
            )

            return {
                "duplicate": False,
                "transaction_id": transaction_id,
                "status": "HELD",
                "reason": "UNKNOWN_SENDER",
            }

        profile = c.execute(
            """
            SELECT *
            FROM profiles
            WHERE id=?
              AND active=1
              AND lifecycle='ACTIVE'
            """,
            (sender["profile_id"],),
        ).fetchone()

        if not profile:
            c.execute(
                """
                UPDATE transactions
                SET status='HELD',
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (transaction_id,),
            )

            return {
                "duplicate": False,
                "transaction_id": transaction_id,
                "status": "HELD",
                "reason": "PROFILE_INACTIVE",
            }

        members = c.execute(
            """
            SELECT *
            FROM profile_members
            WHERE profile_id=?
              AND version=?
            ORDER BY recipient_id
            """,
            (
                profile["id"],
                profile["version"],
            ),
        ).fetchall()

        fees = c.execute(
            """
            SELECT *
            FROM profile_fees
            WHERE profile_id=?
              AND version=?
            ORDER BY id
            """,
            (
                profile["id"],
                profile["version"],
            ),
        ).fetchall()

        if not members:
            raise ValueError("Active profile has no recipients.")

        for member in members:
            recipient = c.execute(
                """
                SELECT *
                FROM recipients
                WHERE id=?
                  AND active=1
                """,
                (member["recipient_id"],),
            ).fetchone()

            if not recipient:
                c.execute(
                    """
                    UPDATE transactions
                    SET status='HELD',
                        updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (transaction_id,),
                )

                return {
                    "duplicate": False,
                    "transaction_id": transaction_id,
                    "status": "HELD",
                    "reason": "RECIPIENT_INACTIVE",
                }

        allocations = [
            (
                row["recipient_id"],
                row["allocation_type"],
                row["value"],
            )
            for row in members
        ]

        flat_fees = [
            (row["id"], row["amount"])
            for row in fees
        ]

        recipient_results, fee_results, business = calculate_allocations(
            amount,
            allocations,
            flat_fees,
        )

        shares = [
            (recipient_id, value)
            for recipient_id, _, value in recipient_results
        ]

        snapshot = {
            "profile_id": profile["id"],
            "profile_name": profile["name"],
            "profile_version": profile["version"],
            "members": [
                {
                    "recipient_id": row["recipient_id"],
                    "email": c.execute(
                        "SELECT email FROM recipients WHERE id=?",
                        (row["recipient_id"],),
                    ).fetchone()["email"],
                    "allocation_type": row["allocation_type"],
                    "value": row["value"],
                }
                for row in members
            ],
            "fees": [
                {
                    "fee_id": row["id"],
                    "name": row["name"],
                    "amount": row["amount"],
                }
                for row in fees
            ],
        }

        c.execute(
            """
            UPDATE transactions
            SET profile_id=?,
                profile_version=?,
                profile_snapshot_json=?,
                status='PAYOUT_PENDING',
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                profile["id"],
                profile["version"],
                json.dumps(snapshot, sort_keys=True),
                transaction_id,
            ),
        )

        for recipient_id, gross in shares:
            recipient = c.execute(
                """
                SELECT *
                FROM recipients
                WHERE id=?
                """,
                (recipient_id,),
            ).fetchone()

            c.execute(
                """
                INSERT INTO payouts(
                    transaction_id,
                    recipient_id,
                    recipient_email,
                    provider_recipient_id,
                    gross,
                    configured_fee,
                    fee,
                    provider_fee,
                    net,
                    status,
                    idempotency_key
                )
                VALUES (?, ?, ?, ?, ?, '0.00', '0.00',
                        '0.00', ?, 'PENDING', ?)
                """,
                (
                    transaction_id,
                    recipient_id,
                    recipient["email"],
                    recipient["provider_id"],
                    str(gross),
                    str(gross),
                    f"tx:{transaction_id}:recipient:{recipient_id}",
                ),
            )

        for fee_id, fee_amount in fee_results:
            fee = c.execute(
                """
                SELECT name
                FROM profile_fees
                WHERE id=?
                """,
                (fee_id,),
            ).fetchone()

            c.execute(
                """
                INSERT INTO fees(
                    transaction_id,
                    fee_type,
                    name,
                    amount
                )
                VALUES (?, 'CONFIGURED', ?, ?)
                """,
                (
                    transaction_id,
                    fee["name"],
                    str(fee_amount),
                ),
            )

        c.execute(
            """
            INSERT INTO ledger_entries(
                transaction_id,
                entry_type,
                amount,
                currency,
                description
            )
            VALUES (?, 'INCOMING', ?, ?, ?)
            """,
            (
                transaction_id,
                str(amount),
                currency,
                "Incoming payment",
            ),
        )

        for recipient_id, gross in shares:
            payout = c.execute(
                """
                SELECT id
                FROM payouts
                WHERE transaction_id=?
                  AND recipient_id=?
                """,
                (transaction_id, recipient_id),
            ).fetchone()

            c.execute(
                """
                INSERT INTO ledger_entries(
                    transaction_id,
                    entry_type,
                    reference_id,
                    amount,
                    currency,
                    description
                )
                VALUES (?, 'RECIPIENT_PAYOUT', ?, ?, ?, ?)
                """,
                (
                    transaction_id,
                    payout["id"],
                    str(gross),
                    currency,
                    "Recipient allocation",
                ),
            )

        for fee_id, fee_amount in fee_results:
            c.execute(
                """
                INSERT INTO ledger_entries(
                    transaction_id,
                    entry_type,
                    reference_id,
                    amount,
                    currency,
                    description
                )
                VALUES (?, 'CONFIGURED_FEE', ?, ?, ?, ?)
                """,
                (
                    transaction_id,
                    fee_id,
                    str(fee_amount),
                    currency,
                    "Configured profile fee",
                ),
            )

        c.execute(
            """
            INSERT INTO ledger_entries(
                transaction_id,
                entry_type,
                amount,
                currency,
                description
            )
            VALUES (?, 'BUSINESS_ALLOCATION', ?, ?, ?)
            """,
            (
                transaction_id,
                str(business),
                currency,
                "Business remainder",
            ),
        )

    return {
        "duplicate": False,
        "transaction_id": transaction_id,
        "status": "PAYOUT_PENDING",
        "business_remainder": str(business),
    }


def execute_payouts(transaction_id):
    if not automation_enabled():
        return {
            "status": "HELD",
            "reason": "SYSTEM_STOPPED",
        }

    with db() as c:
        tx = c.execute(
            """
            SELECT *
            FROM transactions
            WHERE id=?
            """,
            (transaction_id,),
        ).fetchone()

        if not tx:
            return {"status": "NOT_FOUND"}

        if tx["status"] not in (
            "PAYOUT_PENDING",
            "PAYOUT_PROCESSING",
            "UNKNOWN",
        ):
            return {"status": tx["status"]}

        recovering = tx["status"] == "PAYOUT_PROCESSING"

        c.execute(
            """
            UPDATE transactions
            SET status='PAYOUT_PROCESSING',
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
              AND status IN (
                  'PAYOUT_PENDING',
                  'PAYOUT_PROCESSING'
              )
            """,
            (transaction_id,),
        )

        rows = c.execute(
            """
            SELECT *
            FROM payouts
            WHERE transaction_id=?
              AND status NOT IN ('COMPLETED', 'UNKNOWN')
            ORDER BY id
            """
            ,
            (transaction_id,),
        ).fetchall()

    if recovering:
        for payout in rows:
            if payout["status"] != "PROCESSING":
                continue

            try:
                status_result = provider.get_payout_status(
                    payout["idempotency_key"],
                    payout["external_id"],
                )
                provider_status = str(
                    status_result.get("status", "")
                ).upper()

                with db() as c:
                    if provider_status == "COMPLETED":
                        c.execute(
                            """
                            UPDATE payouts
                            SET status='COMPLETED',
                                external_id=?,
                                last_error=NULL,
                                updated_at=CURRENT_TIMESTAMP
                            WHERE id=?
                              AND status='PROCESSING'
                            """,
                            (
                                status_result.get("external_id")
                                or payout["external_id"],
                                payout["id"],
                            ),
                        )
                    elif provider_status == "FAILED":
                        c.execute(
                            """
                            UPDATE payouts
                            SET status='FAILED',
                                last_error=?,
                                updated_at=CURRENT_TIMESTAMP
                            WHERE id=?
                              AND status='PROCESSING'
                            """,
                            (
                                "Provider confirmed payout failure during recovery.",
                                payout["id"],
                            ),
                        )
                    else:
                        c.execute(
                            """
                            UPDATE payouts
                            SET status='UNKNOWN',
                                last_error=?,
                                updated_at=CURRENT_TIMESTAMP
                            WHERE id=?
                              AND status='PROCESSING'
                            """,
                            (
                                "Provider status could not be resolved during recovery.",
                                payout["id"],
                            ),
                        )

            except Exception as exc:
                with db() as c:
                    c.execute(
                        """
                        UPDATE payouts
                        SET status='UNKNOWN',
                            last_error=?,
                            updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                          AND status='PROCESSING'
                        """,
                        (
                            f"Recovery status lookup failed: {exc}",
                            payout["id"],
                        ),
                    )

        with db() as c:
            rows = c.execute(
                """
                SELECT *
                FROM payouts
                WHERE transaction_id=?
                  AND status NOT IN ('COMPLETED', 'UNKNOWN')
                ORDER BY id
                """,
                (transaction_id,),
            ).fetchall()

    results = []

    for payout in rows:
        if payout["attempts"] >= settings.max_retries:
            results.append("FAILED")
            continue

        with db() as c:
            c.execute(
                """
                UPDATE payouts
                SET attempts=attempts+1,
                    status='PROCESSING',
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                  AND status!='COMPLETED'
                  AND attempts < ?
                """,
                (
                    payout["id"],
                    settings.max_retries,
                ),
            )

            current = c.execute(
                """
                SELECT *
                FROM payouts
                WHERE id=?
                """,
                (payout["id"],),
            ).fetchone()

        if not current or current["status"] == "COMPLETED":
            results.append("COMPLETED")
            continue

        if current["attempts"] > settings.max_retries:
            results.append("FAILED")
            continue

        try:
            if settings.dry_run:
                result = {
                    "status": "COMPLETED",
                    "external_id": (
                        f"DRY-{transaction_id}-{payout['id']}"
                    ),
                }
            else:
                result = provider.create_payout(
                    current["recipient_email"],
                    current["net"],
                    current["idempotency_key"],
                )

            provider_status = str(
                result.get("status", "")
            ).upper()

            if provider_status == "COMPLETED":
                with db() as c:
                    c.execute(
                        """
                        UPDATE payouts
                        SET status='COMPLETED',
                            external_id=?,
                            last_error=NULL,
                            updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                          AND status!='COMPLETED'
                        """,
                        (
                            result.get("external_id"),
                            current["id"],
                        ),
                    )

                results.append("COMPLETED")

            elif provider_status == "UNKNOWN":
                with db() as c:
                    c.execute(
                        """
                        UPDATE payouts
                        SET status='UNKNOWN',
                            external_id=?,
                            last_error=?,
                            updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                          AND status!='COMPLETED'
                        """,
                        (
                            result.get("external_id"),
                            "Provider returned UNKNOWN status.",
                            current["id"],
                        ),
                    )

                results.append("UNKNOWN")

            else:
                error_message = (
                    result.get("error")
                    or f"Provider returned status: {provider_status or 'UNKNOWN'}"
                )

                with db() as c:
                    if current["attempts"] < settings.max_retries:
                        c.execute(
                            """
                            UPDATE payouts
                            SET status='PENDING',
                                last_error=?,
                                updated_at=CURRENT_TIMESTAMP
                            WHERE id=?
                            """,
                            (
                                str(error_message),
                                current["id"],
                            ),
                        )
                        payout_result = "RETRY"
                    else:
                        c.execute(
                            """
                            UPDATE payouts
                            SET status='FAILED',
                                last_error=?,
                                updated_at=CURRENT_TIMESTAMP
                            WHERE id=?
                            """,
                            (
                                str(error_message),
                                current["id"],
                            ),
                        )
                        payout_result = "FAILED"

                results.append(payout_result)

        except Exception as exc:
            with db() as c:
                if current["attempts"] < settings.max_retries:
                    c.execute(
                        """
                        UPDATE payouts
                        SET status='PENDING',
                            last_error=?,
                            updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                        """,
                        (
                            str(exc),
                            current["id"],
                        ),
                    )
                    payout_result = "RETRY"
                else:
                    c.execute(
                        """
                        UPDATE payouts
                        SET status='FAILED',
                            last_error=?,
                            updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                        """,
                        (
                            str(exc),
                            current["id"],
                        ),
                    )
                    payout_result = "FAILED"

            results.append(payout_result)

    with db() as c:
        counts = c.execute(
            """
            SELECT
                SUM(CASE WHEN status='COMPLETED' THEN 1 ELSE 0 END) AS completed,
                SUM(CASE WHEN status='UNKNOWN' THEN 1 ELSE 0 END) AS unknown,
                SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END) AS failed,
                SUM(
                    CASE
                        WHEN status IN ('PENDING', 'PROCESSING')
                        THEN 1
                        ELSE 0
                    END
                ) AS unresolved
            FROM payouts
            WHERE transaction_id=?
            """,
            (transaction_id,),
        ).fetchone()

        if counts["unknown"] > 0:
            final_status = "UNKNOWN"
        elif counts["unresolved"] > 0:
            final_status = "PAYOUT_PENDING"
        elif counts["failed"] > 0:
            final_status = "FAILED"
        else:
            final_status = "COMPLETED"

        c.execute(
            """
            UPDATE transactions
            SET status=?,
                completed_at=CASE
                    WHEN ?='COMPLETED'
                    THEN CURRENT_TIMESTAMP
                    ELSE completed_at
                END,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                final_status,
                final_status,
                transaction_id,
            ),
        )

    return {
        "status": final_status,
        "results": results,
    }

def transaction_details(transaction_id):
    with db() as c:
        tx = c.execute(
            """
            SELECT *
            FROM transactions
            WHERE id=?
            """,
            (transaction_id,),
        ).fetchone()

        payouts = (
            c.execute(
                """
                SELECT *
                FROM payouts
                WHERE transaction_id=?
                ORDER BY id
                """,
                (transaction_id,),
            ).fetchall()
            if tx
            else []
        )

        fees = (
            c.execute(
                """
                SELECT *
                FROM fees
                WHERE transaction_id=?
                ORDER BY id
                """,
                (transaction_id,),
            ).fetchall()
            if tx
            else []
        )

        ledger = (
            c.execute(
                """
                SELECT *
                FROM ledger_entries
                WHERE transaction_id=?
                ORDER BY id
                """,
                (transaction_id,),
            ).fetchall()
            if tx
            else []
        )

    return tx, payouts, fees, ledger
