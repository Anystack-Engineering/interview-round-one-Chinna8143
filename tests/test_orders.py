import json
import re
from collections import Counter
from jsonpath_ng import parse
import pytest

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

@pytest.fixture(scope="session")
def data():
    with open("data/orders.json") as f:
        return json.load(f)

@pytest.fixture(scope="session")
def orders(data):
    # returns list of order dicts
    return [m.value for m in parse("$.orders[*]").find(data)]

# ---------- A) Presence & Format Validation ----------

def test_order_id_presence(data):
    ids = [m.value for m in parse("$.orders[*].id").find(data)]
    assert all(ids), f"Found empty order id(s): {ids}"

def test_order_status_valid(data):
    valid = {"PAID", "PENDING", "CANCELLED"}
    statuses = [(m.context.value.get("id"), m.value) for m in parse("$.orders[*].status").find(data)]
    invalid = [iid for iid, s in statuses if s not in valid]
    assert invalid == [], f"Orders with invalid status: {invalid}"

def test_customer_email_format(data):
    bad = []
    for m in parse("$.orders[*]").find(data):
        order = m.value
        oid = order.get("id")
        email = order.get("customer", {}).get("email")
        if not email or not EMAIL_RE.match(email):
            bad.append(oid)
    assert bad == ["A-1002", "A-1003"], f"Expected invalid emails ['A-1002','A-1003'], got {bad}"

def test_lines_integrity(data):
    bad_orders = set()
    for m in parse("$.orders[*]").find(data):
        order = m.value
        oid = order.get("id")
        status = order.get("status")
        lines = order.get("lines", [])
        # For PAID/PENDING lines must be non-empty
        if status in {"PAID", "PENDING"} and not lines:
            bad_orders.add(oid)
        for ln in lines:
            if not ln.get("sku"):
                bad_orders.add(oid)
            if ln.get("qty", 0) <= 0:
                bad_orders.add(oid)
            if ln.get("price", 0) < 0:
                bad_orders.add(oid)
    # Only A-1003 has non-positive qty and negative price in this dataset
    assert bad_orders == {"A-1003"}, f"Lines integrity failed for {sorted(bad_orders)}"

def test_payment_refund_consistency(data):
    paid_not_captured = []
    cancelled_refund_mismatch = []
    for m in parse("$.orders[*]").find(data):
        o = m.value
        oid = o.get("id")
        status = o.get("status")
        if status == "PAID":
            if not o.get("payment", {}).get("captured"):
                paid_not_captured.append(oid)
        if status == "CANCELLED" and o.get("lines"):
            expected = sum(l["qty"] * l["price"] for l in o.get("lines", []))
            actual = o.get("refund", {}).get("amount")
            if actual != expected:
                cancelled_refund_mismatch.append(oid)
    assert paid_not_captured == [], f"PAID orders not captured: {paid_not_captured}"
    assert cancelled_refund_mismatch == [], f"Cancelled refund mismatch: {cancelled_refund_mismatch}"

def test_shipping_fee_non_negative(data):
    bad = []
    for m in parse("$.orders[*]").find(data):
        o = m.value
        fee = o.get("shipping", {}).get("fee", 0)
        if fee < 0:
            bad.append(o.get("id"))
    assert bad == [], f"Orders with negative shipping fee: {bad}"

# ---------- B) Extraction & Aggregation ----------

def test_list_of_order_ids(data):
    ids = [m.value for m in parse("$.orders[*].id").find(data)]
    assert ids == ["A-1001","A-1002","A-1003","A-1004","A-1005"]

def test_total_line_items(data):
    total = sum(len(m.value.get("lines", [])) for m in parse("$.orders[*]").find(data))
    assert total == 7

def test_top2_skus_by_quantity(data):
    counter = Counter()
    for m in parse("$.orders[*].lines[*]").find(data):
        ln = m.value
        qty = ln.get("qty", 0)
        if qty > 0:
            counter[ln["sku"]] += qty
    top2 = counter.most_common(2)
    assert top2 == [("PEN-RED", 5), ("NOTE-POCKET", 2)]

def test_gmv_per_order(data):
    gmv = {}
    for m in parse("$.orders[*]").find(data):
        o = m.value
        oid = o["id"]
        gmv[oid] = sum(l["qty"] * l["price"] for l in o.get("lines", []))
    expected = {
        "A-1001": 70.0,
        "A-1002": 0.0,
        "A-1003": -15.0,
        "A-1004": 16.0,
        "A-1005": 55.0,
    }
    assert gmv == expected

def test_orders_with_invalid_emails(data):
    invalid = []
    for m in parse("$.orders[*]").find(data):
        o = m.value
        email = o.get("customer", {}).get("email")
        if not email or not EMAIL_RE.match(email):
            invalid.append(o.get("id"))
    assert invalid == ["A-1002","A-1003"]

def test_paid_orders_payment_captured(data):
    failed = [o.get("id") for o in (m.value for m in parse("$.orders[*]").find(data)) if o.get("status") == "PAID" and not o.get("payment", {}).get("captured")]
    assert failed == []

def test_cancelled_orders_refund_correct(data):
    correct = []
    for m in parse("$.orders[*]").find(data):
        o = m.value
        if o.get("status") == "CANCELLED" and o.get("lines"):
            expected = sum(l["qty"] * l["price"] for l in o.get("lines", []))
            if o.get("refund", {}).get("amount") == expected:
                correct.append(o.get("id"))
    assert correct == ["A-1004"]

# ---------- C) Summary report ----------

def test_summary_report(data):
    orders = [m.value for m in parse("$.orders[*]").find(data)]
    summary = {
        "total_orders": len(orders),
        "total_line_items": sum(len(o.get("lines", [])) for o in orders),
        "invalid_orders": []
    }

    for o in orders:
        issues = []
        if not o.get("id"):
            issues.append("missing id")
        if o.get("status") not in {"PAID", "PENDING", "CANCELLED"}:
            issues.append("invalid status")
        email = o.get("customer", {}).get("email")
        if not email or not EMAIL_RE.match(email):
            issues.append("invalid email")
        for l in o.get("lines", []):
            if l.get("qty", 0) <= 0:
                issues.append("non-positive qty")
            if l.get("price", 0) < 0:
                issues.append("negative price")
        if issues:
            summary["invalid_orders"].append({"id": o.get("id"), "issues": issues})

    # print for human review in CI logs
    print("SUMMARY:", summary)
    assert summary["total_orders"] == 5
    assert summary["total_line_items"] == 7
    assert len(summary["invalid_orders"]) >= 1