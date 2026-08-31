-- ============================================================
-- HOMS: Clinical Staff Management Microservice
-- Database: SQLite
-- Owner: Clinical Staff Management service (Doctor / Nurse / Specialist workflows)
-- ============================================================
--
-- IMPORTANT NOTE ON IDs:
-- Columns like patient_id, admission_id, doctor_id, specialist_id, and
-- nurse_id refer to records owned by OTHER microservices (Patient &
-- Admission owns patients and admissions, Staff & Shift Management owns
-- staff members). Because each service owns its own database, these are
-- NOT real foreign keys enforced by SQLite. They are just stored ID
-- numbers. Your backend should validate they exist, and that the
-- referenced admission is currently active, by calling the owning
-- service's API, not by querying another database directly.
--
-- ADMISSION VALIDATION RULES (enforced in the backend, not the schema):
--   - Creating a clinical_records or surgery_requests row is BLOCKED if
--     the referenced admission is not active.
--   - Updating an existing clinical_records row is ALLOWED even after
--     the admission becomes inactive (a doctor finishing documentation
--     after discharge), but the row is flagged via
--     updated_after_discharge so the frontend can show an audit banner.
--   - An open consultation_requests row is auto-cancelled the next time
--     it is accessed if its admission has since become inactive.
--   - care_tasks may be completed after discharge without restriction;
--     this simply closes the task out administratively.
-- ============================================================


-- ------------------------------------------------------------
-- 1. clinical_records
-- The core table. One row = one doctor's assessment of a patient,
-- scoped to a specific admission (hospital stay).
-- Everything else in this service hangs off a clinical record.
-- ------------------------------------------------------------
CREATE TABLE clinical_records (
    record_id           INTEGER PRIMARY KEY AUTOINCREMENT,

    -- References to other services (not local foreign keys)
    patient_id           INTEGER NOT NULL,   -- from Patient & Admission service
    admission_id         INTEGER NOT NULL,   -- from Patient & Admission service; scopes this record to one hospital stay
    doctor_id             INTEGER NOT NULL,   -- from Staff & Shift service (staff_id of the doctor)

    -- Clinical content
    assessment_notes     TEXT NOT NULL,      -- what the doctor observed / recorded
    diagnosis_summary    TEXT,               -- short summary of the working diagnosis
    care_plan            TEXT,               -- what should happen next

    -- Workflow state
    status                TEXT NOT NULL DEFAULT 'open'
                              CHECK (status IN ('open', 'under_review', 'closed', 'archived')),

    -- Audit flag: set to 1 the moment an update is saved against a record
    -- whose admission is no longer active. Lets the frontend show a
    -- banner rather than needing a separate audit log table.
    updated_after_discharge INTEGER NOT NULL DEFAULT 0
                              CHECK (updated_after_discharge IN (0, 1)),

    created_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Speeds up "show me all records for this patient / this admission" lookups
CREATE INDEX idx_clinical_records_patient   ON clinical_records(patient_id);
CREATE INDEX idx_clinical_records_admission ON clinical_records(admission_id);
CREATE INDEX idx_clinical_records_doctor    ON clinical_records(doctor_id);


-- ------------------------------------------------------------
-- 2. consultation_requests
-- One row = a doctor asking a specialist to review something.
-- Links back to the clinical record it came from.
-- ------------------------------------------------------------
CREATE TABLE consultation_requests (
    request_id            INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Local link (real foreign key, same database)
    clinical_record_id    INTEGER NOT NULL,

    -- References to other services
    patient_id             INTEGER NOT NULL,   -- kept here too, so you don't need a join just to filter by patient
    admission_id           INTEGER NOT NULL,   -- kept here too; also what the discharge auto-cancel check uses
    requesting_doctor_id  INTEGER NOT NULL,   -- from Staff & Shift service
    specialist_id          INTEGER NOT NULL,   -- from Staff & Shift service

    -- Consultation content
    reason_for_request    TEXT NOT NULL,      -- why the doctor is asking
    recommendation        TEXT,               -- filled in once the specialist responds

    -- Workflow state
    -- 'cancelled' covers both a doctor withdrawing a request raised in
    -- error, and the system auto-cancelling one left open after discharge.
    status                 TEXT NOT NULL DEFAULT 'requested'
                              CHECK (status IN ('requested', 'in_review', 'completed', 'cancelled')),

    requested_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at           TIMESTAMP,          -- NULL until the specialist finishes

    FOREIGN KEY (clinical_record_id) REFERENCES clinical_records(record_id)
);

CREATE INDEX idx_consultation_requests_record     ON consultation_requests(clinical_record_id);
CREATE INDEX idx_consultation_requests_specialist ON consultation_requests(specialist_id);
CREATE INDEX idx_consultation_requests_admission  ON consultation_requests(admission_id);
-- Added on review: patient_id and requesting_doctor_id existed as columns
-- but had no index. Both are realistic filter columns ("this patient's
-- consultation history", "this doctor's open requests"), same pattern as
-- the indexing already applied to clinical_records above.
CREATE INDEX idx_consultation_requests_patient    ON consultation_requests(patient_id);
CREATE INDEX idx_consultation_requests_doctor     ON consultation_requests(requesting_doctor_id);


-- ------------------------------------------------------------
-- 3. care_tasks
-- One row = a nursing task tied to a clinical record.
-- Covers the "Nurse acknowledges tasks and updates care status" workflow.
-- Reaches patient/admission context through clinical_record_id, so no
-- need to duplicate those IDs here directly. Completing a task after
-- discharge is allowed without restriction (it simply closes the task
-- out administratively), so no discharge-related flag is needed here.
-- ------------------------------------------------------------
CREATE TABLE care_tasks (
    task_id                INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Local link (real foreign key, same database)
    clinical_record_id     INTEGER NOT NULL,

    -- Reference to other service
    assigned_nurse_id      INTEGER NOT NULL,   -- from Staff & Shift service

    -- Task content
    task_description       TEXT NOT NULL,      -- e.g. "Check vitals every 4 hours"
    notes                  TEXT,               -- nurse's own notes/updates

    -- Workflow state
    status                  TEXT NOT NULL DEFAULT 'pending'
                              CHECK (status IN ('pending', 'acknowledged', 'completed', 'cancelled')),

    due_at                  TIMESTAMP,          -- when the task should be done by
    completed_at            TIMESTAMP,          -- when it actually was

    FOREIGN KEY (clinical_record_id) REFERENCES clinical_records(record_id)
);

CREATE INDEX idx_care_tasks_record ON care_tasks(clinical_record_id);
CREATE INDEX idx_care_tasks_nurse  ON care_tasks(assigned_nurse_id);


-- ------------------------------------------------------------
-- 4. surgery_requests
-- One row = a doctor scheduling a surgery for an admitted patient.
-- Creating a row here is what dispatches the request to Room & Bed
-- Management (theatre prep) and Pharmacy & Medication Inventory
-- Management (surgery kit prep) at the same time. Blocked outright if
-- the admission is not active; this table only tracks that the request
-- was made, downstream preparation status lives in those other
-- services' own databases.
-- ------------------------------------------------------------
CREATE TABLE surgery_requests (
    request_id             INTEGER PRIMARY KEY AUTOINCREMENT,

    -- References to other services
    patient_id              INTEGER NOT NULL,   -- from Patient & Admission service
    admission_id            INTEGER NOT NULL,   -- from Patient & Admission service; the stay this surgery belongs to
    doctor_id                INTEGER NOT NULL,   -- from Staff & Shift service; doctor who scheduled it

    -- Surgery content
    procedure_type           TEXT NOT NULL,      -- e.g. "Appendectomy"
    scheduled_at              TIMESTAMP NOT NULL, -- date and time of the procedure

    -- Workflow state
    status                    TEXT NOT NULL DEFAULT 'scheduled'
                                 CHECK (status IN ('scheduled', 'completed', 'cancelled')),

    created_at                 TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_surgery_requests_patient   ON surgery_requests(patient_id);
CREATE INDEX idx_surgery_requests_admission ON surgery_requests(admission_id);
CREATE INDEX idx_surgery_requests_doctor    ON surgery_requests(doctor_id);


-- ------------------------------------------------------------
-- 5. ai_summaries
-- One row = one AI-generated summary of a patient's CURRENT ADMISSION.
-- This is your audit trail: every AI output gets logged here, along
-- with what a human did with it. Ties into the "AI suggests, human
-- decides" rule that applies across the whole HOMS project.
--
-- Linked by admission_id rather than clinical_record_id: a summary
-- is generated from every clinical record belonging to one admission,
-- not from a single record, so it doesn't correctly belong to just one
-- of them. There is deliberately no cross-admission summary; a patient
-- with several past admissions only ever gets summarised for the one
-- they are currently in.
-- ------------------------------------------------------------
CREATE TABLE ai_summaries (
    summary_id              INTEGER PRIMARY KEY AUTOINCREMENT,

    -- References to other services (not local foreign keys)
    admission_id             INTEGER NOT NULL,   -- from Patient & Admission service; the admission this summary covers
    patient_id                INTEGER NOT NULL,   -- kept here too, for convenient display without an extra lookup

    -- What the AI produced
    summary_text              TEXT NOT NULL,      -- the actual generated summary/guidance
    model_used                 TEXT,               -- e.g. 'qwen2.5:0.5b'
    source_reference           TEXT,               -- which policy doc it drew from (used from Release 1 onward, NULL in R0)

    generated_at                TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- Human review (this is the "human authority" control from the architecture doc)
    reviewed_by_staff_id      INTEGER,            -- from Staff & Shift service, NULL until someone reviews it
    review_status               TEXT NOT NULL DEFAULT 'pending'
                                  CHECK (review_status IN ('pending', 'accepted', 'edited', 'rejected'))
);

CREATE INDEX idx_ai_summaries_admission ON ai_summaries(admission_id);
-- Added on review: patient_id existed as a column but had no index,
-- even though the comment above notes it's kept specifically "for
-- convenient display" — which implies it gets queried, not just stored.
CREATE INDEX idx_ai_summaries_patient   ON ai_summaries(patient_id);
