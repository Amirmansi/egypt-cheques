# Copyright (c) 2021, erpcloud.systems and Contributors
# See license.txt
"""
Unit tests for _compute_payment_entry_amounts in multiple_cheque_entry.py.

These tests verify the fix for incorrect source_exchange_rate / paid_amount
assignments when creating a Payment Entry from Multiple Cheque Entry.

The scenario described in the issue
-------------------------------------
  Company currency        : ILS
  paid_from account       : party receivable  (currency = ILS)
  paid_to   account       : cheque wallet      (currency = USD)
  Cheque amount entered   : 1 000 USD  →  row.paid_amount = 1 000
  stored_exchange_rate    : 3.159059          (USD → ILS)

Expected Payment Entry
  paid_amount             : 3 159.059 ILS  (from party ILS account)
  received_amount         : 1 000     USD  (into USD cheque wallet)
  source_exchange_rate    : 1          (ILS → ILS = 1)
  target_exchange_rate    : 3.159059   (USD → ILS)

Wrong behaviour (before fix)
  paid_from_account_currency was USD  →  "Paid Amount (USD) = 3 159.059"
"""

import sys
import types
import unittest

# ---------------------------------------------------------------------------
# Bootstrap a minimal frappe stub so the module can be imported without a
# running Frappe instance.
# ---------------------------------------------------------------------------

def _make_frappe_stub():
	mod = types.ModuleType("frappe")
	mod.db = None                          # not used in pure-computation tests
	mod._ = lambda s, *a: s
	mod.whitelist = lambda fn=None, **kw: (fn if fn else lambda f: f)
	class _VE(Exception):
		pass
	mod.ValidationError = _VE
	mod.throw = lambda msg, exc=None: (_ for _ in ()).throw((exc or _VE)(msg))
	import unittest.mock as _m
	mod.get_cached_value = _m.MagicMock()
	mod.get_all = _m.MagicMock(return_value=[])
	return mod

_frappe = _make_frappe_stub()
sys.modules.setdefault("frappe", _frappe)
sys.modules.setdefault("frappe.model", types.ModuleType("frappe.model"))
_doc_mod = types.ModuleType("frappe.model.document")
_doc_mod.Document = object
sys.modules.setdefault("frappe.model.document", _doc_mod)
sys.modules.setdefault("frappe.desk", types.ModuleType("frappe.desk"))
_ds = types.ModuleType("frappe.desk.search")
_ds.sanitize_searchfield = lambda s: s
sys.modules.setdefault("frappe.desk.search", _ds)

_utils = types.ModuleType("frappe.utils")

def _flt(val, precision=None):
	try:
		v = float(val or 0)
	except (TypeError, ValueError):
		v = 0.0
	return round(v, precision) if precision is not None else v

_utils.flt = _flt
_utils.nowdate = lambda: "2024-01-01"
import unittest.mock as _mock
for _attr in ("getdate", "get_url", "now", "nowtime", "get_time", "today",
              "get_datetime", "add_days", "add_to_date"):
	setattr(_utils, _attr, _mock.MagicMock())
sys.modules["frappe.utils"] = _utils

# Now import the functions under test.
from ecs_cheques.ecs_cheques.doctype.multiple_cheque_entry.multiple_cheque_entry import (  # noqa: E402
	_compute_payment_entry_amounts,
	_get_account_currency_db,
	create_payment_entry_from_cheque,
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestComputePaymentEntryAmounts(unittest.TestCase):
	"""Verify _compute_payment_entry_amounts for all standard scenarios."""

	# ------------------------------------------------------------------
	# Scenario from the issue
	# ------------------------------------------------------------------

	def test_receive_cross_currency_ils_from_usd_to(self):
		"""
		Receive: paid_from = ILS (party), paid_to = USD (cheque wallet).
		row_paid_amount = 1 000 USD (entered in paid_to currency).
		stored_exchange_rate = 3.159059 (USD → ILS).

		Expected:
		  paid_amount          = 3 159.059 ILS
		  received_amount      = 1 000 USD
		  source_exchange_rate = 1
		  target_exchange_rate = 3.159059
		  paid_from_account_currency = ILS
		  paid_to_account_currency   = USD
		"""
		result = _compute_payment_entry_amounts(
			row_paid_amount=1000.0,
			paid_from_currency="ILS",
			paid_to_currency="USD",
			company_currency="ILS",
			stored_exchange_rate=3.159059,
			payment_type="Receive",
		)
		self.assertAlmostEqual(result["paid_amount"], 3159.059, places=3,
			msg="paid_amount (ILS) must equal 1000 × 3.159059")
		self.assertAlmostEqual(result["received_amount"], 1000.0, places=3,
			msg="received_amount (USD) must be the original cheque amount")
		self.assertAlmostEqual(result["source_exchange_rate"], 1.0, places=6,
			msg="source_exchange_rate must be 1 when paid_from = company currency")
		self.assertAlmostEqual(result["target_exchange_rate"], 3.159059, places=6,
			msg="target_exchange_rate must equal stored_exchange_rate (USD → ILS)")
		self.assertEqual(result["paid_from_account_currency"], "ILS")
		self.assertEqual(result["paid_to_account_currency"], "USD")

	def test_receive_cross_currency_no_inverted_paid_amount(self):
		"""
		Regression: the bug showed 'Paid Amount (USD) = 3 159.059'.
		This is only possible if paid_from_account_currency is wrongly set to USD.
		Verify it is always ILS when paid_from = ILS.
		"""
		result = _compute_payment_entry_amounts(1000.0, "ILS", "USD", "ILS", 3.159059, "Receive")
		self.assertEqual(result["paid_from_account_currency"], "ILS",
			msg="paid_from_account_currency must NOT be USD (regression guard)")
		# The erroneous behaviour was: paid_amount = 3159.059 displayed as USD.
		# With correct currencies this is now 3159.059 ILS, not USD.
		self.assertGreater(result["paid_amount"], result["received_amount"],
			msg="ILS amount must be larger than the USD amount for this exchange rate")

	# ------------------------------------------------------------------
	# Pay: mirror of the Receive scenario
	# ------------------------------------------------------------------

	def test_pay_cross_currency_usd_from_ils_to(self):
		"""
		Pay: paid_from = USD (cheque/bank), paid_to = ILS (supplier account).
		row_paid_amount = 1 000 USD (entered in paid_from currency).
		stored_exchange_rate = 3.159059 (USD → ILS).

		Expected:
		  paid_amount          = 1 000 USD
		  received_amount      = 3 159.059 ILS
		  source_exchange_rate = 3.159059
		  target_exchange_rate = 1
		"""
		result = _compute_payment_entry_amounts(
			row_paid_amount=1000.0,
			paid_from_currency="USD",
			paid_to_currency="ILS",
			company_currency="ILS",
			stored_exchange_rate=3.159059,
			payment_type="Pay",
		)
		self.assertAlmostEqual(result["paid_amount"], 1000.0, places=3)
		self.assertAlmostEqual(result["received_amount"], 3159.059, places=3)
		self.assertAlmostEqual(result["source_exchange_rate"], 3.159059, places=6)
		self.assertAlmostEqual(result["target_exchange_rate"], 1.0, places=6)
		self.assertEqual(result["paid_from_account_currency"], "USD")
		self.assertEqual(result["paid_to_account_currency"], "ILS")

	# ------------------------------------------------------------------
	# Same-currency (no conversion)
	# ------------------------------------------------------------------

	def test_same_currency_ils_both(self):
		"""When both accounts are ILS, all amounts equal and rates = 1."""
		result = _compute_payment_entry_amounts(
			row_paid_amount=5000.0,
			paid_from_currency="ILS",
			paid_to_currency="ILS",
			company_currency="ILS",
			stored_exchange_rate=1.0,
			payment_type="Receive",
		)
		self.assertAlmostEqual(result["paid_amount"], 5000.0, places=3)
		self.assertAlmostEqual(result["received_amount"], 5000.0, places=3)
		self.assertEqual(result["source_exchange_rate"], 1.0)
		self.assertEqual(result["target_exchange_rate"], 1.0)

	def test_same_currency_rate_one_even_if_stored_rate_wrong(self):
		"""Same currency: exchange rates must be 1 regardless of stored_exchange_rate."""
		result = _compute_payment_entry_amounts(5000.0, "ILS", "ILS", "ILS", 3.5, "Pay")
		self.assertEqual(result["source_exchange_rate"], 1.0)
		self.assertEqual(result["target_exchange_rate"], 1.0)
		self.assertAlmostEqual(result["paid_amount"], 5000.0, places=3)

	# ------------------------------------------------------------------
	# ERPNext GL invariant: base_paid == base_received
	# ------------------------------------------------------------------

	def test_gl_balance_receive(self):
		"""base_paid_amount must equal base_received_amount (GL invariant)."""
		result = _compute_payment_entry_amounts(1000.0, "ILS", "USD", "ILS", 3.159059, "Receive")
		base_paid = result["paid_amount"] * result["source_exchange_rate"]
		base_received = result["received_amount"] * result["target_exchange_rate"]
		self.assertAlmostEqual(base_paid, base_received, places=2,
			msg=f"GL imbalance: base_paid={base_paid} ≠ base_received={base_received}")

	def test_gl_balance_pay(self):
		"""GL invariant for Pay type."""
		result = _compute_payment_entry_amounts(1000.0, "USD", "ILS", "ILS", 3.159059, "Pay")
		base_paid = result["paid_amount"] * result["source_exchange_rate"]
		base_received = result["received_amount"] * result["target_exchange_rate"]
		self.assertAlmostEqual(base_paid, base_received, places=2)

	# ------------------------------------------------------------------
	# source_exchange_rate = 1 only when paid_from = company currency
	# ------------------------------------------------------------------

	def test_source_rate_is_1_only_when_paid_from_equals_company(self):
		"""source_exchange_rate must NOT be 1 if paid_from is a foreign currency."""
		result = _compute_payment_entry_amounts(1000.0, "USD", "ILS", "ILS", 3.159059, "Pay")
		self.assertNotEqual(result["source_exchange_rate"], 1.0,
			msg="source_exchange_rate must not default to 1 for a foreign paid_from account")


# ---------------------------------------------------------------------------
# Tests for the DB-fetch path: create_payment_entry_from_cheque(docname, row_id)
# ---------------------------------------------------------------------------

class _Row:
	"""Minimal child-row stub."""
	def __init__(self, **kwargs):
		for k, v in kwargs.items():
			setattr(self, k, v)

	def get(self, key, default=None):
		return getattr(self, key, default)


class _Doc:
	"""Minimal parent-document stub."""
	def __init__(self, **kwargs):
		for k, v in kwargs.items():
			setattr(self, k, v)

	def get(self, key, default=None):
		return getattr(self, key, default)


class TestCreatePaymentEntryFromCheque(unittest.TestCase):
	"""Verify create_payment_entry_from_cheque builds the correct Payment Entry dict.

	We mock all Frappe DB/doc calls so no running instance is required.
	"""

	def _make_receive_row(self):
		return _Row(
			name="ROW-001",
			idx=1,
			account_paid_from="ILS-Receivable",
			account_paid_to="USD-Wallet",
			paid_amount=1000.0,
			amount_in_company_currency=3159.059,
			target_exchange_rate=3.159059,
			mode_of_payment="Cheque",
			party_type="Customer",
			party="CUST-001",
			cheque_type="Crossed",
			reference_no="CHQ-001",
			reference_date="2024-01-15",
			first_beneficiary="Company",
			person_name="Ahmed",
			issuer_name="Ahmed",
			picture_of_check=None,
			bank="National Bank",
			payment_entry=None,
		)

	def _make_parent_doc(self, row):
		return _Doc(
			name="MCE-001",
			company="Test Co",
			payment_type="Receive",
			posting_date="2024-01-15",
			mode_of_payment="Cheque",
			mode_of_payment_type="Cheque",
			cheque_bank="National Bank",
			bank_acc="Bank-ILS",
			cheque_table=[row],
			cheque_table_2=[],
		)

	def setUp(self):
		import sys
		self._frappe = sys.modules["frappe"]

		# Capture inserted PE dict
		self._inserted = {}
		self._submitted = False
		self._set_values = []

		class _FakePE:
			def __init__(inner_self, d):
				inner_self.__dict__.update(d)
				inner_self.name = "PE-TEST-001"
				inner_self.flags = type("F", (), {"ignore_permissions": False})()

			def insert(inner_self):
				self._inserted = inner_self.__dict__.copy()

			def submit(inner_self):
				self._submitted = True

		row = self._make_receive_row()
		doc = self._make_parent_doc(row)

		self._frappe.get_doc = lambda *args, **kwargs: (
			doc if (args and args[0] == "Multiple Cheque Entry") else _FakePE(kwargs or args[0] if args else {})
		)

		# Mock get_doc to return either the parent or a new PE
		orig_get_doc = self._frappe.get_doc
		def _get_doc(arg, *rest):
			if arg == "Multiple Cheque Entry":
				return doc
			# It's a PE dict
			return _FakePE(arg)
		self._frappe.get_doc = _get_doc

		# Mock db
		class _DB:
			def get_value(self, doctype, name, field):
				if doctype == "Company":
					return "ILS"
				if doctype == "Account":
					if name == "ILS-Receivable":
						return "ILS"
					if name == "USD-Wallet":
						return "USD"
				return None

			def set_value(self_, doctype, name, field, value):
				self._set_values.append((doctype, name, field, value))

		self._frappe.db = _DB()
		self._frappe.throw = lambda msg, exc=None: (_ for _ in ()).throw(Exception(msg))

	def test_receive_db_fetch_amounts(self):
		"""Receive: amounts fetched from DB row match v15 conventions."""
		result = create_payment_entry_from_cheque("MCE-001", "ROW-001")

		self.assertEqual(result, "PE-TEST-001")
		self.assertTrue(self._submitted, "Payment Entry must be submitted")

		pe = self._inserted
		self.assertAlmostEqual(pe.get("paid_amount"), 3159.059, places=6,
			msg="paid_amount must equal amount_in_company_currency (ILS)")
		self.assertAlmostEqual(pe.get("received_amount"), 1000.0, places=6,
			msg="received_amount must equal row.paid_amount (USD)")
		self.assertAlmostEqual(pe.get("source_exchange_rate"), 1.0, places=6)
		self.assertAlmostEqual(pe.get("target_exchange_rate"), 3.159059, places=6)
		self.assertEqual(pe.get("paid_from_account_currency"), "ILS")
		self.assertEqual(pe.get("paid_to_account_currency"), "USD")

	def test_receive_child_row_updated(self):
		"""Child row payment_entry must be updated via frappe.db.set_value."""
		create_payment_entry_from_cheque("MCE-001", "ROW-001")

		self.assertTrue(
			any(
				sv[0] == "Cheque Table Receive" and sv[1] == "ROW-001"
				and sv[2] == "payment_entry" and sv[3] == "PE-TEST-001"
				for sv in self._set_values
			),
			"frappe.db.set_value must be called to persist payment_entry on the child row",
		)

	def test_receive_ignore_permissions_set(self):
		"""pe.flags.ignore_permissions must be True before insert."""
		# Re-capture flag state at insert time
		flags_at_insert = {}

		orig_get_doc = self._frappe.get_doc
		class _FlagCapturePE:
			def __init__(inner_self, d):
				inner_self.__dict__.update(d)
				inner_self.name = "PE-TEST-001"
				inner_self.flags = type("F", (), {"ignore_permissions": False})()

			def insert(inner_self):
				flags_at_insert["ignore_permissions"] = inner_self.flags.ignore_permissions

			def submit(inner_self):
				pass

		def _get_doc2(arg, *rest):
			if arg == "Multiple Cheque Entry":
				return orig_get_doc("Multiple Cheque Entry")
			return _FlagCapturePE(arg)

		self._frappe.get_doc = _get_doc2
		create_payment_entry_from_cheque("MCE-001", "ROW-001")
		self.assertTrue(flags_at_insert.get("ignore_permissions"),
			"flags.ignore_permissions must be True when inserting the Payment Entry")


if __name__ == "__main__":
	unittest.main()



class TestExchangeRatePartyToMop(unittest.TestCase):
	"""Tests for the bidirectional exchange-rate fields and their effect on PE creation.

	Scenario: company currency = USD, party account = ILS, bank/MOP account = USD.
	  exchange_rate_mop_to_party = 3.159059  (USD -> ILS)
	  exchange_rate_party_to_mop = 0.316555  (ILS -> USD  = 1 / 3.159059)
	"""

	_RATE_MOP_TO_PARTY = 3.159059
	_RATE_PARTY_TO_MOP = round(1.0 / 3.159059, 9)

	# ------------------------------------------------------------------
	# _compute_payment_entry_amounts with company = paid_to currency (USD)
	# ------------------------------------------------------------------

	def test_receive_company_equals_paid_to(self):
		"""Receive: company = paid_to (USD). source = 1/stored_rate, target = 1."""
		result = _compute_payment_entry_amounts(
			row_paid_amount=1000.0,
			paid_from_currency="ILS",
			paid_to_currency="USD",
			company_currency="USD",
			stored_exchange_rate=self._RATE_MOP_TO_PARTY,
			payment_type="Receive",
		)
		self.assertAlmostEqual(result["source_exchange_rate"], self._RATE_PARTY_TO_MOP, places=6,
			msg="source_exchange_rate must be 1/stored_rate when paid_to = company currency")
		self.assertAlmostEqual(result["target_exchange_rate"], 1.0, places=6,
			msg="target_exchange_rate must be 1 when paid_to = company currency")
		self.assertEqual(result["paid_from_account_currency"], "ILS")
		self.assertEqual(result["paid_to_account_currency"], "USD")

	def test_receive_company_equals_paid_to_gl_balance(self):
		"""GL invariant: base_paid == base_received for company=USD scenario."""
		result = _compute_payment_entry_amounts(
			1000.0, "ILS", "USD", "USD", self._RATE_MOP_TO_PARTY, "Receive",
		)
		base_paid = result["paid_amount"] * result["source_exchange_rate"]
		base_received = result["received_amount"] * result["target_exchange_rate"]
		self.assertAlmostEqual(base_paid, base_received, places=2)

	# ------------------------------------------------------------------
	# create_payment_entry_from_cheque with exchange_rate_party_to_mop set
	# ------------------------------------------------------------------

	def _make_row_with_party_to_mop(self):
		"""Row with exchange_rate_party_to_mop set (company=USD scenario)."""
		return _Row(
			name="ROW-002",
			idx=1,
			account_paid_from="ILS-Receivable",
			account_paid_to="USD-Bank",
			paid_amount=1000.0,
			amount_in_company_currency=3159.059,
			target_exchange_rate=3.159059,
			exchange_rate_mop_to_party=3.159059,
			exchange_rate_party_to_mop=round(1.0 / 3.159059, 9),
			mode_of_payment="Cheque",
			party_type="Customer",
			party="CUST-002",
			cheque_type="Crossed",
			reference_no="CHQ-002",
			reference_date="2024-01-15",
			first_beneficiary="Company",
			person_name="Sara",
			issuer_name="Sara",
			picture_of_check=None,
			bank="Test Bank",
			payment_entry=None,
		)

	def setUp(self):
		import sys
		self._frappe = sys.modules["frappe"]
		self._inserted = {}
		self._submitted = False
		self._set_values = []

		row = self._make_row_with_party_to_mop()
		doc = _Doc(
			name="MCE-002",
			company="Test Co USD",
			payment_type="Receive",
			posting_date="2024-01-15",
			mode_of_payment="Cheque",
			mode_of_payment_type="Cheque",
			cheque_bank="Test Bank",
			bank_acc="Bank-USD",
			cheque_table=[row],
			cheque_table_2=[],
		)

		class _FakePE:
			def __init__(inner_self, d):
				inner_self.__dict__.update(d)
				inner_self.name = "PE-TEST-002"
				inner_self.flags = type("F", (), {"ignore_permissions": False})()

			def insert(inner_self):
				self._inserted = inner_self.__dict__.copy()

			def submit(inner_self):
				self._submitted = True

		def _get_doc(arg, *rest):
			if arg == "Multiple Cheque Entry":
				return doc
			return _FakePE(arg)

		self._frappe.get_doc = _get_doc

		class _DB:
			def get_value(self, doctype, name, field):
				if doctype == "Company":
					return "USD"
				if doctype == "Account":
					if name == "ILS-Receivable":
						return "ILS"
					if name == "USD-Bank":
						return "USD"
				return None

			def set_value(self_, doctype, name, field, value):
				self._set_values.append((doctype, name, field, value))

		self._frappe.db = _DB()
		self._frappe.throw = lambda msg, exc=None: (_ for _ in ()).throw(Exception(msg))

	def test_party_to_mop_used_as_source_exchange_rate(self):
		"""exchange_rate_party_to_mop must become source_exchange_rate in the PE."""
		create_payment_entry_from_cheque("MCE-002", "ROW-002")
		pe = self._inserted
		expected_source = round(1.0 / 3.159059, 9)
		self.assertAlmostEqual(pe.get("source_exchange_rate"), expected_source, places=6,
			msg="source_exchange_rate must equal exchange_rate_party_to_mop")

	def test_gl_balance_with_party_to_mop(self):
		"""base_paid == base_received when exchange_rate_party_to_mop is used."""
		create_payment_entry_from_cheque("MCE-002", "ROW-002")
		pe = self._inserted
		base_paid = pe.get("paid_amount") * pe.get("source_exchange_rate")
		base_received = pe.get("received_amount") * pe.get("target_exchange_rate")
		self.assertAlmostEqual(base_paid, base_received, places=2,
			msg="GL imbalance: base_paid={0} != base_received={1}".format(base_paid, base_received))

	def test_received_amount_equals_cheque_amount(self):
		"""received_amount must equal the original cheque amount (1000 USD)."""
		create_payment_entry_from_cheque("MCE-002", "ROW-002")
		pe = self._inserted
		self.assertAlmostEqual(pe.get("received_amount"), 1000.0, places=6,
			msg="received_amount must be the original cheque amount")

	def test_paid_amount_equals_amount_in_company_currency(self):
		"""paid_amount (ILS) must equal amount_in_company_currency (3159.059)."""
		create_payment_entry_from_cheque("MCE-002", "ROW-002")
		pe = self._inserted
		self.assertAlmostEqual(pe.get("paid_amount"), 3159.059, places=6,
			msg="paid_amount must equal amount_in_company_currency")


# ---------------------------------------------------------------------------
# Bug A: USD → USD same-company-currency scenario
# ---------------------------------------------------------------------------

class TestUSDtoUSDSameCompanyCurrency(unittest.TestCase):
	"""Receive: paid_from = USD (company), paid_to = USD (company).
	Both accounts in company currency – no exchange-rate conversion needed.
	exchange_rate_party_to_mop may contain a stale wrong value; it must be
	cleared and must NOT cause a mismatch error.
	"""

	_STALE_RATE = 0.31655   # wrong value that would cause the mismatch error

	def _make_row(self):
		return _Row(
			name="ROW-USD-USD",
			idx=1,
			account_paid_from="USD-Receivable",
			account_paid_to="USD-Wallet",
			paid_amount=1000.0,
			amount_in_company_currency=1000.0,
			target_exchange_rate=1.0,
			exchange_rate_mop_to_party=3.159059,   # stale value from a previous ILS→USD rate lookup
			exchange_rate_party_to_mop=self._STALE_RATE,   # stale 1/3.159059 value from JS sync
			mode_of_payment="Cheque",
			party_type="Customer",
			party="CUST-USD",
			cheque_type="Crossed",
			reference_no="CHQ-USD",
			reference_date="2024-01-15",
			first_beneficiary="Company",
			person_name="",
			issuer_name="",
			picture_of_check=None,
			bank="National Bank",
			payment_entry=None,
		)

	def setUp(self):
		import sys
		self._frappe = sys.modules["frappe"]
		self._inserted = {}
		self._submitted = False
		self._set_values = []

		row = self._make_row()
		doc = _Doc(
			name="MCE-USD",
			company="Test Co USD",
			payment_type="Receive",
			posting_date="2024-01-15",
			mode_of_payment="Cheque",
			mode_of_payment_type="Cheque",
			cheque_bank="National Bank",
			bank_acc="Bank-USD",
			cheque_table=[row],
			cheque_table_2=[],
		)

		class _FakePE:
			def __init__(inner_self, d):
				inner_self.__dict__.update(d)
				inner_self.name = "PE-USD-USD"
				inner_self.flags = type("F", (), {"ignore_permissions": False})()

			def insert(inner_self):
				self._inserted = inner_self.__dict__.copy()

			def submit(inner_self):
				self._submitted = True

		def _get_doc(arg, *rest):
			if arg == "Multiple Cheque Entry":
				return doc
			return _FakePE(arg)

		self._frappe.get_doc = _get_doc

		class _DB:
			def get_value(self, doctype, name, field):
				if doctype == "Company":
					return "USD"
				if doctype == "Account":
					return "USD"   # both accounts are USD
				return None

			def set_value(self_, doctype, name, field, value):
				self._set_values.append((doctype, name, field, value))

		self._frappe.db = _DB()
		self._frappe.throw = lambda msg, exc=None: (_ for _ in ()).throw(Exception(msg))

	def test_usd_usd_source_and_target_rates_are_1(self):
		"""USD→USD: source and target exchange rates must both be 1."""
		create_payment_entry_from_cheque("MCE-USD", "ROW-USD-USD")
		pe = self._inserted
		self.assertAlmostEqual(pe.get("source_exchange_rate"), 1.0, places=6)
		self.assertAlmostEqual(pe.get("target_exchange_rate"), 1.0, places=6)

	def test_usd_usd_paid_and_received_amounts_equal(self):
		"""USD→USD: paid_amount and received_amount must equal the cheque amount."""
		create_payment_entry_from_cheque("MCE-USD", "ROW-USD-USD")
		pe = self._inserted
		self.assertAlmostEqual(pe.get("paid_amount"), 1000.0, places=6)
		self.assertAlmostEqual(pe.get("received_amount"), 1000.0, places=6)

	def test_usd_usd_stale_party_to_mop_cleared(self):
		"""USD→USD: stale exchange_rate_party_to_mop must be cleared to 0."""
		create_payment_entry_from_cheque("MCE-USD", "ROW-USD-USD")
		cleared = [sv for sv in self._set_values
				   if sv[0] == "Cheque Table Receive"
				   and sv[2] == "exchange_rate_party_to_mop"
				   and sv[3] == 0]
		self.assertTrue(cleared, "exchange_rate_party_to_mop must be set to 0 for USD→USD")

	def test_usd_usd_stale_mop_to_party_cleared(self):
		"""USD→USD: stale exchange_rate_mop_to_party must be cleared to 0."""
		create_payment_entry_from_cheque("MCE-USD", "ROW-USD-USD")
		cleared = [sv for sv in self._set_values
				   if sv[0] == "Cheque Table Receive"
				   and sv[2] == "exchange_rate_mop_to_party"
				   and sv[3] == 0]
		self.assertTrue(cleared, "exchange_rate_mop_to_party must be set to 0 for USD→USD")

	def test_usd_usd_gl_balance(self):
		"""USD→USD: GL invariant base_paid == base_received."""
		create_payment_entry_from_cheque("MCE-USD", "ROW-USD-USD")
		pe = self._inserted
		base_paid = pe.get("paid_amount") * pe.get("source_exchange_rate")
		base_received = pe.get("received_amount") * pe.get("target_exchange_rate")
		self.assertAlmostEqual(base_paid, base_received, places=2)


# ---------------------------------------------------------------------------
# Bug B: JOD → USD (foreign paid_to, company paid_from) scenario
# ---------------------------------------------------------------------------

class TestJODtoUSDReceive(unittest.TestCase):
	"""Receive: paid_from = USD (company), paid_to = JOD (foreign MOP account).
	Cheque amount: 1000 JOD; amount_in_company_currency: 1410.437 USD.
	target_exchange_rate must be 1410.437/1000 = 1.410437, NOT stored_rate.
	"""

	_JOD_RATE = 1.410437       # 1 JOD = 1.410437 USD
	_CHEQUE_AMOUNT_JOD = 1000.0
	_BASE_USD = 1410.437

	def _make_row(self):
		return _Row(
			name="ROW-JOD-USD",
			idx=1,
			account_paid_from="USD-Receivable",
			account_paid_to="JOD-Wallet",
			paid_amount=self._CHEQUE_AMOUNT_JOD,
			amount_in_company_currency=self._BASE_USD,
			target_exchange_rate=self._JOD_RATE,
			mode_of_payment="Cheque",
			party_type="Customer",
			party="CUST-JOD",
			cheque_type="Crossed",
			reference_no="CHQ-JOD",
			reference_date="2024-01-15",
			first_beneficiary="Company",
			person_name="",
			issuer_name="",
			picture_of_check=None,
			bank="Jordan Bank",
			payment_entry=None,
		)

	def setUp(self):
		import sys
		self._frappe = sys.modules["frappe"]
		self._inserted = {}
		self._submitted = False
		self._set_values = []

		row = self._make_row()
		doc = _Doc(
			name="MCE-JOD",
			company="Test Co USD",
			payment_type="Receive",
			posting_date="2024-01-15",
			mode_of_payment="Cheque",
			mode_of_payment_type="Cheque",
			cheque_bank="Jordan Bank",
			bank_acc="Bank-USD",
			cheque_table=[row],
			cheque_table_2=[],
		)

		class _FakePE:
			def __init__(inner_self, d):
				inner_self.__dict__.update(d)
				inner_self.name = "PE-JOD-USD"
				inner_self.flags = type("F", (), {"ignore_permissions": False})()

			def insert(inner_self):
				self._inserted = inner_self.__dict__.copy()

			def submit(inner_self):
				self._submitted = True

		def _get_doc(arg, *rest):
			if arg == "Multiple Cheque Entry":
				return doc
			return _FakePE(arg)

		self._frappe.get_doc = _get_doc

		class _DB:
			def get_value(self, doctype, name, field):
				if doctype == "Company":
					return "USD"
				if doctype == "Account":
					if name == "USD-Receivable":
						return "USD"
					if name == "JOD-Wallet":
						return "JOD"
				return None

			def set_value(self_, doctype, name, field, value):
				self._set_values.append((doctype, name, field, value))

		self._frappe.db = _DB()
		self._frappe.throw = lambda msg, exc=None: (_ for _ in ()).throw(Exception(msg))

	def test_jod_usd_source_rate_is_1(self):
		"""JOD→USD Receive: source_exchange_rate must be 1 (paid_from = company currency)."""
		create_payment_entry_from_cheque("MCE-JOD", "ROW-JOD-USD")
		pe = self._inserted
		self.assertAlmostEqual(pe.get("source_exchange_rate"), 1.0, places=6)

	def test_jod_usd_target_rate_derived_from_amounts(self):
		"""JOD→USD Receive: target_exchange_rate = paid_amount / received_amount."""
		create_payment_entry_from_cheque("MCE-JOD", "ROW-JOD-USD")
		pe = self._inserted
		expected = self._BASE_USD / self._CHEQUE_AMOUNT_JOD   # 1.410437
		self.assertAlmostEqual(pe.get("target_exchange_rate"), expected, places=4)

	def test_jod_usd_received_amount_is_cheque_amount(self):
		"""JOD→USD Receive: received_amount must equal the JOD cheque amount."""
		create_payment_entry_from_cheque("MCE-JOD", "ROW-JOD-USD")
		pe = self._inserted
		self.assertAlmostEqual(pe.get("received_amount"), self._CHEQUE_AMOUNT_JOD, places=3)

	def test_jod_usd_paid_amount_is_base_usd(self):
		"""JOD→USD Receive: paid_amount must equal amount_in_company_currency (USD)."""
		create_payment_entry_from_cheque("MCE-JOD", "ROW-JOD-USD")
		pe = self._inserted
		self.assertAlmostEqual(pe.get("paid_amount"), self._BASE_USD, places=3)

	def test_jod_usd_gl_balance(self):
		"""JOD→USD Receive: base_paid == base_received (no Exchange Gain/Loss)."""
		create_payment_entry_from_cheque("MCE-JOD", "ROW-JOD-USD")
		pe = self._inserted
		base_paid = pe.get("paid_amount") * pe.get("source_exchange_rate")
		base_received = pe.get("received_amount") * pe.get("target_exchange_rate")
		self.assertAlmostEqual(base_paid, base_received, places=2,
			msg=f"GL imbalance: base_paid={base_paid} != base_received={base_received}")

	def test_jod_usd_compute_helper_gl_balance(self):
		"""_compute_payment_entry_amounts: JOD→USD Receive gives consistent amounts."""
		result = _compute_payment_entry_amounts(
			row_paid_amount=1000.0,
			paid_from_currency="USD",
			paid_to_currency="JOD",
			company_currency="USD",
			stored_exchange_rate=self._JOD_RATE,
			payment_type="Receive",
		)
		base_paid = result["paid_amount"] * result["source_exchange_rate"]
		base_received = result["received_amount"] * result["target_exchange_rate"]
		self.assertAlmostEqual(base_paid, base_received, places=2)


# ---------------------------------------------------------------------------
# _compute_payment_entry_amounts: USD→USD same-company-currency
# ---------------------------------------------------------------------------

class TestComputeAmountsUSDtoUSD(unittest.TestCase):
	"""_compute_payment_entry_amounts for USD→USD (company = USD)."""

	def test_usd_usd_company_currency_rates_are_1(self):
		"""USD→USD company-currency: source and target rates must both be 1."""
		result = _compute_payment_entry_amounts(
			row_paid_amount=1000.0,
			paid_from_currency="USD",
			paid_to_currency="USD",
			company_currency="USD",
			stored_exchange_rate=0.31655,   # stale/wrong value
			payment_type="Receive",
		)
		self.assertEqual(result["source_exchange_rate"], 1.0)
		self.assertEqual(result["target_exchange_rate"], 1.0)
		self.assertAlmostEqual(result["paid_amount"], 1000.0, places=3)
		self.assertAlmostEqual(result["received_amount"], 1000.0, places=3)
