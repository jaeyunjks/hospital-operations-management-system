#!/usr/bin/env python3
"""HTTP-only backend for Student 3 Pharmacy Operations."""
from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta
import urllib.error
import urllib.request

from flask import Flask, jsonify, request

from services.ai_client import health_check
from services.expiry_advisory import advisory as expiry_advisory

DATABASE_SERVICE_URL = os.environ.get(
    "DATABASE_URL", os.environ.get("DATABASE_SERVICE_URL", "http://localhost:6300")
).rstrip("/")
PORT = int(os.environ.get("PORT", os.environ.get("BACKEND_PORT", "5300")))
MANAGER_ROLE = "Pharmacy Manager"
OPEN_STATUSES = {"pending_approval", "approved", "ordered"}
DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 100
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
app = Flask(__name__)


class DatabaseServiceError(RuntimeError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def fail(message, status):
    return jsonify({"error": message}), status


def pagination_from_request():
    """Validate common list pagination after the endpoint's filters apply."""
    try:
        page = int(request.args.get("page", 1))
        page_size = int(request.args.get("page_size", DEFAULT_PAGE_SIZE))
    except (TypeError, ValueError):
        raise ValueError("page and page_size must be positive integers") from None
    if page < 1 or page_size < 1 or page_size > MAX_PAGE_SIZE:
        raise ValueError(f"page must be positive and page_size must be between 1 and {MAX_PAGE_SIZE}")
    return page, page_size


def paginate(rows, page, page_size):
    """Return the requested page and metadata for already-filtered rows."""
    total_items = len(rows)
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    page = min(page, total_pages)
    start = (page - 1) * page_size
    return rows[start:start + page_size], {
        "page": page, "page_size": page_size, "total_items": total_items,
        "total_pages": total_pages, "has_previous": page > 1,
        "has_next": page < total_pages,
    }


def database_request(path, method="GET", payload=None):
    """Call only the database service; this process never opens SQLite."""
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    call = urllib.request.Request(f"{DATABASE_SERVICE_URL}{path}", data=data,
                                  headers=headers, method=method)
    try:
        with urllib.request.urlopen(call, timeout=5) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            message = json.load(exc).get("error", "Database service request failed")
        except (json.JSONDecodeError, AttributeError):
            message = "Database service request failed"
        raise DatabaseServiceError(exc.code, message) from exc
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise DatabaseServiceError(503, "Database service unavailable") from exc


def require_manager():
    return None if request.headers.get("X-HOMS-Role") == MANAGER_ROLE else fail("Pharmacy Manager role required", 403)


def validate_supplier(payload):
    if not isinstance(payload, dict):
        raise ValueError("JSON body required")
    name = str(payload.get("name", "")).strip()
    if not name:
        raise ValueError("name is required")
    try:
        lead_time_days = int(payload.get("lead_time_days"))
    except (TypeError, ValueError):
        raise ValueError("lead_time_days must be an integer >= 0") from None
    if lead_time_days < 0 or str(payload.get("lead_time_days")).strip() != str(lead_time_days):
        raise ValueError("lead_time_days must be an integer >= 0")
    email = (payload.get("contact_email") or "").strip() or None
    if email and not EMAIL.match(email):
        raise ValueError("contact_email must be a valid email address")
    return {"name": name, "contact_email": email,
            "phone": (payload.get("phone") or "").strip() or None,
            "lead_time_days": lead_time_days}


def enriched(supplier, medicines, orders):
    supplier_id = supplier["supplier_id"]
    relevant_medicines = [m for m in medicines if m.get("supplier_id") == supplier_id]
    relevant_orders = [o for o in orders if o.get("supplier_id") == supplier_id]
    return {**supplier, "medicines_supplied": len(relevant_medicines),
            "open_orders": sum(o.get("status") in OPEN_STATUSES for o in relevant_orders),
            "total_ordered_value": round(sum((o.get("quantity_ordered") or 0) * (o.get("unit_price") or 0) for o in relevant_orders), 2)}


def suppliers_for(status, search):
    if status not in {"active", "discontinued", "all"}:
        raise ValueError("status must be active, discontinued, or all")
    suppliers, medicines, orders = (database_request("/suppliers"), database_request("/medicines"), database_request("/purchase_orders"))
    search = search.strip().casefold()
    return [enriched(s, medicines, orders) for s in suppliers
            if (status == "all" or s["status"] == status) and (not search or search in s["name"].casefold())]


def name_exists(name, excluded_id=None):
    return any(s["name"].casefold() == name.casefold() and s["supplier_id"] != excluded_id
               for s in database_request("/suppliers"))


def medicine_batches(medicine_id):
    """Use the existing per-medicine batches endpoint; no database SQL here."""
    return database_request(f"/medicines/{medicine_id}/batches")


def parse_date(value):
    return date.fromisoformat(value)


def enriched_medicine(medicine, suppliers, batches):
    today = date.today()
    valid = [batch for batch in batches if parse_date(batch["expiry_date"]) >= today]
    supplier = next((s for s in suppliers if s["supplier_id"] == medicine.get("supplier_id")), None)
    available = sum(batch.get("quantity_remaining", 0) for batch in valid)
    return {**medicine, "available_quantity": available,
            "is_low_stock": available <= medicine["reorder_level"],
            "earliest_expiry": min((batch["expiry_date"] for batch in valid), default=None),
            "batch_count": len(valid), "supplier_name": supplier["name"] if supplier else None}


def medicines_for(search, category, status, stock_status, expiring_within):
    if status not in {"active", "discontinued", "all"}:
        raise ValueError("status must be active, discontinued, or all")
    if stock_status not in {"all", "low", "out"}:
        raise ValueError("stock_status must be all, low, or out")
    try:
        days = int(expiring_within) if expiring_within else None
        if days is not None and days < 0: raise ValueError
    except ValueError:
        raise ValueError("expiring_within must be a non-negative number of days") from None
    medicines, suppliers = database_request("/medicines"), database_request("/suppliers")
    rows = [enriched_medicine(m, suppliers, medicine_batches(m["medicine_id"])) for m in medicines]
    term = search.strip().casefold()
    deadline = date.today() + timedelta(days=days or 0) if days is not None else None
    return [m for m in rows if (status == "all" or m["status"] == status)
            and (not category or category == "all" or m["category"] == category)
            and (not term or term in m["name"].casefold())
            and (stock_status == "all" or (stock_status == "low" and m["is_low_stock"] and m["available_quantity"] > 0) or (stock_status == "out" and m["available_quantity"] == 0))
            and (deadline is None or (m["earliest_expiry"] and parse_date(m["earliest_expiry"]) <= deadline))]


def validate_medicine(payload):
    if not isinstance(payload, dict): raise ValueError("JSON body required")
    required = {key: str(payload.get(key, "")).strip() for key in ("name", "category", "unit")}
    if not all(required.values()): raise ValueError("name, category and unit are required")
    try:
        price = float(payload.get("unit_price"))
        reorder = int(payload.get("reorder_level"))
        stock = int(payload.get("stock_quantity", 0))
    except (TypeError, ValueError):
        raise ValueError("unit_price must be a number and stock values must be integers") from None
    if price < 0 or reorder < 0 or stock < 0: raise ValueError("unit_price and stock values must be >= 0")
    try: supplier_id = int(payload.get("supplier_id"))
    except (TypeError, ValueError): raise ValueError("supplier_id is required") from None
    supplier = database_request(f"/suppliers/{supplier_id}")
    if supplier["status"] != "active": raise ValueError("supplier_id must reference an active supplier")
    return {**required, "unit_price": price, "reorder_level": reorder, "stock_quantity": stock,
            "supplier_id": supplier_id, "storage_instructions": (payload.get("storage_instructions") or "").strip() or None}


@app.get("/health")
def health():
    try:
        database_request("/health")
        return jsonify({"status": "ok", "service": "student-3-backend"})
    except DatabaseServiceError:
        return jsonify({"status": "degraded", "database_service": "unavailable"}), 503


@app.get("/api/ai/health")
def ai_health():
    """Expose a non-crashing operational check for the shared Ollama runtime."""
    return jsonify(health_check())


@app.post("/api/ai/expiry-advisory")
def ai_expiry_advisory():
    """Return expiry advice only; this endpoint never changes inventory data."""
    payload = request.get_json(silent=True) or {}
    try:
        days_ahead = int(payload.get("days_ahead", 30))
        if not 0 <= days_ahead <= 365:
            raise ValueError
    except (TypeError, ValueError):
        return fail("days_ahead must be an integer between 0 and 365", 400)
    try:
        # Read-only orchestration: the advisory does not write batches, medicines, or movements.
        result, _input, _model_result = expiry_advisory(database_request, days_ahead)
        return jsonify(result)
    except DatabaseServiceError as exc:
        return fail(str(exc), exc.status)


# Existing demo-picker proxies, retained while the supplier slice is added.
@app.get("/api/staff")
def staff_list():
    try: return jsonify({"staff": database_request("/staff")})
    except DatabaseServiceError as exc: return fail(str(exc), exc.status)


@app.get("/api/staff/<int:staff_id>")
def staff_detail(staff_id):
    try: return jsonify({"staff": database_request(f"/staff/{staff_id}")})
    except DatabaseServiceError as exc: return fail(str(exc), exc.status)


@app.get("/api/suppliers")
def supplier_list():
    try:
        rows = suppliers_for(request.args.get("status", "active"), request.args.get("search", ""))
        rows, pagination = paginate(rows, *pagination_from_request())
        return jsonify({"suppliers": rows, "pagination": pagination})
    except ValueError as exc: return fail(str(exc), 400)
    except DatabaseServiceError as exc: return fail(str(exc), exc.status)


@app.get("/api/suppliers/<int:supplier_id>")
def supplier_detail(supplier_id):
    try:
        supplier = database_request(f"/suppliers/{supplier_id}")
        medicines = [m for m in database_request("/medicines") if m.get("supplier_id") == supplier_id]
        for medicine in medicines:
            medicine["at_or_below_reorder"] = medicine.get("stock_quantity", 0) <= medicine.get("reorder_level", 0)
        orders = [o for o in database_request("/purchase_orders") if o.get("supplier_id") == supplier_id]
        return jsonify({"supplier": enriched(supplier, medicines, orders), "medicines": medicines, "purchase_orders": orders})
    except DatabaseServiceError as exc: return fail(str(exc), exc.status)


@app.post("/api/suppliers")
def supplier_create():
    if denied := require_manager(): return denied
    try:
        payload = validate_supplier(request.get_json(silent=True))
        if name_exists(payload["name"]): return fail("A supplier with that name already exists", 409)
        return jsonify(database_request("/suppliers", "POST", payload)), 201
    except ValueError as exc: return fail(str(exc), 400)
    except DatabaseServiceError as exc: return fail(str(exc), exc.status)


@app.put("/api/suppliers/<int:supplier_id>")
def supplier_update(supplier_id):
    if denied := require_manager(): return denied
    try:
        database_request(f"/suppliers/{supplier_id}")
        payload = validate_supplier(request.get_json(silent=True))
        if name_exists(payload["name"], supplier_id): return fail("A supplier with that name already exists", 409)
        return jsonify(database_request(f"/suppliers/{supplier_id}", "PUT", payload))
    except ValueError as exc: return fail(str(exc), 400)
    except DatabaseServiceError as exc: return fail(str(exc), exc.status)


@app.delete("/api/suppliers/<int:supplier_id>")
def supplier_deactivate(supplier_id):
    if denied := require_manager(): return denied
    try: return jsonify(database_request(f"/suppliers/{supplier_id}", "DELETE"))
    except DatabaseServiceError as exc: return fail(str(exc), exc.status)


@app.post("/api/suppliers/<int:supplier_id>/reactivate")
def supplier_reactivate(supplier_id):
    if denied := require_manager(): return denied
    try: return jsonify(database_request(f"/suppliers/{supplier_id}", "PUT", {"status": "active"}))
    except DatabaseServiceError as exc: return fail(str(exc), exc.status)


@app.get("/api/medicines")
def medicine_list():
    try:
        categories = sorted({row["category"] for row in database_request("/medicines")})
        rows = medicines_for(request.args.get("search", ""), request.args.get("category", "all"), request.args.get("status", "active"), request.args.get("stock_status", "all"), request.args.get("expiring_within"))
        rows, pagination = paginate(rows, *pagination_from_request())
        return jsonify({"medicines": rows, "categories": categories, "pagination": pagination})
    except ValueError as exc: return fail(str(exc), 400)
    except DatabaseServiceError as exc: return fail(str(exc), exc.status)


@app.get("/api/medicines/<int:medicine_id>")
def medicine_detail(medicine_id):
    try:
        medicine = database_request(f"/medicines/{medicine_id}")
        suppliers, batches, movements, orders = (database_request("/suppliers"), medicine_batches(medicine_id), database_request("/stock_movements"), database_request("/purchase_orders"))
        today = date.today()
        for batch in batches:
            expiry = parse_date(batch["expiry_date"])
            batch["expiry_status"] = "expired" if expiry < today else "expiring_soon" if expiry <= today + timedelta(days=30) else "valid"
        batches.sort(key=lambda batch: batch["expiry_date"])
        supplier = next((s for s in suppliers if s["supplier_id"] == medicine.get("supplier_id")), None)
        return jsonify({"medicine": enriched_medicine(medicine, suppliers, batches), "batches": batches, "stock_movements": sorted([m for m in movements if m.get("medicine_id") == medicine_id], key=lambda m: m["created_at"], reverse=True)[:10], "supplier": supplier, "purchase_orders": [o for o in orders if o.get("medicine_id") == medicine_id]})
    except DatabaseServiceError as exc: return fail(str(exc), exc.status)


def medicine_name_exists(name, excluded_id=None):
    return any(m["name"].casefold() == name.casefold() and m["medicine_id"] != excluded_id for m in database_request("/medicines"))


@app.post("/api/medicines")
def medicine_create():
    if denied := require_manager(): return denied
    try:
        payload = validate_medicine(request.get_json(silent=True))
        if medicine_name_exists(payload["name"]): return fail("A medicine with that name already exists", 409)
        return jsonify(database_request("/medicines", "POST", payload)), 201
    except ValueError as exc: return fail(str(exc), 400)
    except DatabaseServiceError as exc: return fail(str(exc), exc.status)


@app.put("/api/medicines/<int:medicine_id>")
def medicine_update(medicine_id):
    if denied := require_manager(): return denied
    try:
        database_request(f"/medicines/{medicine_id}")
        payload = validate_medicine(request.get_json(silent=True))
        if medicine_name_exists(payload["name"], medicine_id): return fail("A medicine with that name already exists", 409)
        return jsonify(database_request(f"/medicines/{medicine_id}", "PUT", payload))
    except ValueError as exc: return fail(str(exc), 400)
    except DatabaseServiceError as exc: return fail(str(exc), exc.status)


@app.delete("/api/medicines/<int:medicine_id>")
def medicine_deactivate(medicine_id):
    if denied := require_manager(): return denied
    try: return jsonify(database_request(f"/medicines/{medicine_id}", "DELETE"))
    except DatabaseServiceError as exc: return fail(str(exc), exc.status)


@app.post("/api/medicines/<int:medicine_id>/reactivate")
def medicine_reactivate(medicine_id):
    if denied := require_manager(): return denied
    try: return jsonify(database_request(f"/medicines/{medicine_id}", "PUT", {"status": "active"}))
    except DatabaseServiceError as exc: return fail(str(exc), exc.status)


@app.get("/api/stock/movements")
def stock_movements():
    """Return the immutable stock ledger enriched from HTTP service data."""
    from_value, to_value = request.args.get("from", ""), request.args.get("to", "")
    try:
        from_date = parse_date(from_value) if from_value else None
        to_date = parse_date(to_value) if to_value else None
    except ValueError:
        return fail("from and to must use YYYY-MM-DD format", 400)
    try:
        limit = int(request.args.get("limit", 100))
        if limit <= 0: raise ValueError
    except ValueError:
        return fail("limit must be a positive integer", 400)
    movement_type = request.args.get("movement_type", "all")
    if movement_type not in {"all", "receive", "issue", "adjust", "waste"}:
        return fail("movement_type must be receive, issue, adjust, waste, or all", 400)
    try:
        movements, medicines = database_request("/stock_movements"), database_request("/medicines")
        batches = {batch["batch_id"]: batch for medicine in medicines for batch in medicine_batches(medicine["medicine_id"])}
        medicine_by_id = {medicine["medicine_id"]: medicine for medicine in medicines}
        medicine_id = request.args.get("medicine_id", type=int)
        performed_by = request.args.get("performed_by", "").strip().casefold()
        rows = []
        for movement in movements:
            movement_date = parse_date(movement["created_at"][:10])
            if (medicine_id and movement["medicine_id"] != medicine_id) or (movement_type != "all" and movement["movement_type"] != movement_type) or (from_date and movement_date < from_date) or (to_date and movement_date > to_date) or (performed_by and performed_by not in (movement.get("performed_by") or "").casefold()): continue
            medicine = medicine_by_id.get(movement["medicine_id"], {})
            batch = batches.get(movement.get("batch_id"))
            rows.append({**movement, "medicine_name": medicine.get("name"), "medicine_unit": medicine.get("unit"), "batch_number": batch.get("batch_number") if batch else None})
        rows.sort(key=lambda row: row["created_at"], reverse=True)
        summary = {kind: {"count": 0, "quantity": 0} for kind in ("receive", "issue", "adjust", "waste")}
        for row in rows:
            summary[row["movement_type"]]["count"] += 1
            summary[row["movement_type"]]["quantity"] += row["quantity"]
        rows, pagination = paginate(rows[:limit], *pagination_from_request())
        return jsonify({"movements": rows, "summary": summary, "pagination": pagination})
    except ValueError as exc: return fail(str(exc), 400)
    except DatabaseServiceError as exc: return fail(str(exc), exc.status)


def batch_rows(expiry_status="all", medicine_id=None, search="", include_empty=False):
    if expiry_status not in {"all", "expired", "expiring_7", "expiring_30", "expiring_90", "valid"}: raise ValueError("Invalid expiry_status")
    batches = database_request("/batches?include_expired=true&include_empty=" + ("true" if include_empty else "false"))
    medicines = {m["medicine_id"]: m for m in database_request("/medicines")}
    today = date.today(); term = search.casefold(); rows=[]
    for batch in batches:
        medicine=medicines.get(batch["medicine_id"], {}); days=(parse_date(batch["expiry_date"])-today).days
        status="expired" if days < 0 else "expiring_soon" if days <= 30 else "valid"
        if medicine_id and batch["medicine_id"] != medicine_id: continue
        if term and term not in batch["batch_number"].casefold() and term not in medicine.get("name", "").casefold(): continue
        if expiry_status=="expired" and days>=0 or expiry_status=="expiring_7" and not (0<=days<=7) or expiry_status=="expiring_30" and not (0<=days<=30) or expiry_status=="expiring_90" and not (0<=days<=90) or expiry_status=="valid" and days<=30: continue
        rows.append({**batch,"medicine_name":medicine.get("name"),"medicine_unit":medicine.get("unit"),"medicine_category":medicine.get("category"),"days_until_expiry":days,"expiry_status":status,"estimated_value":round(batch["quantity_remaining"]*medicine.get("unit_price",0),2)})
    rows.sort(key=lambda row: row["expiry_date"])
    summary={kind:{"count":0,"quantity":0} for kind in ("expired","expiring_7","expiring_30","valid")}
    for row in rows:
        if row["days_until_expiry"]<0: bucket="expired"
        elif row["days_until_expiry"]<=7: bucket="expiring_7"
        elif row["days_until_expiry"]<=30: bucket="expiring_30"
        else: bucket="valid"
        summary[bucket]["count"]+=1; summary[bucket]["quantity"]+=row["quantity_remaining"]
    summary["expired"]["estimated_value"]=round(sum(row["estimated_value"] for row in rows if row["days_until_expiry"]<0),2)
    return rows, summary


@app.get("/api/batches")
def batches():
    try:
        medicine_id=request.args.get("medicine_id",type=int)
        rows,summary=batch_rows(request.args.get("expiry_status","all"),medicine_id,request.args.get("search",""),request.args.get("include_empty","false")=="true")
        rows, pagination = paginate(rows, *pagination_from_request())
        return jsonify({"batches":rows,"summary":summary,"pagination":pagination})
    except ValueError as exc: return fail(str(exc),400)
    except DatabaseServiceError as exc: return fail(str(exc),exc.status)


@app.post("/api/batches/<int:batch_id>/write-off")
def write_off_batch(batch_id):
    if denied:=require_manager(): return denied
    reason=(request.get_json(silent=True) or {}).get("reason","").strip()
    if not reason: return fail("reason is required",400)
    try:
        batch=database_request(f"/batches/{batch_id}")
        if batch["quantity_remaining"]==0: return fail("Batch is already empty",400)
        medicine=database_request(f"/medicines/{batch['medicine_id']}"); quantity=batch["quantity_remaining"]
        # Separate HTTP calls cannot be transactional: write the ledger first so a failure never silently loses stock.
        database_request("/stock_movements","POST",{"medicine_id":batch["medicine_id"],"batch_id":batch_id,"movement_type":"waste","quantity":quantity,"reason":reason,"performed_by":request.headers.get("X-HOMS-Name","Pharmacy Manager"),"created_at":date.today().isoformat()+"T00:00:00"})
        database_request(f"/batches/{batch_id}","PUT",{"quantity_remaining":0})
        all_batches=database_request(f"/batches?medicine_id={batch['medicine_id']}&include_expired=true&include_empty=true")
        stock=sum(item["quantity_remaining"] for item in all_batches)
        database_request(f"/medicines/{batch['medicine_id']}","PUT",{**medicine,"stock_quantity":stock})
        return jsonify({"batch_id":batch_id,"medicine_name":medicine["name"],"quantity_written_off":quantity,"reason":reason,"stock_quantity":stock})
    except DatabaseServiceError as exc: return fail(str(exc),exc.status)


def active_medicine(medicine_id):
    medicine=database_request(f"/medicines/{medicine_id}")
    if medicine["status"] != "active": raise ValueError("Medicine is discontinued")
    return medicine


def recalculate_stock(medicine):
    batches=database_request(f"/batches?medicine_id={medicine['medicine_id']}&include_expired=false&include_empty=true")
    stock=sum(batch["quantity_remaining"] for batch in batches)
    database_request(f"/medicines/{medicine['medicine_id']}","PUT",{**medicine,"stock_quantity":stock})
    return stock


@app.post("/api/stock/issue")
def issue_stock():
    payload=request.get_json(silent=True) or {}
    try:
        medicine_id=int(payload.get("medicine_id")); quantity=int(payload.get("quantity")); reason=str(payload.get("reason","")).strip()
        if quantity<=0 or not reason: raise ValueError("quantity must be positive and reason is required")
        medicine=active_medicine(medicine_id)
        batches=database_request(f"/batches?medicine_id={medicine_id}&include_expired=false&include_empty=false")
        available=sum(batch["quantity_remaining"] for batch in batches)
        if available<quantity: return jsonify({"error":"Insufficient stock; nothing was changed","requested":quantity,"available":available,"shortfall":quantity-available}),409
        needed=quantity; breakdown=[]
        for batch in batches:
            if not needed: break
            taken=min(needed,batch["quantity_remaining"])
            # These independent HTTP calls have no shared transaction; record the ledger before changing stock.
            database_request("/stock_movements","POST",{"medicine_id":medicine_id,"batch_id":batch["batch_id"],"movement_type":"issue","quantity":taken,"reason":reason,"performed_by":request.headers.get("X-HOMS-Name","Pharmacy user"),"created_at":date.today().isoformat()+"T00:00:00"})
            database_request(f"/batches/{batch['batch_id']}","PUT",{"quantity_remaining":batch["quantity_remaining"]-taken})
            breakdown.append({"batch_number":batch["batch_number"],"expiry_date":batch["expiry_date"],"quantity_taken":taken}); needed-=taken
        return jsonify({"medicine_name":medicine["name"],"total_issued":quantity,"breakdown":breakdown,"stock_quantity":recalculate_stock(medicine)})
    except (ValueError,TypeError) as exc:return fail(str(exc),400)
    except DatabaseServiceError as exc:return fail(str(exc),exc.status)


@app.post("/api/stock/receive")
def receive_stock():
    payload=request.get_json(silent=True) or {}
    try:
        medicine_id=int(payload.get("medicine_id")); quantity=int(payload.get("quantity")); expiry=parse_date(payload.get("expiry_date",""))
        if quantity<=0: raise ValueError("quantity must be positive")
        if expiry < date.today(): raise ValueError("expiry_date cannot be in the past")
        medicine=active_medicine(medicine_id)
        batch=database_request("/batches","POST",{"medicine_id":medicine_id,"batch_number":str(payload.get("batch_number","")).strip(),"expiry_date":expiry.isoformat(),"quantity_received":quantity,"quantity_remaining":quantity,"received_at":date.today().isoformat()+"T00:00:00"})
        database_request("/stock_movements","POST",{"medicine_id":medicine_id,"batch_id":batch["batch_id"],"movement_type":"receive","quantity":quantity,"reason":"Delivery received","performed_by":request.headers.get("X-HOMS-Name","Pharmacy user"),"created_at":date.today().isoformat()+"T00:00:00"})
        stock=recalculate_stock(medicine)
        if payload.get("po_id"):
            order=database_request(f"/purchase_orders/{int(payload['po_id'])}"); received=order["quantity_received"]+quantity
            update={"quantity_received":received}
            if received>=order["quantity_ordered"]: update["status"]="received"
            database_request(f"/purchase_orders/{order['po_id']}","PUT",update)
        return jsonify({"batch":batch,"stock_quantity":stock})
    except (ValueError,TypeError) as exc:return fail(str(exc),400)
    except DatabaseServiceError as exc:return fail(str(exc),exc.status)


def enrich_order(order, medicines, suppliers):
    medicine=next((m for m in medicines if m["medicine_id"]==order["medicine_id"]),{})
    supplier=next((s for s in suppliers if s["supplier_id"]==order["supplier_id"]),{})
    total=(order.get("quantity_ordered") or 0)*(order.get("unit_price") or 0)
    return {**order,"medicine_name":medicine.get("name"),"medicine_unit":medicine.get("unit"),"supplier_name":supplier.get("name"),"supplier_lead_time_days":supplier.get("lead_time_days"),"total_value":round(total,2),"outstanding":order["quantity_ordered"]-order["quantity_received"],"is_overdue":bool(order.get("expected_at") and parse_date(order["expected_at"])<date.today() and order["status"] not in {"received","rejected","cancelled"})}

def orders_for_filters():
    orders=database_request("/purchase_orders"); meds=database_request("/medicines"); sups=database_request("/suppliers")
    status=request.args.get("status","all"); med=request.args.get("medicine_id",type=int); sup=request.args.get("supplier_id",type=int); ai=request.args.get("ai_generated")
    rows=[enrich_order(o,meds,sups) for o in orders if (status=="all" or o["status"]==status) and (not med or o["medicine_id"]==med) and (not sup or o["supplier_id"]==sup) and (ai not in {"true","1"} or o["ai_generated"]==1)]
    summary={};
    for row in rows:
        summary.setdefault(row["status"],{"count":0,"total_value":0}); summary[row["status"]]["count"]+=1; summary[row["status"]]["total_value"]+=row["total_value"]
    return rows,summary

@app.get("/api/purchase-orders")
def purchase_orders():
    try:
        rows,summary=orders_for_filters()
        rows.sort(key=lambda row: row["created_at"], reverse=True)
        rows, pagination = paginate(rows, *pagination_from_request())
        return jsonify({"purchase_orders":rows,"summary":summary,"pagination":pagination})
    except ValueError as exc:return fail(str(exc),400)
    except DatabaseServiceError as exc:return fail(str(exc),exc.status)
@app.get("/api/purchase-orders/open")
def open_purchase_orders():
    try:
        rows,_=orders_for_filters(); return jsonify({"purchase_orders":[r for r in rows if r["status"] in {"approved","ordered"} and r["outstanding"]>0]})
    except DatabaseServiceError as exc:return fail(str(exc),exc.status)
@app.get("/api/purchase-orders/<int:po_id>")
def purchase_order_detail(po_id):
    try:
        order=database_request(f"/purchase_orders/{po_id}"); return jsonify({"purchase_order":enrich_order(order,database_request("/medicines"),database_request("/suppliers"))})
    except DatabaseServiceError as exc:return fail(str(exc),exc.status)

def transition(po_id,target,reason=""):
    if denied:=require_manager():return denied
    try:
        order=database_request(f"/purchase_orders/{po_id}"); allowed={"approved":{"pending_approval"},"rejected":{"pending_approval"},"ordered":{"approved"},"cancelled":{"draft","pending_approval","approved","ordered"}}
        if order["status"] not in allowed[target]: return fail(f"Cannot set {target} from {order['status']}",409)
        if target in {"rejected","cancelled"} and not reason.strip(): return fail("decision_reason is required",400)
        return jsonify(database_request(f"/purchase_orders/{po_id}","PUT",{"status":target,"approved_by":request.headers.get("X-HOMS-Name","Pharmacy Manager"),"decision_reason":reason or None}))
    except DatabaseServiceError as exc:return fail(str(exc),exc.status)
@app.post("/api/purchase-orders/<int:po_id>/approve")
def po_approve(po_id):return transition(po_id,"approved",(request.get_json(silent=True) or {}).get("decision_reason", ""))
@app.post("/api/purchase-orders/<int:po_id>/reject")
def po_reject(po_id):return transition(po_id,"rejected",(request.get_json(silent=True) or {}).get("decision_reason", ""))
@app.post("/api/purchase-orders/<int:po_id>/mark-ordered")
def po_ordered(po_id):return transition(po_id,"ordered")
@app.post("/api/purchase-orders/<int:po_id>/cancel")
def po_cancel(po_id):return transition(po_id,"cancelled",(request.get_json(silent=True) or {}).get("decision_reason", ""))

def validate_order(payload):
    try:
        quantity=int(payload.get("quantity_ordered")); price=float(payload.get("unit_price")); medicine_id=int(payload.get("medicine_id")); supplier_id=int(payload.get("supplier_id"))
        if quantity<=0 or price<0: raise ValueError
    except (ValueError,TypeError): raise ValueError("quantity_ordered must be positive and unit_price must be >= 0") from None
    medicine=database_request(f"/medicines/{medicine_id}"); supplier=database_request(f"/suppliers/{supplier_id}")
    if medicine["status"]!="active" or supplier["status"]!="active": raise ValueError("medicine and supplier must be active")
    return {"medicine_id":medicine_id,"supplier_id":supplier_id,"quantity_ordered":quantity,"quantity_received":int(payload.get("quantity_received",0)),"unit_price":price,"status":payload.get("status","draft"),"created_by":payload.get("created_by","Pharmacy Manager"),"approved_by":payload.get("approved_by"),"ai_generated":0,"ai_reasoning":None,"decision_reason":payload.get("decision_reason"),"created_at":payload.get("created_at",date.today().isoformat()+"T00:00:00"),"expected_at":payload.get("expected_at")}
@app.post("/api/purchase-orders")
def po_create():
    if denied:=require_manager():return denied
    try:return jsonify(database_request("/purchase_orders","POST",validate_order(request.get_json(silent=True) or {}))),201
    except ValueError as exc:return fail(str(exc),400)
    except DatabaseServiceError as exc:return fail(str(exc),exc.status)
@app.put("/api/purchase-orders/<int:po_id>")
def po_update(po_id):
    if denied:=require_manager():return denied
    try:
        existing=database_request(f"/purchase_orders/{po_id}")
        if existing["status"] not in {"draft","pending_approval"}:return fail("Only draft or pending approval orders can be edited",409)
        return jsonify(database_request(f"/purchase_orders/{po_id}","PUT",validate_order({**existing,**(request.get_json(silent=True) or {})})))
    except ValueError as exc:return fail(str(exc),400)
    except DatabaseServiceError as exc:return fail(str(exc),exc.status)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
