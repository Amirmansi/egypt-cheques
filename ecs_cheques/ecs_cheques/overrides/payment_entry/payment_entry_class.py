# -*- coding: utf-8 -*-
# Copyright (c) 2024, erpcloud.systems and contributors
# For license information, please see license.txt
"""Custom PaymentEntry override for egypt-cheques.

Issue 1 – Party account currency: ERPNext raises an error when the selected
receivable/payable account has a different currency than the party's existing
GL entries.  The validation is performed inside
``erpnext.accounts.doctype.gl_entry.gl_entry.GLEntry.validate_currency()``
which calls ``erpnext.accounts.party.validate_party_gle_currency``.

We override ``PaymentEntry.validate`` and ``PaymentEntry.on_submit`` to
temporarily replace that function with a no-op so that Payment Entries can be
saved/submitted even when the chosen account currency differs from historical GL
entries.  The GL entries themselves are still created correctly; only the
currency-mismatch check is bypassed.

Issue 2 – Same-currency exchange rate: when ``paid_from_account_currency`` and
``paid_to_account_currency`` are the same we ensure ``received_amount`` is
always equal to ``paid_amount`` and both exchange rates are kept consistent.
"""

import frappe
from erpnext.accounts.doctype.payment_entry.payment_entry import PaymentEntry
from frappe.utils import flt

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _noop(*args, **kwargs):
    """No-operation placeholder for validate_party_gle_currency."""
    return None


def _patch_gle_currency_check():
    """Return (module, original_fn) after replacing validate_party_gle_currency
    with a no-op in the gl_entry module namespace where it was imported."""
    try:
        import erpnext.accounts.doctype.gl_entry.gl_entry as gle_mod
        orig = gle_mod.validate_party_gle_currency
        gle_mod.validate_party_gle_currency = _noop
        return gle_mod, orig
    except (ImportError, AttributeError):
        return None, None


def _restore_gle_currency_check(gle_mod, orig_fn):
    """Restore the original validate_party_gle_currency function."""
    if gle_mod is not None and orig_fn is not None:
        gle_mod.validate_party_gle_currency = orig_fn


# ---------------------------------------------------------------------------
# Custom controller
# ---------------------------------------------------------------------------

class CustomPaymentEntry(PaymentEntry):
    """Subclass of ERPNext PaymentEntry with the following customisations:

    1. Skip ``validate_party_gle_currency`` check so that a different
       receivable/payable account can be used even when the party has prior GL
       entries in a different currency.
    2. Ensure ``received_amount == paid_amount`` when both account currencies
       are identical, preventing accidental drift between the two fields.
    """

    # ------------------------------------------------------------------
    # Validate
    # ------------------------------------------------------------------

    def validate(self):
        gle_mod, orig_fn = _patch_gle_currency_check()
        try:
            super().validate()
        finally:
            _restore_gle_currency_check(gle_mod, orig_fn)

        # Issue 2: same-currency guard
        self._sync_amounts_for_same_currency()

    # ------------------------------------------------------------------
    # Submit
    # ------------------------------------------------------------------

    def on_submit(self):
        gle_mod, orig_fn = _patch_gle_currency_check()
        try:
            super().on_submit()
        finally:
            _restore_gle_currency_check(gle_mod, orig_fn)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _sync_amounts_for_same_currency(self):
        """When paid_from and paid_to accounts share the same currency, ensure
        received_amount == paid_amount.

        Exchange rates are forced to 1 ONLY when the account currency also
        equals the company currency.  If the two accounts share a foreign
        currency (e.g. both ILS inside a USD company), the exchange rates
        must remain as set by ERPNext so that base amounts are converted
        correctly; forcing 1 in that scenario would make USD = ILS.

        ERPNext already handles this inside set_target_exchange_rate/
        set_received_amount, but we add an explicit guard here so that amounts
        are always coherent even if set_amounts() was skipped for some reason.
        """
        if not (self.paid_from_account_currency and self.paid_to_account_currency):
            return
        if self.paid_from_account_currency != self.paid_to_account_currency:
            return

        company_currency = frappe.get_cached_value("Company", self.company, "default_currency")

        # Only force exchange rates to 1 when the shared account currency IS
        # the company currency.  Otherwise leave ERPNext's computed rates intact.
        if self.paid_from_account_currency == company_currency:
            self.source_exchange_rate = 1
            self.target_exchange_rate = 1

        # Sync received_amount – always correct when both sides share a currency
        if flt(self.paid_amount) and flt(self.received_amount) != flt(self.paid_amount):
            self.received_amount = self.paid_amount
