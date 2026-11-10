"""Deterministic demo cohort.

Twelve telecom subscribers cover the full risk spectrum: healthy growth,
price sensitivity, support friction, payment problems, cancellation threats and
quiet loyalty. Chat text is intentionally realistic so the extraction prompts
have meaningful language to work with.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import BillingRecordIn, ChatChannel, ChatMessageIn, ChatRole, CustomerIn
from .timeutil import utcnow


@dataclass(frozen=True)
class SeedCustomer:
    customer: CustomerIn
    billing: tuple[BillingRecordIn, ...]
    messages: tuple[ChatMessageIn, ...]


def _period(now: datetime, months_ago: int) -> str:
    year = now.year
    month = now.month - months_ago
    while month <= 0:
        month += 12
        year -= 1
    return f"{year:04d}-{month:02d}"


def _billing(
    now: datetime,
    invoice: float,
    usage: tuple[float, float, float],
    failures: tuple[int, int, int] = (0, 0, 0),
    late: tuple[int, int, int] = (0, 0, 0),
    overage: tuple[float, float, float] = (0.0, 0.0, 0.0),
    discount: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> tuple[BillingRecordIn, ...]:
    """Build three monthly records, oldest first."""
    records: list[BillingRecordIn] = []
    for index, months_ago in enumerate((2, 1, 0)):
        records.append(
            BillingRecordIn(
                period=_period(now, months_ago),
                invoiced_amount=invoice + overage[index],
                paid_amount=0.0 if failures[index] else invoice + overage[index],
                payment_failures=failures[index],
                late_payments=late[index],
                overage_charges=overage[index],
                discount_amount=discount[index],
                usage_gb=usage[index],
            )
        )
    return tuple(records)


def _chat(
    now: datetime,
    days_ago: int,
    role: ChatRole,
    message: str,
    channel: ChatChannel = "chat",
) -> ChatMessageIn:
    ts = now - timedelta(days=days_ago)
    return ChatMessageIn(role=role, message=message, channel=channel, ts=ts)


def build_seed_customers(now: datetime | None = None) -> list[SeedCustomer]:
    current = now or utcnow()
    seed: list[SeedCustomer] = []

    seed.append(
        SeedCustomer(
            customer=CustomerIn(
                customer_id="CUST-0001",
                name="Olivia Chen",
                email="olivia.chen@example.com",
                plan="premium",
                mrr=89.0,
                tenure_months=42,
                region="us-west",
            ),
            billing=_billing(current, 89.0, (210.0, 218.0, 236.0), discount=(10.0, 10.0, 10.0)),
            messages=(
                _chat(current, 18, "customer", "Everything has been working great since the upgrade, thank you."),
                _chat(current, 17, "agent", "Glad to hear it. Anything else I can help with?"),
                _chat(current, 4, "customer", "Could you explain how the autopay discount applies to my bill?"),
                _chat(current, 3, "agent", "It is applied automatically each cycle and shown as a credit line."),
            ),
        )
    )

    seed.append(
        SeedCustomer(
            customer=CustomerIn(
                customer_id="CUST-0002",
                name="Marcus Reed",
                email="marcus.reed@example.com",
                plan="standard",
                mrr=54.0,
                tenure_months=8,
                region="us-east",
            ),
            billing=_billing(
                current,
                54.0,
                (240.0, 205.0, 196.0),
                failures=(0, 1, 0),
                late=(0, 1, 0),
                overage=(18.0, 24.0, 22.0),
                discount=(0.0, 10.0, 10.0),
            ),
            messages=(
                _chat(current, 12, "customer", "My bill keeps creeping up with these overage charges. It is frustrating."),
                _chat(current, 11, "agent", "I can review the usage breakdown with you."),
                _chat(current, 6, "customer", "This is the second time I have been charged extra. I might cancel if this keeps happening."),
                _chat(current, 2, "customer", "Still waiting for someone to explain the charge."),
            ),
        )
    )

    seed.append(
        SeedCustomer(
            customer=CustomerIn(
                customer_id="CUST-0003",
                name="Priya Patel",
                email="priya.patel@example.com",
                plan="premium",
                mrr=129.0,
                tenure_months=26,
                region="eu-west",
            ),
            billing=_billing(current, 129.0, (420.0, 405.0, 398.0)),
            messages=(
                _chat(current, 15, "customer", "The internet has been down again for two evenings in a row."),
                _chat(current, 14, "agent", "I am sorry about that. We are checking the local node."),
                _chat(current, 9, "customer", "It is still not fixed. This is the third outage this month."),
                _chat(current, 8, "customer", "I want to speak to a manager, this is unacceptable."),
                _chat(current, 1, "customer", "No response from anyone and the connection is still dropping."),
            ),
        )
    )

    seed.append(
        SeedCustomer(
            customer=CustomerIn(
                customer_id="CUST-0004",
                name="Daniel Kim",
                email="daniel.kim@example.com",
                plan="business",
                mrr=249.0,
                tenure_months=18,
                region="us-east",
            ),
            billing=_billing(
                current,
                249.0,
                (980.0, 760.0, 640.0),
                failures=(0, 2, 1),
                late=(1, 2, 1),
                overage=(0.0, 30.0, 45.0),
                discount=(0.0, 0.0, 25.0),
            ),
            messages=(
                _chat(current, 10, "customer", "We are ready to cancel our contract and switch to NovaNet."),
                _chat(current, 9, "agent", "I would like to understand what went wrong before you decide."),
                _chat(current, 5, "customer", "The billing failures and slow support are hurting our business. Leave the contract open for now, but we are evaluating other providers."),
                _chat(current, 1, "customer", "Still unresolved and nobody has called us back."),
            ),
        )
    )

    seed.append(
        SeedCustomer(
            customer=CustomerIn(
                customer_id="CUST-0005",
                name="Sofia Alvarez",
                email="sofia.alvarez@example.com",
                plan="standard",
                mrr=49.0,
                tenure_months=3,
                region="us-south",
            ),
            billing=_billing(
                current,
                49.0,
                (160.0, 150.0, 170.0),
                failures=(1, 1, 1),
                late=(2, 1, 2),
                overage=(8.0, 0.0, 12.0),
            ),
            messages=(
                _chat(current, 7, "customer", "I have been charged twice this month and the payment keeps failing."),
                _chat(current, 6, "agent", "Let me check the invoice and the card on file."),
                _chat(current, 3, "customer", "This is ridiculous, I need a refund and a clear explanation."),
                _chat(current, 1, "customer", "Still waiting and the charge is still there."),
            ),
        )
    )

    seed.append(
        SeedCustomer(
            customer=CustomerIn(
                customer_id="CUST-0006",
                name="Aisha Bello",
                email="aisha.bello@example.com",
                plan="starter",
                mrr=29.0,
                tenure_months=14,
                region="af-west",
            ),
            billing=_billing(current, 29.0, (95.0, 62.0, 48.0), overage=(0.0, 6.0, 0.0)),
            messages=(
                _chat(current, 13, "customer", "The speed has been slow in the evenings and I am paying for more than I use."),
                _chat(current, 12, "agent", "We can compare your usage with the current plan."),
                _chat(current, 4, "customer", "I am thinking about downgrading. Can you show me a cheaper plan?"),
            ),
        )
    )

    seed.append(
        SeedCustomer(
            customer=CustomerIn(
                customer_id="CUST-0007",
                name="Tom Novak",
                email="tom.novak@example.com",
                plan="standard",
                mrr=59.0,
                tenure_months=2,
                region="eu-central",
            ),
            billing=_billing(current, 59.0, (80.0, 66.0, 52.0)),
            messages=(
                _chat(current, 5, "customer", "The router disconnects every evening and the signal is weak in my office."),
                _chat(current, 4, "agent", "Let us run a line check and move the router."),
                _chat(current, 1, "customer", "It is still dropping and I have already tried restarting it twice."),
            ),
        )
    )

    seed.append(
        SeedCustomer(
            customer=CustomerIn(
                customer_id="CUST-0008",
                name="Grace Liu",
                email="grace.liu@example.com",
                plan="business",
                mrr=299.0,
                tenure_months=36,
                region="us-west",
            ),
            billing=_billing(current, 299.0, (600.0, 740.0, 910.0), overage=(0.0, 0.0, 12.0)),
            messages=(
                _chat(current, 20, "customer", "Thanks for the quick support last week, the new office link works perfectly."),
                _chat(current, 19, "agent", "Great to hear. Let us know if the team needs more capacity."),
                _chat(current, 6, "customer", "We are growing and interested in the higher-capacity add-on."),
            ),
        )
    )

    seed.append(
        SeedCustomer(
            customer=CustomerIn(
                customer_id="CUST-0009",
                name="Ahmed Hassan",
                email="ahmed.hassan@example.com",
                plan="standard",
                mrr=64.0,
                tenure_months=20,
                region="me-east",
            ),
            billing=_billing(current, 64.0, (200.0, 195.0, 190.0), late=(0, 1, 1), overage=(0.0, 12.0, 30.0)),
            messages=(
                _chat(current, 11, "customer", "My invoice includes a charge I did not approve. I would like a refund."),
                _chat(current, 10, "agent", "I can open a billing dispute and review the line items."),
                _chat(current, 2, "customer", "I am very disappointed, this has happened before and I am still waiting for the correction."),
            ),
        )
    )

    seed.append(
        SeedCustomer(
            customer=CustomerIn(
                customer_id="CUST-0010",
                name="Emma Wilson",
                email="emma.wilson@example.com",
                plan="premium",
                mrr=99.0,
                tenure_months=60,
                region="us-east",
            ),
            billing=_billing(current, 99.0, (260.0, 264.0, 281.0)),
            messages=(
                _chat(current, 22, "customer", "Thanks, the new mesh setup is working well."),
                _chat(current, 3, "customer", "Is there a loyalty option if I add another room to my plan?"),
            ),
        )
    )

    seed.append(
        SeedCustomer(
            customer=CustomerIn(
                customer_id="CUST-0011",
                name="Liam O'Brien",
                email="liam.obrien@example.com",
                plan="starter",
                mrr=39.0,
                tenure_months=11,
                region="eu-west",
            ),
            billing=_billing(current, 39.0, (120.0, 104.0, 92.0), overage=(0.0, 4.0, 0.0)),
            messages=(
                _chat(current, 8, "customer", "The internet drops every evening around eight and comes back after ten minutes."),
                _chat(current, 7, "agent", "That sounds like local congestion. We can check the channel."),
                _chat(current, 2, "customer", "It happened again last night and nobody has explained why."),
            ),
        )
    )

    seed.append(
        SeedCustomer(
            customer=CustomerIn(
                customer_id="CUST-0012",
                name="Ana Silva",
                email="ana.silva@example.com",
                plan="business",
                mrr=189.0,
                tenure_months=30,
                region="latam",
            ),
            billing=_billing(
                current,
                189.0,
                (700.0, 610.0, 520.0),
                failures=(0, 2, 2),
                late=(1, 2, 2),
                overage=(0.0, 20.0, 35.0),
                discount=(0.0, 0.0, 15.0),
            ),
            messages=(
                _chat(current, 9, "customer", "This is the third time I have escalated and we are considering leaving for SkyLink."),
                _chat(current, 8, "agent", "I understand and I am escalating this to the retention desk."),
                _chat(current, 3, "customer", "Nobody called me back and the payment failure is still unresolved. We may cancel the contract."),
            ),
        )
    )

    return seed
