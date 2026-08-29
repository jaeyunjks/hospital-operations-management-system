-- Database schema for the Patient & Admissions Management database
-- Creation date: 29/08/2026

-- Owned data:

-- SQLite. Applied by init_database.py, which runs this file then seed_data.sql.

PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS admissions;
DROP TABLE IF EXISTS patient_admin_notes;
DROP TABLE IF EXISTS patient_contacts;
DROP TABLE IF EXISTS patient_medical_information;
DROP TABLE IF EXISTS patient_addresses;
DROP TABLE IF EXISTS patients;

-- Tables:
--      patients --> Core patient identity / contact information
--      patient_addresses --> A patient's address information
--      patient_medical_information --> A patient's insurance and medicare information
--      patient_contacts --> Emergency contacts for a patient
--      patient_admin_notes --> Administrative notes
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

        -- Contact information
    p_mobile TEXT NOT NULL,

    p_method_of_contact TEXT
        CHECK (p_method_of_contact IN (
            'Post',
            'Email',
            'Text'
        )),

    -- Optional identification information
    p_middle_name TEXT,
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

    p_marital_status TEXT NOT NULL DEFAULT 'Single'
        CHECK (p_marital_status IN (
            'Single',
            'Married',
            'De-Facto',
            'Widowed',
            'Separated',
            'Divorced'
        )),

    -- Legal requirement to ask if a patient is of Aboriginal or Torres Strait Islander heritage.
    p_first_nations_heritage TEXT NOT NULL DEFAULT 'Unknown'
        CHECK (p_first_nations_heritage IN (
            'Unknown',
            'Aboriginal',
            'Torres Strait Islander',
            'Both',
            'Neither'
        )),

    -- Does the patient require a translator or interpreter
    p_language_assistance INTEGER NOT NULL DEFAULT 0
        CHECK (p_language_assistance IN (
            0,
            1
        )),

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
-- patient_addresses
CREATE TABLE patient_addresses (
    address_id INTEGER PRIMARY KEY,
    patient_id INTEGER NOT NULL,

    address_street TEXT NOT NULL,
    address_suburb TEXT NOT NULL,
    address_state TEXT NOT NULL
        CHECK (address_state IN (
            'New South Wales',
            'Victoria',
            'Queensland',
            'Western Australia',
            'South Australia',
            'Tasmania',
            'Northern Territory',
            'Australian Capital Territory'
        )),

    address_postcode TEXT NOT NULL,

    address_is_primary INTEGER NOT NULL DEFAULT 0
        CHECK (address_is_primary IN (
            0,
            1
        )),

    FOREIGN KEY (patient_id) 
        REFERENCES patients (patient_id)
);

-- -----------------------------------------------------------------------------
-- patient_insurance
CREATE TABLE patient_medical_information (
    insurance_id INTEGER PRIMARY KEY,
    patient_id INTEGER NOT NULL,

    medicare_number TEXT NOT NULL,
    medicare_individual_reference_number TEXT NOT NULL,
    medicare_expiry_date TEXT NOT NULL,

    -- Does the patient have private insurance / travel insurance (in the case of an international traveller)
    private_insurance INTEGER NOT NULL DEFAULT 0
        CHECK (private_insurance IN (
            0,
            1
        )),

    p_centrelink_number TEXT,

    FOREIGN KEY (patient_id) 
        REFERENCES patients (patient_id)
);

-- -----------------------------------------------------------------------------
-- patient_contacts
CREATE TABLE patient_contacts (
    contact_id INTEGER PRIMARY KEY,
    patient_id INTEGER NOT NULL,

    -- Is this the patient's primary emergency contact 
    contact_primary INTEGER NOT NULL DEFAULT 0
        CHECK (contact_primary IN (
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
    contact_email TEXT,

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