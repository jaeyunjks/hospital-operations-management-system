-- ==========================================================================
-- Student 5 — Staff & Shift Management
-- Database microservice schema (SQLite)
-- ==========================================================================
-- Implements the approved design in:
--   docs/architecture/student-5-database-design.md
--   docs/prompts/student-5/database-development.md  (S5-DB-001)
--
-- Entities:  STAFF, SHIFT, SHIFT_ASSIGNMENT
-- Relationship:  STAFF 1:M SHIFT_ASSIGNMENT M:1 SHIFT
--
-- This schema owns only the data required by the Staff & Shift Management
-- feature. It holds no references to any other student's database.
-- ==========================================================================

PRAGMA foreign_keys = ON;

-- --------------------------------------------------------------------------
-- STAFF — hospital workforce information
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS staff (
    staff_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT    NOT NULL,
    role                TEXT    NOT NULL,
    department          TEXT    NOT NULL,
    specialisation      TEXT,
    availability_status TEXT    NOT NULL DEFAULT 'Available'
                        CHECK (availability_status IN ('Available', 'Unavailable', 'On Leave')),
    employment_status   TEXT    NOT NULL DEFAULT 'Full-Time'
                        CHECK (employment_status IN ('Full-Time', 'Part-Time', 'Casual', 'Contract')),
    notes               TEXT,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- --------------------------------------------------------------------------
-- SHIFT — planned hospital shifts
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS shift (
    shift_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    department           TEXT    NOT NULL,
    shift_date           TEXT    NOT NULL,   -- ISO 8601 date: YYYY-MM-DD
    start_time           TEXT    NOT NULL,   -- 24-hour time: HH:MM
    end_time             TEXT    NOT NULL,   -- 24-hour time: HH:MM (may cross midnight)
    required_role        TEXT    NOT NULL,
    required_staff_count INTEGER NOT NULL DEFAULT 1
                         CHECK (required_staff_count > 0),
    shift_status         TEXT    NOT NULL DEFAULT 'Planned'
                         CHECK (shift_status IN ('Planned', 'Open', 'Filled', 'Completed', 'Cancelled')),
    notes                TEXT,
    created_at           TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at           TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- --------------------------------------------------------------------------
-- SHIFT_ASSIGNMENT — resolves the M:N relationship between staff and shifts
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS shift_assignment (
    assignment_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    shift_id          INTEGER NOT NULL,
    staff_id          INTEGER NOT NULL,
    assignment_status TEXT    NOT NULL DEFAULT 'Assigned'
                      CHECK (assignment_status IN ('Assigned', 'Confirmed', 'Declined', 'Cancelled', 'Completed')),
    approved_by       TEXT,
    approved_at       TEXT,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT    NOT NULL DEFAULT (datetime('now')),

    -- A shift cannot be deleted while leaving orphaned assignments behind.
    CONSTRAINT fk_assignment_shift
        FOREIGN KEY (shift_id) REFERENCES shift (shift_id)
        ON DELETE CASCADE,

    -- Staff records are retained: a staff member with assignment history
    -- cannot be deleted until those assignments are removed first.
    CONSTRAINT fk_assignment_staff
        FOREIGN KEY (staff_id) REFERENCES staff (staff_id)
        ON DELETE RESTRICT,

    -- The same staff member may only be assigned to a given shift once.
    CONSTRAINT uq_assignment_shift_staff
        UNIQUE (shift_id, staff_id)
);

-- --------------------------------------------------------------------------
-- Indexes — support the common lookups of the backend/API microservice
-- --------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_staff_department     ON staff (department);
CREATE INDEX IF NOT EXISTS idx_staff_role           ON staff (role);
CREATE INDEX IF NOT EXISTS idx_staff_availability   ON staff (availability_status);

CREATE INDEX IF NOT EXISTS idx_shift_date           ON shift (shift_date);
CREATE INDEX IF NOT EXISTS idx_shift_department     ON shift (department);
CREATE INDEX IF NOT EXISTS idx_shift_status         ON shift (shift_status);

CREATE INDEX IF NOT EXISTS idx_assignment_shift     ON shift_assignment (shift_id);
CREATE INDEX IF NOT EXISTS idx_assignment_staff     ON shift_assignment (staff_id);
CREATE INDEX IF NOT EXISTS idx_assignment_status    ON shift_assignment (assignment_status);

-- --------------------------------------------------------------------------
-- Triggers — keep updated_at accurate for UPDATE operations (CRUD support)
-- --------------------------------------------------------------------------
CREATE TRIGGER IF NOT EXISTS trg_staff_updated_at
AFTER UPDATE ON staff
FOR EACH ROW
BEGIN
    UPDATE staff SET updated_at = datetime('now') WHERE staff_id = OLD.staff_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_shift_updated_at
AFTER UPDATE ON shift
FOR EACH ROW
BEGIN
    UPDATE shift SET updated_at = datetime('now') WHERE shift_id = OLD.shift_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_shift_assignment_updated_at
AFTER UPDATE ON shift_assignment
FOR EACH ROW
BEGIN
    UPDATE shift_assignment SET updated_at = datetime('now') WHERE assignment_id = OLD.assignment_id;
END;
