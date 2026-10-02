from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


CENT = Decimal("0.01")
ZERO = Decimal("0.00")
HUNDRED = Decimal("100")


def D(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(
            f"Invalid decimal value: {value!r}"
        ) from exc

    if not result.is_finite():
        raise ValueError(
            f"Invalid decimal value: {value!r}"
        )

    return result


def money(value):
    value = D(value)

    if value < ZERO:
        raise ValueError(
            "Money amount cannot be negative."
        )

    return value.quantize(
        CENT,
        rounding=ROUND_HALF_UP,
    )


def percent(value):
    value = D(value)

    if value < ZERO or value > HUNDRED:
        raise ValueError(
            "Percentage must be between 0% and 100%."
        )

    return value


def percent_amount(amount, percentage):
    amount = money(amount)
    percentage = percent(percentage)

    return money(
        amount * percentage / HUNDRED
    )


def validate_percentages(items):
    total = sum(
        (
            percent(percentage)
            for _, percentage in items
        ),
        ZERO,
    )

    if total > HUNDRED:
        raise ValueError(
            "Recipient percentages must total 100% or less."
        )

    return total


def calculate(amount, items):
    """
    Calculate percentage allocations and business remainder.

    items:
        [(recipient_id, percentage), ...]

    Returns:
        shares, business_remainder
    """
    amount = money(amount)
    items = list(items)

    validate_percentages(items)

    shares = []
    allocated = ZERO

    for recipient_id, percentage in items:
        share = percent_amount(
            amount,
            percentage,
        )

        shares.append(
            (recipient_id, share)
        )

        allocated += share

    remainder = money(
        amount - allocated
    )

    if allocated + remainder != amount:
        raise ArithmeticError(
            "Financial allocation does not reconcile."
        )

    return shares, remainder


def calculate_allocations(
    amount,
    allocations,
    flat_fees=(),
):
    """
    Calculate mixed recipient allocations.

    allocations:

        [
            (recipient_id, "PERCENT", value),
            (recipient_id, "FEE", value),
        ]

    PERCENT:
        value is a percentage of gross incoming amount.

    FEE:
        value is a fixed monetary amount paid to
        that recipient.

    flat_fees:

        [
            (fee_id, amount),
        ]

    Returns:

        recipient_allocations,
        fees,
        business_remainder

    Exact accounting identity:

        gross
        =
        recipient allocations
        + configured fees
        + business remainder
    """
    amount = money(amount)
    allocations = list(allocations)
    flat_fees = list(flat_fees)

    seen = set()
    percentage_total = ZERO
    recipient_results = []

    for recipient_id, allocation_type, value in allocations:
        recipient_id = int(recipient_id)

        if recipient_id in seen:
            raise ValueError(
                "A recipient cannot appear more than once."
            )

        seen.add(recipient_id)

        allocation_type = (
            str(allocation_type)
            .strip()
            .upper()
        )

        if allocation_type == "PERCENT":
            allocation_value = percent(value)

            percentage_total += allocation_value

            recipient_amount = percent_amount(
                amount,
                allocation_value,
            )

        elif allocation_type == "FEE":
            allocation_value = money(value)

            recipient_amount = allocation_value

        else:
            raise ValueError(
                "Unsupported allocation type: "
                f"{allocation_type}"
            )

        recipient_results.append(
            (
                recipient_id,
                allocation_type,
                recipient_amount,
            )
        )

    if percentage_total > HUNDRED:
        raise ValueError(
            "Recipient percentages must total "
            "100% or less."
        )

    fees = []
    fee_total = ZERO

    for fee_id, fee_amount in flat_fees:
        fee = money(fee_amount)

        fees.append(
            (fee_id, fee)
        )

        fee_total += fee

    recipient_total = sum(
        (
            value
            for _, _, value in recipient_results
        ),
        ZERO,
    )

    remaining = (
        amount
        - recipient_total
        - fee_total
    )

    if remaining < ZERO:
        raise ValueError(
            "Recipient allocations and flat fees "
            "exceed the incoming amount."
        )

    business_remainder = money(
        remaining
    )

    if (
        recipient_total
        + fee_total
        + business_remainder
        != amount
    ):
        raise ArithmeticError(
            "Financial allocation does not reconcile."
        )

    return (
        recipient_results,
        fees,
        business_remainder,
    )


def calculate_with_fees(
    amount,
    percentage_items,
    flat_fees=(),
):
    """
    Backward-compatible percentage allocation
    plus configured flat fees.

    Returns:
        shares, fees, business_remainder
    """
    amount = money(amount)
    percentage_items = list(
        percentage_items
    )
    flat_fees = list(flat_fees)

    allocations = [
        (
            recipient_id,
            "PERCENT",
            percentage,
        )
        for recipient_id, percentage
        in percentage_items
    ]

    recipient_results, fees, business = (
        calculate_allocations(
            amount,
            allocations,
            flat_fees,
        )
    )

    shares = [
        (recipient_id, value)
        for recipient_id, _, value
        in recipient_results
    ]

    return shares, fees, business
