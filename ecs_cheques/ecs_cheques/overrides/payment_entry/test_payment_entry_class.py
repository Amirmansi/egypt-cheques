# Copyright (c) 2024, erpcloud.systems and Contributors
# See license.txt
"""
Unit tests for CustomPaymentEntry._sync_amounts_for_same_currency.

These tests verify that exchange rates are only forced to 1 when the shared
account currency equals the company currency, and are left intact when the
accounts share a foreign currency (e.g. ILS/ILS inside a USD company).

Tests run with Python's built-in unittest and do NOT require a live
Frappe/ERPNext instance.
"""
from __future__ import unicode_literals
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Bootstrap minimal stubs so the module can be imported without Frappe.
# ---------------------------------------------------------------------------

def _flt(val, precision=None):
    try:
        v = float(val or 0)
    except (TypeError, ValueError):
        v = 0.0
    if precision is not None:
        v = round(v, precision)
    return v


_frappe_stub = sys.modules.get("frappe")
if _frappe_stub is None:
    _frappe_stub = types.ModuleType("frappe")
    sys.modules["frappe"] = _frappe_stub

# Ensure required attributes exist on the frappe stub
if not hasattr(_frappe_stub, "db"):
    _frappe_stub.db = MagicMock()
if not hasattr(_frappe_stub, "_"):
    _frappe_stub._ = lambda s, *a: s
if not hasattr(_frappe_stub, "whitelist"):
    _frappe_stub.whitelist = lambda fn=None, **kw: (fn if fn else lambda f: f)
if not hasattr(_frappe_stub, "ValidationError"):
    class _VE(Exception):
        pass
    _frappe_stub.ValidationError = _VE
if not hasattr(_frappe_stub, "throw"):
    def _throw(msg, exc=None):
        raise (_frappe_stub.ValidationError)(msg)
    _frappe_stub.throw = _throw
if not hasattr(_frappe_stub, "get_cached_value"):
    _frappe_stub.get_cached_value = MagicMock(return_value=None)

# frappe.utils
_utils_mod = sys.modules.get("frappe.utils")
if _utils_mod is None:
    _utils_mod = types.ModuleType("frappe.utils")
    sys.modules["frappe.utils"] = _utils_mod
if not hasattr(_utils_mod, "flt"):
    _utils_mod.flt = _flt

# frappe.model / frappe.model.document
for _mod_name in ("frappe.model", "frappe.model.document"):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)
if not hasattr(sys.modules["frappe.model.document"], "Document"):
    sys.modules["frappe.model.document"].Document = object

# frappe.desk / frappe.desk.search
for _mod_name in ("frappe.desk", "frappe.desk.search"):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)
if not hasattr(sys.modules["frappe.desk.search"], "sanitize_searchfield"):
    sys.modules["frappe.desk.search"].sanitize_searchfield = lambda s: s

# erpnext stubs needed by payment_entry_class
for _mod_name in (
    "erpnext",
    "erpnext.accounts",
    "erpnext.accounts.doctype",
    "erpnext.accounts.doctype.payment_entry",
    "erpnext.accounts.doctype.payment_entry.payment_entry",
):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)

# Provide a minimal PaymentEntry base class
_pe_mod = sys.modules["erpnext.accounts.doctype.payment_entry.payment_entry"]
if not hasattr(_pe_mod, "PaymentEntry"):
    class _BasePaymentEntry:
        def validate(self):
            pass
        def on_submit(self):
            pass
    _pe_mod.PaymentEntry = _BasePaymentEntry

import frappe  # noqa: E402  (the stub registered above)

from ecs_cheques.ecs_cheques.overrides.payment_entry.payment_entry_class import (  # noqa: E402
    CustomPaymentEntry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry(**kwargs):
    """Return a CustomPaymentEntry instance with only the attributes we care about."""
    entry = CustomPaymentEntry.__new__(CustomPaymentEntry)
    entry.company = kwargs.get("company", "Test Company")
    entry.paid_from_account_currency = kwargs.get("paid_from_account_currency", "ILS")
    entry.paid_to_account_currency = kwargs.get("paid_to_account_currency", "ILS")
    entry.source_exchange_rate = kwargs.get("source_exchange_rate", 3.7)
    entry.target_exchange_rate = kwargs.get("target_exchange_rate", 3.7)
    entry.paid_amount = kwargs.get("paid_amount", 6000.0)
    entry.received_amount = kwargs.get("received_amount", 6000.0)
    return entry


def _patch_company_currency(currency):
    """Patch frappe.get_cached_value to return *currency* for a Company lookup."""
    return patch.object(frappe, "get_cached_value", return_value=currency)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSyncAmountsForSameCurrencyRatesAreOne(unittest.TestCase):
    """Case 1: account currency == company currency → rates must be forced to 1."""

    def test_rates_set_to_1_when_account_currency_equals_company_currency(self):
        entry = _make_entry(
            paid_from_account_currency="ILS",
            paid_to_account_currency="ILS",
            source_exchange_rate=3.7,
            target_exchange_rate=3.7,
        )
        with _patch_company_currency("ILS"):
            entry._sync_amounts_for_same_currency()

        self.assertEqual(entry.source_exchange_rate, 1)
        self.assertEqual(entry.target_exchange_rate, 1)

    def test_rates_set_to_1_when_usd_accounts_in_usd_company(self):
        entry = _make_entry(
            paid_from_account_currency="USD",
            paid_to_account_currency="USD",
            source_exchange_rate=0.5,
            target_exchange_rate=0.5,
        )
        with _patch_company_currency("USD"):
            entry._sync_amounts_for_same_currency()

        self.assertEqual(entry.source_exchange_rate, 1)
        self.assertEqual(entry.target_exchange_rate, 1)


class TestSyncAmountsForSameCurrencyRatesPreserved(unittest.TestCase):
    """Case 2: account currency != company currency (e.g. ILS/ILS in USD company)
    → rates must NOT be overwritten."""

    def test_rates_not_changed_when_ils_accounts_in_usd_company(self):
        """ILS/ILS accounts inside a USD company – exchange rates must stay."""
        entry = _make_entry(
            paid_from_account_currency="ILS",
            paid_to_account_currency="ILS",
            source_exchange_rate=3.7,
            target_exchange_rate=3.7,
        )
        with _patch_company_currency("USD"):
            entry._sync_amounts_for_same_currency()

        self.assertAlmostEqual(entry.source_exchange_rate, 3.7, places=6)
        self.assertAlmostEqual(entry.target_exchange_rate, 3.7, places=6)

    def test_rates_not_changed_when_eur_accounts_in_usd_company(self):
        entry = _make_entry(
            paid_from_account_currency="EUR",
            paid_to_account_currency="EUR",
            source_exchange_rate=1.08,
            target_exchange_rate=1.08,
        )
        with _patch_company_currency("USD"):
            entry._sync_amounts_for_same_currency()

        self.assertAlmostEqual(entry.source_exchange_rate, 1.08, places=6)
        self.assertAlmostEqual(entry.target_exchange_rate, 1.08, places=6)


class TestSyncAmountsReceivedAmount(unittest.TestCase):
    """received_amount must always be synced to paid_amount when currencies match."""

    def test_received_amount_synced_same_company_currency(self):
        entry = _make_entry(
            paid_from_account_currency="ILS",
            paid_to_account_currency="ILS",
            paid_amount=5000.0,
            received_amount=4999.0,
        )
        with _patch_company_currency("ILS"):
            entry._sync_amounts_for_same_currency()

        self.assertAlmostEqual(entry.received_amount, 5000.0, places=6)

    def test_received_amount_synced_foreign_currency(self):
        """Even when rates stay, received_amount must equal paid_amount."""
        entry = _make_entry(
            paid_from_account_currency="ILS",
            paid_to_account_currency="ILS",
            paid_amount=6000.0,
            received_amount=5999.0,
            source_exchange_rate=3.7,
            target_exchange_rate=3.7,
        )
        with _patch_company_currency("USD"):
            entry._sync_amounts_for_same_currency()

        self.assertAlmostEqual(entry.received_amount, 6000.0, places=6)

    def test_no_change_when_already_in_sync(self):
        entry = _make_entry(paid_amount=6000.0, received_amount=6000.0)
        original_received = entry.received_amount
        with _patch_company_currency("ILS"):
            entry._sync_amounts_for_same_currency()
        self.assertEqual(entry.received_amount, original_received)


class TestSyncAmountsEdgeCases(unittest.TestCase):
    """Early-return / no-op scenarios."""

    def test_skipped_when_currencies_differ(self):
        """If paid_from != paid_to, nothing should change."""
        entry = _make_entry(
            paid_from_account_currency="ILS",
            paid_to_account_currency="USD",
            source_exchange_rate=3.7,
            target_exchange_rate=3.7,
            paid_amount=6000.0,
            received_amount=1620.0,
        )
        with _patch_company_currency("ILS"):
            entry._sync_amounts_for_same_currency()

        # No changes expected
        self.assertAlmostEqual(entry.source_exchange_rate, 3.7, places=6)
        self.assertAlmostEqual(entry.target_exchange_rate, 3.7, places=6)
        self.assertAlmostEqual(entry.received_amount, 1620.0, places=6)

    def test_skipped_when_currency_fields_empty(self):
        entry = _make_entry(
            paid_from_account_currency="",
            paid_to_account_currency="",
            source_exchange_rate=3.7,
            target_exchange_rate=3.7,
        )
        with _patch_company_currency("ILS"):
            entry._sync_amounts_for_same_currency()

        self.assertAlmostEqual(entry.source_exchange_rate, 3.7, places=6)

    def test_skipped_when_paid_amount_is_zero(self):
        """Zero paid_amount: received_amount must not be overwritten."""
        entry = _make_entry(
            paid_from_account_currency="ILS",
            paid_to_account_currency="ILS",
            paid_amount=0,
            received_amount=1000.0,
        )
        with _patch_company_currency("ILS"):
            entry._sync_amounts_for_same_currency()

        # received_amount should not be touched
        self.assertAlmostEqual(entry.received_amount, 1000.0, places=6)


if __name__ == "__main__":
    unittest.main()
