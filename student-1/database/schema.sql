-- Database schema for the Patient & Admissions Management databse
-- Creation date: 29/08/2026

-- Owned data:

-- SQLite. Applied by init_database.py, which runs this file then seed_data.sql.

PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS patients;
DROP TABLE IF EXISTS patient_admin_notes;
DROP TABLE IF EXISTS admissions;

-- Tables:
--      patients --> Core patient identity / contact information
--      patientAdminNotes --> Administrative notes
--      admissions --> A patient's individual admissions information 

-- -----------------------------------------------------------------------------
-- patients
CREATE TABLE patients (
    patient_id INTEGER PRIMARY KEY,

    p_first_name TEXT NOT NULL,
    p_last_name TEXT NOT NULL,

    p_date_of_birth TEXT,

    p_mailing_address TEXT,
    p_email_address TEXT,
    p_mobile TEXT,
    p_landline TEXT,

    patient_status TEXT NOT NULL DEFAULT 'Active'
        CHECK (patient_status IN (
            'Active', 
            'Inactive', 
            'Deceased', 
            'Transferred'
        )),

    created_at TEXT NOT NULL,
    updated_at TEXT,

    deactivated_at TEXT
);

-- -----------------------------------------------------------------------------
-- patient_admin_notes
CREATE TABLE patient_admin_notes (
    note_id INTEGER PRIMARY KEY,
    patient_id INTEGER NOT NULL,
    -- author_id INTEGER NOT NULL,

    note_text TEXT NOT NULL,

    created_at TEXT NOT NULL,

    FOREIGN KEY (patient_id) 
        REFERENCES patients (patient_id)
        ON DELETE RESTRICT
);

-- -----------------------------------------------------------------------------
-- admissions
CREATE TABLE admissions (
    admission_id INTEGER PRIMARY KEY,
    patient_id INTEGER NOT NULL,

    admission_date TEXT,
    discharge_date TEXT,

    admission_status TEXT NOT NULL DEFAULT 'Pending'
        CHECK (admission_status IN (
            'Pending', 
            'Active', 
            'Cancelled', 
            'Completed'
        )),

    FOREIGN KEY (patient_id) 
        REFERENCES patients (patient_id)
);