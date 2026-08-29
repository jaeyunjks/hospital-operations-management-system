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
--      patient_contacts --> Emergency contacts for a patient
--      patientAdminNotes --> Administrative notes
--      admissions --> A patient's individual admissions information 

-- -----------------------------------------------------------------------------
-- patients
CREATE TABLE patients (
    patient_id INTEGER PRIMARY KEY,

    -- Core identification information
    p_title TEXT NOT NULL,

    p_first_name TEXT NOT NULL,
    p_last_name TEXT NOT NULL,
    p_date_of_birth TEXT NOT NULL,

    p_assigned_sex TEXT NOT NULL DEFAULT 'Unassigned'
        CHECK (p_assigned_sex IN (
            'Male',
            'Female',
            'Alternate',
            'Unassigned'
        )),

        -- Residential information
    p_street_address TEXT NOT NULL,
    p_suburb TEXT NOT NULL,

    p_state TEXT NOT NULL
        CHECK (p_state IN (
            'New South Wales',
            'Victoria',
            'Queensland',
            'Western Australia',
            'South Australia',
            'Tasmania',
            'Northern Territory',
            'Australian Capital Territory'
        )),

    p_postcode TEXT NOT NULL,

        -- Contact information
    p_mobile TEXT NOT NULL,

    -- Key:
    -- 0 --> Post
    -- 1 --> Email
    -- 2 --> Text
    p_method_of_contact INTEGER
        CHECK (p_method_of_contact IN (
            0,
            1,
            2
        )),

    -- Optional identification information
    p_preferred_name TEXT,
    p_maiden_name TEXT,
    p_previous_last_name TEXT,

    p_international_visitor INTEGER NOT NULL DEFAULT 0
        CHECK (p_international_visitor IN (
            0,
            1
        )),

    p_email_address TEXT,
    p_landline TEXT,

    -- Key:
    -- 0 --> Single
    -- 1 --> Married
    -- 2 --> De-Facto
    -- 3 --> Widowed
    -- 4 --> Separated
    -- 5 --> Divorced
    p_marital_status INTEGER NOT NULL DEFAULT 0
        CHECK (p_marital_status IN (
            0,
            1,
            2,
            3,
            4,
            5
        )),

    -- Core medical information
    p_medicare_number TEXT NOT NULL,
    p_medicare_individual_reference_number TEXT NOT NULL,
    p_medicare_expiry_date TEXT NOT NULL,

        -- Does the patient have private insurance / travel insurance (in the case of an international traveller)
    p_private_insurance INTEGER NOT NULL DEFAULT 0
        CHECK (p_private_insurance IN (
            0,
            1
        )),

    -- Legal requirement to ask if a patient is of Aboriginal or Torres Strait Islander heritage.
    -- Key:
    -- 0 --> Unknown
    -- 1 --> Aboriginal
    -- 2 --> Torres Strait Islander
    -- 3 --> Both
    -- 4 --> Neither
    p_first_nations_heritage INTEGER NOT NULL DEFAULT 0
        CHECK (p_first_nations_heritage IN (
            0,
            1,
            2,
            3,
            4
        )),

    -- Does the patient require a translator or interpreter
    p_language_assistance INTEGER NOT NULL DEFAULT 0
        CHECK (p_language_assistance IN (
            0,
            1
        )),

    -- Optional medical information
    p_centrelink_number TEXT,

    -- Internal administrative information
    patient_status TEXT NOT NULL DEFAULT 'Active'
        CHECK (patient_status IN (
            'Active', 
            'Inactive', 
            'Deceased', 
            'Transferred'
        )),

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    deactivated_at TEXT
);

-- -----------------------------------------------------------------------------
-- patient_contacts
CREATE TABLE patient_contacts (
    contact_id INTEGER PRIMARY KEY,

    -- Is this the patient's primary emergency contact 
    contact_primary INTEGER NOT NULL DEFAULT 0
        CHECK (contact_address_same_as_patient IN (
            0,
            1
        )),

    contact_first_name TEXT NOT NULL,
    contact_last_name TEXT NOT NULL,
    contact_date_of_birth TEXT NOT NULL,

    contact_relationship TEXT NOT NULL,

    contact_address_same_as_patient INTEGER NOT NULL DEFAULT 0
        CHECK (contact_address_same_as_patient IN (
            0,
            1
        )),

    contact_address TEXT NOT NULL,
    contact_mobile TEXT NOT NULL,

    contact_landline TEXT,

    FOREIGN KEY (patient_id)
        REFERENCES patients (patient_id)
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