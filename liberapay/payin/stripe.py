from __future__ import absolute_import, division, print_function, unicode_literals

from decimal import Decimal

import stripe
import stripe.error

from ..models.exchange_route import ExchangeRoute
from ..utils.currencies import Money
from .common import update_payin, update_payin_transfer


# https://stripe.com/docs/currencies#presentment-currencies
ZERO_DECIMAL_CURRENCIES = """
    BIF CLP DJF GNF JPY KMF KRW MGA PYG RWF UGX VND VUV XAF XOF XPF
""".split()


def int_to_Money(amount, currency):
    currency = currency.upper()
    if currency in ZERO_DECIMAL_CURRENCIES:
        return Money(Decimal(amount), currency)
    return Money(Decimal(amount) / 100, currency)


def Money_to_int(m):
    if m.currency in ZERO_DECIMAL_CURRENCIES:
        return int(m.amount)
    return int(m.amount * 100)


def repr_stripe_error(e):
    """Given a `StripeError` exception, return an error message suitable for display.
    """
    msg = e.user_message or e.code
    return '%s (request ID: %s)' % (msg, e.request_id)


def repr_charge_error(charge):
    """Given a `Charge` object, return an error message suitable for display.
    """
    if charge.status != 'failed':
        return
    return '%s (code %s)' % (charge.failure_message, charge.failure_code)


def destination_charge(db, payin, payer, statement_descriptor):
    """Create a Destination Charge.

    Doc: https://stripe.com/docs/connect/destination-charges

    Destination charges don't have built-in support for processing payments
    "at cost", so we (mis)use transfer reversals to recover the exact amount of
    the Stripe fee.

    """
    assert payer.id == payin.payer
    pt = db.one("SELECT * FROM payin_transfers WHERE payin = %s", (payin.id,))
    destination = db.one("SELECT id FROM payment_accounts WHERE pk = %s", (pt.destination,))
    amount = payin.amount
    route = ExchangeRoute.from_id(payer, payin.route)
    if destination == 'acct_1ChyayFk4eGpfLOC':
        # Stripe rejects the charge if the destination is our own account
        destination = None
    else:
        destination = {'account': destination}
    try:
        charge = stripe.Charge.create(
            amount=Money_to_int(amount),
            currency=amount.currency.lower(),
            customer=route.remote_user_id,
            destination=destination,
            metadata={'payin_id': payin.id},
            source=route.address,
            statement_descriptor=statement_descriptor,
            expand=['balance_transaction'],
            idempotency_key='payin_%i' % payin.id,
        )
    except stripe.error.StripeError as e:
        return update_payin(db, payin.id, '', 'failed', repr_stripe_error(e))
    except Exception as e:
        from liberapay.website import website
        website.tell_sentry(e, {})
        return update_payin(db, payin.id, '', 'failed', str(e))

    bt = charge.balance_transaction
    amount_settled = int_to_Money(bt.amount, bt.currency)
    fee = int_to_Money(bt.fee, bt.currency)
    net_amount = amount_settled - fee

    if destination:
        tr = stripe.Transfer.retrieve(charge.transfer)
        tr.reversals.create(
            amount=bt.fee,
            description="Stripe fee",
            metadata={'payin_id': payin.id},
            idempotency_key='payin_fee_%i' % payin.id,
        )

    status = charge.status
    error = repr_charge_error(charge)

    payin = update_payin(
        db, payin.id, charge.id, status, error,
        amount_settled=amount_settled, fee=fee
    )

    pt_remote_id = getattr(charge, 'transfer', None)
    pt = update_payin_transfer(
        db, pt.id, pt_remote_id, status, error, amount=net_amount
    )

    return payin
