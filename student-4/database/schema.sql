-- =====================================================================
-- Room & Bed Management — database schema
-- Student 4 (Jisoo Jung / Asher), HOMS Group 10
--
-- Owned data (HOMS Architecture v2.2, Table 17):
--   "Room & Bed Service — room state, bed allocation, occupancy,
--    shortage and release."
--
-- SQLite. Applied by init_db.py, which runs this file then seed_data.sql.
-- =====================================================================

PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS shortage_cases;
DROP TABLE IF EXISTS room_arrangements;
DROP TABLE IF EXISTS beds;
DROP TABLE IF EXISTS rooms;
DROP TABLE IF EXISTS room_types;


-- ---------------------------------------------------------------------
-- room_types
-- care_category groups every room into one of three buckets so patients
-- with similar care needs are placed together, and so the AI suggestion
-- endpoint can narrow candidates before ranking them.
-- ---------------------------------------------------------------------
CREATE TABLE room_types (
    type_id             INTEGER PRIMARY KEY,
    type_name           TEXT    NOT NULL UNIQUE,
    care_category       TEXT    NOT NULL
                        CHECK (care_category IN ('Surgical', 'Short-term', 'Long-term')),
    default_capacity    INTEGER NOT NULL CHECK (default_capacity > 0),
    requires_monitoring INTEGER NOT NULL DEFAULT 0 CHECK (requires_monitoring IN (0, 1)),
    description         TEXT
);


-- ---------------------------------------------------------------------
-- rooms
-- Soft delete: status becomes 'Out of Service'; the row is never removed,
-- so historical arrangements stay valid.
-- ---------------------------------------------------------------------
CREATE TABLE rooms (
    room_id     INTEGER PRIMARY KEY,
    room_number TEXT    NOT NULL UNIQUE,
    ward        TEXT    NOT NULL,
    floor       TEXT    NOT NULL,
    type_id     INTEGER NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'Available'
                CHECK (status IN ('Available', 'In Use', 'Cleaning', 'Out of Service')),
    notes       TEXT,
    FOREIGN KEY (type_id) REFERENCES room_types (type_id)
);


-- ---------------------------------------------------------------------
-- beds
-- A bed row is any allocatable slot, including an operating table.
--
-- 'reserved' implements the reserve -> allocate -> occupy sequence
-- required by Architecture v2.2 section 5.2 (bed shortage workflow).
-- Soft delete: status becomes 'maintenance'.
-- ---------------------------------------------------------------------
CREATE TABLE beds (
    bed_id     INTEGER PRIMARY KEY,
    room_id    INTEGER NOT NULL,
    bed_number TEXT    NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'available'
               CHECK (status IN ('available', 'reserved', 'occupied', 'maintenance')),
    UNIQUE (room_id, bed_number),
    FOREIGN KEY (room_id) REFERENCES rooms (room_id)
);


-- ---------------------------------------------------------------------
-- room_arrangements
-- Holds BOTH inpatient bed stays and operating theatre sessions,
-- distinguished by `purpose`. One table means the double-booking
-- conflict check is written once and covers both.
--
-- patient_id and admission_id refer to the Patient & Admission service
-- (Student 1). They are deliberately NOT foreign keys: that data lives
-- in a separate database microservice, so the constraint cannot and must
-- not be declared here. Architecture v2.2 Table 14 defines the current
-- allocation as Admission <-> Bed, so admission_id is the precise link;
-- it is nullable until Student 1's admission identifiers are agreed.
--
-- transferred_from_id records a patient moved between beds, so the two
-- arrangements form a traceable chain rather than two unrelated rows.
--
-- Arrangements are never deleted. They are cancelled by setting
-- status = 'Cancelled', preserving a complete audit trail.
-- ---------------------------------------------------------------------
CREATE TABLE room_arrangements (
    arrangement_id       INTEGER PRIMARY KEY,
    bed_id               INTEGER NOT NULL,
    patient_id           INTEGER NOT NULL,
    admission_id         INTEGER,
    purpose              TEXT    NOT NULL
                         CHECK (purpose IN ('Inpatient stay', 'Surgery')),
    procedure_name       TEXT,
    surgeon_name         TEXT,
    patient_requirements TEXT,
    care_category        TEXT    NOT NULL
                         CHECK (care_category IN ('Surgical', 'Short-term', 'Long-term')),
    start_time           TEXT    NOT NULL,
    end_time             TEXT,
    status               TEXT    NOT NULL DEFAULT 'Scheduled'
                         CHECK (status IN ('Scheduled', 'In Progress', 'Completed', 'Cancelled')),
    transferred_from_id  INTEGER,
    arranged_by          TEXT    NOT NULL,
    FOREIGN KEY (bed_id) REFERENCES beds (bed_id),
    FOREIGN KEY (transferred_from_id) REFERENCES room_arrangements (arrangement_id)
);


-- ---------------------------------------------------------------------
-- shortage_cases
-- Architecture v2.2 section 5.2: when no compatible bed is available,
-- Room & Bed Management opens a shortage case, records the requirement
-- and urgency, offers compatible options and stores the coordinator's
-- decision and reason. The AI may rank options; it never allocates.
-- ---------------------------------------------------------------------
CREATE TABLE shortage_cases (
    case_id                INTEGER PRIMARY KEY,
    patient_id             INTEGER NOT NULL,
    admission_id           INTEGER,
    required_care_category TEXT    NOT NULL
                           CHECK (required_care_category IN ('Surgical', 'Short-term', 'Long-term')),
    required_ward          TEXT,
    urgency                TEXT    NOT NULL DEFAULT 'Medium'
                           CHECK (urgency IN ('Low', 'Medium', 'High', 'Critical')),
    holding_location       TEXT,
    opened_at              TEXT    NOT NULL,
    resolved_at            TEXT,
    status                 TEXT    NOT NULL DEFAULT 'Open'
                           CHECK (status IN ('Open', 'Option offered', 'Resolved', 'Escalated', 'Cancelled')),
    chosen_option          TEXT,
    decision_reason        TEXT,
    resolved_bed_id        INTEGER,
    opened_by              TEXT    NOT NULL,
    decided_by             TEXT,
    FOREIGN KEY (resolved_bed_id) REFERENCES beds (bed_id)
);


-- ---------------------------------------------------------------------
-- Indexes. idx_arr_bed_time backs the double-booking conflict check.
-- ---------------------------------------------------------------------
CREATE INDEX idx_rooms_type      ON rooms (type_id);
CREATE INDEX idx_beds_room       ON beds (room_id);
CREATE INDEX idx_arr_bed_time    ON room_arrangements (bed_id, start_time, end_time);
CREATE INDEX idx_arr_status      ON room_arrangements (status);
CREATE INDEX idx_shortage_status ON shortage_cases (status, urgency);
