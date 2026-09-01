import sqlite3
from datetime import datetime

from flask import Flask, jsonify, request

import db

DB_PATH = str(db.get_database_path())

VALID_MEDICINE_STATUSES = {"active", "discontinued"}
VALID_SUPPLIER_STATUSES = {"active", "discontinued"}
VALID_PO_STATUSES = {
    "draft",
    "pending_approval",
    "approved",
    "ordered",
    "received",
    "rejected",
    "cancelled",
}
VALID_MOVEMENT_TYPES = {"receive", "issue", "adjust", "waste"}

app = Flask(__name__)


def init_database():
    """Apply the database schema without inserting or modifying data."""
    with db.get_connection() as connection:
        db.apply_schema(connection)


def get_db():
    return db.connect()


def json_error(message, status_code):
    return jsonify({"error": message}), status_code


@app.errorhandler(400)
def handle_bad_request(_error):
    return json_error("Invalid request", 400)


@app.errorhandler(404)
def handle_not_found(_error):
    return json_error("Resource not found", 404)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy"})


@app.route("/staff", methods=["GET"])
def get_staff():
    """Return staff records for the backend/API service.

    ``role`` is optional and is limited to the two database role values used
    by Student 3's pharmacy demonstration.
    """
    role = request.args.get("role")
    if role not in (None, "manager", "staff"):
        return json_error("Invalid staff role", 400)

    conn = get_db()
    try:
        if role is None:
            rows = conn.execute(
                "SELECT staff_id, name, role, notes, created_at, updated_at "
                "FROM staff ORDER BY name ASC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT staff_id, name, role, notes, created_at, updated_at "
                "FROM staff WHERE role = ? ORDER BY name ASC",
                (role,),
            ).fetchall()
        return jsonify([dict(row) for row in rows])
    finally:
        conn.close()


@app.route("/staff/<int:staff_id>", methods=["GET"])
def get_staff_member(staff_id):
    """Return one staff record for the backend/API service."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT staff_id, name, role, notes, created_at, updated_at "
            "FROM staff WHERE staff_id = ?",
            (staff_id,),
        ).fetchone()
        if row is None:
            return json_error("Staff member not found", 404)
        return jsonify(dict(row))
    finally:
        conn.close()


@app.route("/medicines", methods=["GET"])
def get_medicines():
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM medicines ORDER BY medicine_id ASC"
        ).fetchall()
        return jsonify([dict(row) for row in rows])
    finally:
        conn.close()


@app.route("/medicines/<int:medicine_id>", methods=["GET"])
def get_medicine(medicine_id):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM medicines WHERE medicine_id = ?",
            (medicine_id,),
        ).fetchone()
        if row is None:
            return json_error("Medicine not found", 404)
        return jsonify(dict(row))
    finally:
        conn.close()


@app.route("/medicines", methods=["POST"])
def create_medicine():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return json_error("JSON body required", 400)

    try:
        name = validate_required_string(payload.get("name"), "name")
        category = validate_required_string(payload.get("category"), "category")
        unit = validate_required_string(payload.get("unit"), "unit")
        unit_price = validate_non_negative_float(payload.get("unit_price"), "unit_price")
        stock_quantity = validate_non_negative_int(payload.get("stock_quantity", 0), "stock_quantity")
        reorder_level = validate_non_negative_int(payload.get("reorder_level"), "reorder_level")
        storage_instructions = payload.get("storage_instructions")
        supplier_id = payload.get("supplier_id")
        if supplier_id is not None:
            supplier_id = validate_positive_int(supplier_id, "supplier_id")
        status = payload.get("status", "active")
        if status not in VALID_MEDICINE_STATUSES:
            return json_error("Invalid medicine status", 400)
    except ValueError as exc:
        return json_error(str(exc), 400)

    sql = """
        INSERT INTO medicines (
            name, category, unit, unit_price, stock_quantity,
            reorder_level, storage_instructions, supplier_id, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    conn = get_db()
    try:
        cursor = conn.execute(
            sql,
            (
                name,
                category,
                unit,
                unit_price,
                stock_quantity,
                reorder_level,
                storage_instructions,
                supplier_id,
                status,
            ),
        )
        conn.commit()
        medicine_id = cursor.lastrowid
        row = conn.execute(
            "SELECT * FROM medicines WHERE medicine_id = ?",
            (medicine_id,),
        ).fetchone()
        return jsonify(dict(row)), 201
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        return json_error(f"Database integrity error: {exc}", 400)
    finally:
        conn.close()


@app.route("/medicines/<int:medicine_id>", methods=["PUT"])
def update_medicine(medicine_id):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return json_error("JSON body required", 400)

    fields = []
    values = []

    for key in ("name", "category", "unit", "unit_price", "stock_quantity", "reorder_level", "storage_instructions", "supplier_id", "status"):
        if key in payload:
            if key in {"name", "category", "unit"}:
                value = validate_required_string(payload.get(key), key)
            elif key == "status":
                value = payload.get(key)
                if value not in VALID_MEDICINE_STATUSES:
                    return json_error("Invalid medicine status", 400)
            elif key in {"unit_price"}:
                value = validate_non_negative_float(payload.get(key), key)
            elif key in {"stock_quantity", "reorder_level"}:
                value = validate_non_negative_int(payload.get(key), key)
            elif key == "supplier_id":
                supplier_value = payload.get(key)
                if supplier_value is not None:
                    value = validate_positive_int(supplier_value, key)
                else:
                    value = None
            else:
                value = payload.get(key)
            fields.append(f"{key} = ?")
            values.append(value)

    if not fields:
        return json_error("No fields supplied for update", 400)

    values.append(medicine_id)
    conn = get_db()
    try:
        cursor = conn.execute(
            f"UPDATE medicines SET {', '.join(fields)} WHERE medicine_id = ?",
            values,
        )
        if cursor.rowcount == 0:
            return json_error("Medicine not found", 404)
        conn.commit()
        row = conn.execute(
            "SELECT * FROM medicines WHERE medicine_id = ?",
            (medicine_id,),
        ).fetchone()
        return jsonify(dict(row))
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        return json_error(f"Database integrity error: {exc}", 400)
    finally:
        conn.close()


@app.route("/medicines/<int:medicine_id>", methods=["DELETE"])
def soft_delete_medicine(medicine_id):
    conn = get_db()
    try:
        cursor = conn.execute(
            "UPDATE medicines SET status = 'discontinued' WHERE medicine_id = ?",
            (medicine_id,),
        )
        if cursor.rowcount == 0:
            return json_error("Medicine not found", 404)
        conn.commit()
        return jsonify({"medicine_id": medicine_id, "status": "discontinued"})
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        return json_error(f"Database integrity error: {exc}", 400)
    finally:
        conn.close()


@app.route("/medicines/<int:medicine_id>/batches", methods=["GET"])
def get_medicine_batches(medicine_id):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT 1 FROM medicines WHERE medicine_id = ?",
            (medicine_id,),
        ).fetchone()
        if row is None:
            return json_error("Medicine not found", 404)
        rows = conn.execute(
            "SELECT * FROM batches WHERE medicine_id = ? ORDER BY batch_id ASC",
            (medicine_id,),
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


@app.route("/suppliers", methods=["GET"])
def get_suppliers():
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM suppliers ORDER BY supplier_id ASC").fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


@app.route("/suppliers/<int:supplier_id>", methods=["GET"])
def get_supplier(supplier_id):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM suppliers WHERE supplier_id = ?",
            (supplier_id,),
        ).fetchone()
        if row is None:
            return json_error("Supplier not found", 404)
        return jsonify(dict(row))
    finally:
        conn.close()


@app.route("/suppliers", methods=["POST"])
def create_supplier():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return json_error("JSON body required", 400)

    try:
        name = validate_required_string(payload.get("name"), "name")
        contact_email = payload.get("contact_email")
        phone = payload.get("phone")
        lead_time_days = validate_non_negative_int(payload.get("lead_time_days"), "lead_time_days")
        status = payload.get("status", "active")
        if status not in VALID_SUPPLIER_STATUSES:
            return json_error("Invalid supplier status", 400)
    except ValueError as exc:
        return json_error(str(exc), 400)

    conn = get_db()
    try:
        cursor = conn.execute(
            """
            INSERT INTO suppliers (name, contact_email, phone, lead_time_days, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, contact_email, phone, lead_time_days, status),
        )
        conn.commit()
        supplier_id = cursor.lastrowid
        row = conn.execute(
            "SELECT * FROM suppliers WHERE supplier_id = ?",
            (supplier_id,),
        ).fetchone()
        return jsonify(dict(row)), 201
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        return json_error(f"Database integrity error: {exc}", 400)
    finally:
        conn.close()


@app.route("/suppliers/<int:supplier_id>", methods=["PUT"])
def update_supplier(supplier_id):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return json_error("JSON body required", 400)

    fields = []
    values = []
    for key in ("name", "contact_email", "phone", "lead_time_days", "status"):
        if key in payload:
            if key == "name":
                value = validate_required_string(payload.get(key), key)
            elif key == "lead_time_days":
                value = validate_non_negative_int(payload.get(key), key)
            elif key == "status":
                value = payload.get(key)
                if value not in VALID_SUPPLIER_STATUSES:
                    return json_error("Invalid supplier status", 400)
            else:
                value = payload.get(key)
            fields.append(f"{key} = ?")
            values.append(value)

    if not fields:
        return json_error("No fields supplied for update", 400)

    values.append(supplier_id)
    conn = get_db()
    try:
        cursor = conn.execute(
            f"UPDATE suppliers SET {', '.join(fields)} WHERE supplier_id = ?",
            values,
        )
        if cursor.rowcount == 0:
            return json_error("Supplier not found", 404)
        conn.commit()
        row = conn.execute(
            "SELECT * FROM suppliers WHERE supplier_id = ?",
            (supplier_id,),
        ).fetchone()
        return jsonify(dict(row))
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        return json_error(f"Database integrity error: {exc}", 400)
    finally:
        conn.close()


@app.route("/suppliers/<int:supplier_id>", methods=["DELETE"])
def soft_delete_supplier(supplier_id):
    conn = get_db()
    try:
        cursor = conn.execute(
            "UPDATE suppliers SET status = 'discontinued' WHERE supplier_id = ?",
            (supplier_id,),
        )
        if cursor.rowcount == 0:
            return json_error("Supplier not found", 404)
        conn.commit()
        return jsonify({"supplier_id": supplier_id, "status": "discontinued"})
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        return json_error(f"Database integrity error: {exc}", 400)
    finally:
        conn.close()


@app.route("/purchase_orders", methods=["GET"])
def get_purchase_orders():
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM purchase_orders ORDER BY created_at DESC"
        ).fetchall()
        return jsonify([dict(row) for row in rows])
    finally:
        conn.close()


@app.route("/purchase_orders/<int:po_id>", methods=["GET"])
def get_purchase_order(po_id):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM purchase_orders WHERE po_id = ?",
            (po_id,),
        ).fetchone()
        if row is None:
            return json_error("Purchase order not found", 404)
        return jsonify(dict(row))
    finally:
        conn.close()


@app.route("/purchase_orders", methods=["POST"])
def create_purchase_order():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return json_error("JSON body required", 400)

    try:
        medicine_id = validate_positive_int(payload.get("medicine_id"), "medicine_id")
        supplier_id = validate_positive_int(payload.get("supplier_id"), "supplier_id")
        quantity_ordered = validate_positive_int(payload.get("quantity_ordered"), "quantity_ordered")
        quantity_received = validate_non_negative_int(payload.get("quantity_received", 0), "quantity_received")
        unit_price = payload.get("unit_price")
        if unit_price is not None:
            unit_price = validate_non_negative_float(unit_price, "unit_price")
        status = payload.get("status", "draft")
        if status not in VALID_PO_STATUSES:
            return json_error("Invalid purchase order status", 400)
        created_by = payload.get("created_by")
        approved_by = payload.get("approved_by")
        ai_generated = payload.get("ai_generated", 0)
        if ai_generated not in (0, 1):
            return json_error("ai_generated must be 0 or 1", 400)
        ai_reasoning = payload.get("ai_reasoning")
        decision_reason = payload.get("decision_reason")
        created_at = validate_required_string(payload.get("created_at"), "created_at")
        expected_at = payload.get("expected_at")
    except ValueError as exc:
        return json_error(str(exc), 400)

    conn = get_db()
    try:
        cursor = conn.execute(
            """
            INSERT INTO purchase_orders (
                medicine_id, supplier_id, quantity_ordered, quantity_received,
                unit_price, status, created_by, approved_by, ai_generated,
                ai_reasoning, decision_reason, created_at, expected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                medicine_id,
                supplier_id,
                quantity_ordered,
                quantity_received,
                unit_price,
                status,
                created_by,
                approved_by,
                ai_generated,
                ai_reasoning,
                decision_reason,
                created_at,
                expected_at,
            ),
        )
        conn.commit()
        po_id = cursor.lastrowid
        row = conn.execute(
            "SELECT * FROM purchase_orders WHERE po_id = ?",
            (po_id,),
        ).fetchone()
        return jsonify(dict(row)), 201
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        return json_error(f"Database integrity error: {exc}", 400)
    finally:
        conn.close()


@app.route("/purchase_orders/<int:po_id>/status", methods=["PATCH"])
def patch_purchase_order_status(po_id):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return json_error("JSON body required", 400)

    status = payload.get("status")
    if status not in VALID_PO_STATUSES:
        return json_error("Invalid purchase order status", 400)

    conn = get_db()
    try:
        cursor = conn.execute(
            "UPDATE purchase_orders SET status = ? WHERE po_id = ?",
            (status, po_id),
        )
        if cursor.rowcount == 0:
            return json_error("Purchase order not found", 404)
        conn.commit()
        row = conn.execute(
            "SELECT * FROM purchase_orders WHERE po_id = ?",
            (po_id,),
        ).fetchone()
        return jsonify(dict(row))
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        return json_error(f"Database integrity error: {exc}", 400)
    finally:
        conn.close()


@app.route("/stock_movements", methods=["GET"])
def get_stock_movements():
    conn = get_db()
    try:
        query = "SELECT * FROM stock_movements WHERE 1 = 1"
        params = []

        from_date = request.args.get("from")
        if from_date:
            try:
                datetime.strptime(from_date, "%Y-%m-%d")
            except ValueError:
                return json_error("from must be in YYYY-MM-DD format", 400)
            query += " AND created_at >= ?"
            params.append(f"{from_date}T00:00:00")

        to_date = request.args.get("to")
        if to_date:
            try:
                datetime.strptime(to_date, "%Y-%m-%d")
            except ValueError:
                return json_error("to must be in YYYY-MM-DD format", 400)
            query += " AND created_at <= ?"
            params.append(f"{to_date}T23:59:59")

        query += " ORDER BY created_at ASC, movement_id ASC"
        rows = conn.execute(query, params).fetchall()
        return jsonify([dict(row) for row in rows])
    finally:
        conn.close()


@app.route("/stock_movements", methods=["POST"])
def create_stock_movement():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return json_error("JSON body required", 400)

    try:
        medicine_id = validate_positive_int(payload.get("medicine_id"), "medicine_id")
        batch_id = payload.get("batch_id")
        if batch_id is not None:
            batch_id = validate_positive_int(batch_id, "batch_id")
        movement_type = payload.get("movement_type")
        if movement_type not in VALID_MOVEMENT_TYPES:
            return json_error("Invalid movement_type", 400)
        quantity = validate_positive_int(payload.get("quantity"), "quantity")
        reason = payload.get("reason")
        performed_by = payload.get("performed_by")
        created_at = validate_required_string(payload.get("created_at"), "created_at")
    except ValueError as exc:
        return json_error(str(exc), 400)

    conn = get_db()
    try:
        cursor = conn.execute(
            """
            INSERT INTO stock_movements (
                medicine_id, batch_id, movement_type, quantity,
                reason, performed_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                medicine_id,
                batch_id,
                movement_type,
                quantity,
                reason,
                performed_by,
                created_at,
            ),
        )
        conn.commit()
        movement_id = cursor.lastrowid
        row = conn.execute(
            "SELECT * FROM stock_movements WHERE movement_id = ?",
            (movement_id,),
        ).fetchone()
        return jsonify(dict(row)), 201
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        return json_error(f"Database integrity error: {exc}", 400)
    finally:
        conn.close()


def validate_required_string(value, field_name):
    if value is None or str(value).strip() == "":
        raise ValueError(f"{field_name} is required")
    return str(value)


def validate_positive_int(value, field_name):
    if value is None:
        raise ValueError(f"{field_name} is required")
    try:
        value_int = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be an integer") from None
    if value_int <= 0:
        raise ValueError(f"{field_name} must be greater than 0")
    return value_int


def validate_non_negative_int(value, field_name):
    if value is None:
        raise ValueError(f"{field_name} is required")
    try:
        value_int = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be an integer") from None
    if value_int < 0:
        raise ValueError(f"{field_name} must be >= 0")
    return value_int


def validate_non_negative_float(value, field_name):
    if value is None:
        raise ValueError(f"{field_name} is required")
    try:
        value_float = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be numeric") from None
    if value_float < 0:
        raise ValueError(f"{field_name} must be >= 0")
    return value_float


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=6300, debug=False)
