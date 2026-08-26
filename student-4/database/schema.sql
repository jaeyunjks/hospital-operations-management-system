-- =====================================================================
-- Room, Bed & Operating Theatre Management — database schema
-- Student 4 (Jisoo Jung / Asher)
--
-- SQLite. Applied by init_db.py, which runs this file then seed_data.sql.
-- =====================================================================

PRAGMA foreign_keys = ON;

-- Dropped in reverse dependency order so the script is re-runnable.
DROP TABLE IF EXISTS room_arrangements;
DROP TABLE IF EXISTS beds;
DROP TABLE IF EXISTS rooms;
DROP TABLE IF EXISTS room_types;


-- ---------------------------------------------------------------------
-- room_types
-- Lookup table. care_category groups every room into one of three
-- buckets so patients with similar care needs are placed together.
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
-- Soft delete: status is set to 'Out of Service' rather than removing
-- the row, so historical arrangements stay valid.
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
-- Soft delete: status is set to 'maintenance'.
-- ---------------------------------------------------------------------
CREATE TABLE beds (
    bed_id     INTEGER PRIMARY KEY,
    room_id    INTEGER NOT NULL,
    bed_number TEXT    NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'available'
               CHECK (status IN ('available', 'occupied', 'maintenance')),
    UNIQUE (room_id, bed_number),
    FOREIGN KEY (room_id) REFERENCES rooms (room_id)
);


-- ---------------------------------------------------------------------
-- room_arrangements
-- Holds BOTH inpatient bed stays and operating theatre sessions,
-- distinguished by `purpose`.
--
-- patient_id refers to the Patient & Admission service (Student 1). It is
-- deliberately NOT a foreign key: that table lives in a separate database
-- microservice, so the constraint cannot and must not be declared here.
--
-- Arrangements are never deleted. They are cancelled by setting
-- status = 'Cancelled', preserving a complete audit trail.
-- ---------------------------------------------------------------------
CREATE TABLE room_arrangements (
    arrangement_id       INTEGER PRIMARY KEY,
    bed_id               INTEGER NOT NULL,
    patient_id           INTEGER NOT NULL,
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
    arranged_by          TEXT    NOT NULL,
    FOREIGN KEY (bed_id) REFERENCES beds (bed_id)
);


-- ---------------------------------------------------------------------
-- Indexes
-- idx_arr_bed_time backs the double-booking conflict check.
-- ---------------------------------------------------------------------
CREATE INDEX idx_rooms_type   ON rooms (type_id);
CREATE INDEX idx_beds_room    ON beds (room_id);
CREATE INDEX idx_arr_bed_time ON room_arrangements (bed_id, start_time, end_time);
CREATE INDEX idx_arr_status   ON room_arrangements (status);
