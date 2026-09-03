#!/usr/bin/env python3
"""Student 3 pharmacy demonstration entry screen.

Run ``python3 app.py`` after initialising ``../database`` with
``python3 init_db.py --check`` and starting the Student 3 backend API.
"""

from __future__ import annotations

import os
import csv
import io
from datetime import date
from pathlib import Path

from flask import (Flask, Response, jsonify, redirect, render_template, request, send_from_directory,
                   session, url_for)

import api_client

ROLE_MANAGER = "Pharmacy Manager"
ROLE_PHARMACIST = "Pharmacist"
ROLE_TO_DATABASE_VALUE = {
    ROLE_MANAGER: "manager",
    ROLE_PHARMACIST: "staff",
}
BACKEND_API_URL = os.environ.get("BACKEND_API_URL", "http://localhost:5300").rstrip("/")
BASE_DIR = Path(__file__).resolve().parent
SHARED_FRONTEND_DIR = BASE_DIR.parents[1] / "shared" / "frontend"

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or os.urandom(32)
app.config["BACKEND_API_URL"] = BACKEND_API_URL


def staff_by_role() -> dict[str, list[dict]]:
    """Group staff returned by the Student 3 backend API for the demo."""
    rows = api_client.list_staff()
    people = {ROLE_MANAGER: [], ROLE_PHARMACIST: []}
    for row in rows:
        for label, database_role in ROLE_TO_DATABASE_VALUE.items():
            if row["role"] == database_role:
                people[label].append(row)
                break
    return people


@app.before_request
def require_demo_identity():
    """Send anonymous visitors to the role picker before any application page.

    The demo routes and asset endpoints are deliberately excluded so the
    picker itself, its scripts/styles, and shared CSS cannot redirect in a
    loop.
    """
    open_endpoints = {
        "demo_entry",
        "demo_enter",
        "demo_exit",
        "static",
        "shared_assets",
        "health",
    }
    if request.endpoint in open_endpoints or request.endpoint is None:
        return None
    if session.get("demo_identity"):
        return None
    return redirect(url_for("demo_entry"))


@app.get("/demo")
def demo_entry():
    """Show the simulated-role selection screen."""
    try:
        people = staff_by_role()
        error = None
    except api_client.BackendError as exc:
        people = {ROLE_MANAGER: [], ROLE_PHARMACIST: []}
        error = f"Staff records are unavailable: {exc}"

    return render_template(
        "demo_entry.html",
        people=people,
        error=error,
        selected_role=request.args.get("role", ""),
        selected_staff_id=request.args.get("staff_id", ""),
        role_manager=ROLE_MANAGER,
        role_pharmacist=ROLE_PHARMACIST,
    )


@app.post("/demo/enter")
def demo_enter():
    """Validate and store the selected simulated pharmacy identity."""
    role = request.form.get("role", "")
    staff_id = request.form.get("staff_id", "")
    if role not in ROLE_TO_DATABASE_VALUE or not staff_id.isdigit():
        return redirect(url_for("demo_entry", role=role, staff_id=staff_id))

    try:
        person = api_client.get_staff(int(staff_id))
    except api_client.BackendError:
        person = None

    if person is None or person.get("role") != ROLE_TO_DATABASE_VALUE[role]:
        return redirect(url_for("demo_entry", role=role, staff_id=staff_id))

    session["demo_identity"] = {
        "staff_id": person["staff_id"],
        "name": person["name"],
        "role": role,
    }
    return redirect(url_for("dashboard"))


def render_page(template_name: str, title: str, note: str):
    """Render one of the intentionally empty Pharmacy Operations pages."""
    return render_template(
        template_name,
        title=title,
        note=note,
        identity=session.get("demo_identity"),
    )


def current_identity():
    return session.get("demo_identity")


def is_manager() -> bool:
    return bool(current_identity() and current_identity().get("role") == ROLE_MANAGER)


def supplier_filters():
    return request.values.get("status", "active"), request.values.get("search", "").strip()


def supplier_form_data():
    return {key: request.form.get(key, "") for key in ("name", "contact_email", "phone", "lead_time_days")}


def medicine_filters():
    return {"search": request.values.get("search", "").strip(), "category": request.values.get("category", "all"), "status": request.values.get("status", "active"), "stock_status": request.values.get("stock_status", "all"), "expiring_within": request.values.get("expiring_within", "")}


def medicine_form_data():
    return {key: request.form.get(key, "") for key in ("name", "category", "unit", "unit_price", "stock_quantity", "reorder_level", "storage_instructions", "supplier_id")}

@app.get("/medicines/<int:medicine_id>/stock-form")
def stock_form(medicine_id):
    try: return render_template("partials/stock_form.html", medicine=api_client.get_medicine(medicine_id)["medicine"])
    except api_client.BackendError as exc:return str(exc),exc.status
@app.post("/medicines/<int:medicine_id>/issue")
def issue_medicine(medicine_id):
    try:
        result=api_client.issue_stock({"medicine_id":medicine_id,"quantity":request.form.get("quantity"),"reason":request.form.get("reason")},current_identity()["role"])
        return render_template("partials/issue_result.html",result=result)
    except api_client.BackendError as exc:return f'<div class="alert alert--danger"><div><p class="alert__body">{exc}. Nothing was changed.</p></div></div>',exc.status
@app.post("/medicines/<int:medicine_id>/receive")
def receive_medicine(medicine_id):
    try:return jsonify(api_client.receive_stock({"medicine_id":medicine_id,"batch_number":request.form.get("batch_number"),"expiry_date":request.form.get("expiry_date"),"quantity":request.form.get("quantity"),"po_id":request.form.get("po_id") or None},current_identity()["role"]))
    except api_client.BackendError as exc:return str(exc),exc.status
@app.get("/purchase-orders")
def purchase_orders_list():
    filters={k:request.values.get(k,"") for k in ("status","medicine_id","supplier_id","ai_generated")}; filters["status"]=filters["status"] or "all"
    try: payload=api_client.list_purchase_orders(**filters); meds=api_client.list_medicines(status="all",category="all",stock_status="all",search="",expiring_within="")["medicines"]; sups=api_client.list_suppliers("all"); error=None
    except api_client.BackendError as exc:payload={"purchase_orders":[],"summary":{}};meds=[];sups=[];error=str(exc)
    return render_template("purchase_orders_list.html",identity=current_identity(),is_manager=is_manager(),filters=filters,medicines=meds,suppliers=sups,error=error,**payload)
@app.get("/purchase-orders/table")
def purchase_orders_table():
    try:return render_template("partials/purchase_orders_table.html",is_manager=is_manager(),**api_client.list_purchase_orders(**{k:request.values.get(k,"") for k in ("status","medicine_id","supplier_id","ai_generated")}))
    except api_client.BackendError as exc:return f'<tr><td colspan="10">{exc}</td></tr>',exc.status
@app.get("/purchase-orders/<int:po_id>/detail")
def po_detail_panel(po_id):
    try:return render_template("partials/purchase_order_detail.html",order=api_client.get_purchase_order(po_id))
    except api_client.BackendError as exc:return str(exc),exc.status
@app.get("/purchase-orders/export")
def po_export():
    try:rows=api_client.list_purchase_orders(**{k:request.values.get(k,"") for k in ("status","medicine_id","supplier_id","ai_generated")})["purchase_orders"]
    except api_client.BackendError as exc:return str(exc),exc.status
    out=io.StringIO();w=csv.writer(out);w.writerow(["Order #","Medicine","Supplier","Ordered","Received","Unit price","Total","Expected","Status"])
    for r in rows:w.writerow([r["po_id"],r["medicine_name"],r["supplier_name"],r["quantity_ordered"],r["quantity_received"],r["unit_price"],r["total_value"],r.get("expected_at") or "",r["status"]])
    return Response(out.getvalue(),mimetype="text/csv",headers={"Content-Disposition":f"attachment; filename=purchase_orders_{date.today().isoformat()}.csv"})
@app.post("/purchase-orders/<int:po_id>/<action>")
def po_action(po_id,action):
    if not is_manager(): return "Pharmacy Manager role required",403
    try: api_client.transition_purchase_order(po_id,action,request.form.get("decision_reason",""),current_identity()["role"])
    except api_client.BackendError as exc:return str(exc),exc.status
    return redirect(url_for("purchase_orders_list"))


def movement_filters():
    return {"medicine_id": request.values.get("medicine_id", ""), "movement_type": request.values.get("movement_type", "all"), "from": request.values.get("from", ""), "to": request.values.get("to", ""), "performed_by": request.values.get("performed_by", ""), "limit": "100"}

def batch_filters(): return {"medicine_id":request.values.get("medicine_id",""),"expiry_status":request.values.get("expiry_status","all"),"search":request.values.get("search",""),"include_empty":request.values.get("include_empty","false")}


@app.get("/shared/<path:filename>")
def shared_assets(filename: str):
    """Serve the team-owned shared frontend assets, matching Student 5."""
    return send_from_directory(SHARED_FRONTEND_DIR, filename)


@app.get("/")
def dashboard():
    return render_page(
        "dashboard.html",
        "Dashboard",
        "Dashboard summary content will be added here later.",
    )


@app.get("/medicines")
def medicines_list():
    filters = medicine_filters()
    try:
        payload, error = api_client.list_medicines(**filters), None
    except api_client.BackendError as exc:
        payload, error = {"medicines": [], "categories": []}, str(exc)
    return render_template("medicines_list.html", title="Medicines", identity=current_identity(), is_manager=is_manager(), filters=filters, error=error, **payload)


@app.get("/medicines/table")
def medicines_table():
    try:
        payload = api_client.list_medicines(**medicine_filters())
        return render_template("partials/medicines_table.html", medicines=payload["medicines"], is_manager=is_manager())
    except api_client.BackendError as exc: return f'<tr><td colspan="9">{exc}</td></tr>', exc.status


@app.get("/medicines/<int:medicine_id>/detail")
def medicine_detail_panel(medicine_id):
    try: return render_template("partials/medicine_detail.html", **api_client.get_medicine(medicine_id))
    except api_client.BackendError as exc: return f"<p>{exc}</p>", exc.status


@app.get("/medicines/form")
def medicine_form():
    if not is_manager():
        return "Pharmacy Manager role required", 403
    medicine_id = request.args.get("medicine_id", type=int)
    try:
        medicine = api_client.get_medicine(medicine_id)["medicine"] if medicine_id else None
        suppliers = api_client.list_suppliers("active")
        return render_template("partials/medicine_form.html", medicine=medicine, suppliers=suppliers, error=None)
    except api_client.BackendError as exc: return f"<p>{exc}</p>", exc.status


@app.post("/medicines/save")
def medicine_save():
    if not is_manager(): return "Pharmacy Manager role required", 403
    medicine_id = request.form.get("medicine_id", type=int)
    try: api_client.save_medicine(medicine_form_data(), current_identity()["role"], medicine_id)
    except api_client.BackendError as exc:
        return render_template("partials/medicine_form.html", medicine={**medicine_form_data(), "medicine_id": medicine_id}, suppliers=api_client.list_suppliers("active"), error=str(exc)), exc.status
    return redirect(url_for("medicines_list"))


@app.post("/medicines/<int:medicine_id>/deactivate")
def medicine_deactivate(medicine_id):
    if not is_manager(): return "Pharmacy Manager role required", 403
    try: api_client.deactivate_medicine(medicine_id, current_identity()["role"])
    except api_client.BackendError as exc: return str(exc), exc.status
    return redirect(url_for("medicines_list", status="all"))


@app.post("/medicines/<int:medicine_id>/reactivate")
def medicine_reactivate(medicine_id):
    if not is_manager(): return "Pharmacy Manager role required", 403
    try: api_client.reactivate_medicine(medicine_id, current_identity()["role"])
    except api_client.BackendError as exc: return str(exc), exc.status
    return redirect(url_for("medicines_list", status="all"))


@app.get("/medicines/export")
def medicines_export():
    try: medicines = api_client.list_medicines(**medicine_filters())["medicines"]
    except api_client.BackendError as exc: return str(exc), exc.status
    output = io.StringIO(); writer = csv.writer(output)
    writer.writerow(["Name", "Category", "Unit", "Unit price", "Available quantity", "Reorder level", "Supplier", "Status"])
    for medicine in medicines: writer.writerow([medicine["name"], medicine["category"], medicine["unit"], medicine["unit_price"], medicine["available_quantity"], medicine["reorder_level"], medicine.get("supplier_name") or "", medicine["status"]])
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename=medicines_{date.today().isoformat()}.csv"})


@app.get("/batches")
def batches():
    filters=batch_filters()
    try: payload=api_client.list_batches(**filters); medicines=api_client.list_medicines(status="all",category="all",stock_status="all",search="",expiring_within="")["medicines"]; error=None
    except api_client.BackendError as exc: payload={"batches":[],"summary":{}}; medicines=[]; error=str(exc)
    return render_template("batches.html",identity=current_identity(),is_manager=is_manager(),filters=filters,medicines=medicines,error=error,**payload)
@app.get("/batches/table")
def batches_table():
    try: return render_template("partials/batches_table.html",is_manager=is_manager(),**api_client.list_batches(**batch_filters()))
    except api_client.BackendError as exc: return f'<tr><td colspan="8">{exc}</td></tr>',exc.status
@app.get("/batches/export")
def batches_export():
    try: rows=api_client.list_batches(**batch_filters())["batches"]
    except api_client.BackendError as exc: return str(exc),exc.status
    output=io.StringIO(); w=csv.writer(output); w.writerow(["Medicine","Batch","Expiry","Days","Quantity","Estimated value","Status"])
    for r in rows:w.writerow([r["medicine_name"],r["batch_number"],r["expiry_date"],r["days_until_expiry"],r["quantity_remaining"],r["estimated_value"],r["expiry_status"]])
    return Response(output.getvalue(),mimetype="text/csv",headers={"Content-Disposition":f"attachment; filename=batches_{date.today().isoformat()}.csv"})
@app.post("/batches/<int:batch_id>/write-off")
def batch_write_off(batch_id):
    if not is_manager(): return "Pharmacy Manager role required",403
    try: result=api_client.write_off_batch(batch_id,request.form.get("reason",""),current_identity()["role"]); return f'<div class="alert alert--success"><div><p class="alert__body">Wrote off {result["quantity_written_off"]} units. <a href="/movements">View stock movements</a></p></div></div>'
    except api_client.BackendError as exc:return str(exc),exc.status


@app.get("/movements")
def movements():
    filters = movement_filters()
    try:
        payload, medicine_rows, error = api_client.list_stock_movements(**filters), api_client.list_medicines(status="all", category="all", stock_status="all", search="", expiring_within="")["medicines"], None
    except api_client.BackendError as exc:
        payload, medicine_rows, error = {"movements": [], "summary": {}}, [], str(exc)
    return render_template("movements.html", title="Stock Movements", identity=current_identity(), filters=filters, medicines=medicine_rows, error=error, **payload)


@app.get("/movements/table")
def movements_table():
    try:
        payload = api_client.list_stock_movements(**movement_filters())
        return render_template("partials/movements_table.html", **payload)
    except api_client.BackendError as exc: return f'<tr><td colspan="7">{exc}</td></tr>', exc.status


@app.get("/movements/summary")
def movements_summary():
    try: return render_template("partials/movements_summary.html", **api_client.list_stock_movements(**movement_filters()))
    except api_client.BackendError as exc: return f"<p>{exc}</p>", exc.status


@app.get("/movements/export")
def movements_export():
    try: movements = api_client.list_stock_movements(**movement_filters())["movements"]
    except api_client.BackendError as exc: return str(exc), exc.status
    output = io.StringIO(); writer = csv.writer(output)
    writer.writerow(["Date & time", "Medicine", "Batch", "Type", "Quantity", "Reason", "Performed by"])
    for movement in movements: writer.writerow([movement["created_at"], movement.get("medicine_name") or "", movement.get("batch_number") or "", movement["movement_type"], movement["quantity"], movement.get("reason") or "", movement.get("performed_by") or ""])
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename=stock_movements_{date.today().isoformat()}.csv"})


@app.get("/suppliers")
def suppliers_list():
    status, search = supplier_filters()
    try:
        suppliers = api_client.list_suppliers(status, search)
        error = None
    except api_client.BackendError as exc:
        suppliers, error = [], str(exc)
    return render_template("suppliers_list.html", title="Suppliers", identity=current_identity(),
                           is_manager=is_manager(), suppliers=suppliers, status=status,
                           search=search, error=error)


@app.get("/suppliers/table")
def suppliers_table():
    status, search = supplier_filters()
    try:
        suppliers = api_client.list_suppliers(status, search)
        return render_template("partials/suppliers_table.html", suppliers=suppliers,
                               is_manager=is_manager(), status=status, search=search)
    except api_client.BackendError as exc:
        return f'<tr><td colspan="8">{exc}</td></tr>', exc.status


@app.get("/suppliers/<int:supplier_id>/detail")
def supplier_detail_panel(supplier_id):
    try:
        return render_template("partials/supplier_detail.html", **api_client.get_supplier(supplier_id))
    except api_client.BackendError as exc:
        return f"<p>{exc}</p>", exc.status


@app.get("/suppliers/form")
def supplier_form():
    if not is_manager():
        return "Pharmacy Manager role required", 403
    supplier_id = request.args.get("supplier_id", type=int)
    supplier, error = None, None
    if supplier_id:
        try: supplier = api_client.get_supplier(supplier_id)["supplier"]
        except api_client.BackendError as exc: error = str(exc)
    return render_template("partials/supplier_form.html", supplier=supplier, error=error)


@app.post("/suppliers/save")
def supplier_save():
    if not is_manager():
        return "Pharmacy Manager role required", 403
    supplier_id = request.form.get("supplier_id", type=int)
    try:
        api_client.save_supplier(supplier_form_data(), current_identity()["role"], supplier_id)
    except api_client.BackendError as exc:
        return render_template("partials/supplier_form.html", supplier={**supplier_form_data(), "supplier_id": supplier_id}, error=str(exc)), exc.status
    return redirect(url_for("suppliers_list"))


@app.post("/suppliers/<int:supplier_id>/deactivate")
def supplier_deactivate(supplier_id):
    if not is_manager(): return "Pharmacy Manager role required", 403
    try: api_client.deactivate_supplier(supplier_id, current_identity()["role"])
    except api_client.BackendError as exc: return str(exc), exc.status
    return redirect(url_for("suppliers_list", status="all"))


@app.post("/suppliers/<int:supplier_id>/reactivate")
def supplier_reactivate(supplier_id):
    if not is_manager(): return "Pharmacy Manager role required", 403
    try: api_client.reactivate_supplier(supplier_id, current_identity()["role"])
    except api_client.BackendError as exc: return str(exc), exc.status
    return redirect(url_for("suppliers_list", status="all"))


@app.get("/suppliers/export")
def suppliers_export():
    status, search = supplier_filters()
    try: suppliers = api_client.list_suppliers(status, search)
    except api_client.BackendError as exc: return str(exc), exc.status
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Name", "Contact email", "Phone", "Lead time (days)", "Medicines supplied", "Open orders", "Status"])
    for supplier in suppliers:
        writer.writerow([supplier["name"], supplier.get("contact_email") or "", supplier.get("phone") or "", supplier["lead_time_days"], supplier["medicines_supplied"], supplier["open_orders"], supplier["status"]])
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename=suppliers_{date.today().isoformat()}.csv"})


@app.post("/demo/exit")
def demo_exit():
    session.pop("demo_identity", None)
    return redirect(url_for("demo_entry"))


@app.get("/health")
def health():
    return {"status": "ok", "service": "student-3-frontend"}


if __name__ == "__main__":
    port = int(os.environ.get("FRONTEND_PORT", "3300"))
    app.run(host="0.0.0.0", port=port, debug=False)
