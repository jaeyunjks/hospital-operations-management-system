-- Seed data for the Patient & Admissions Management database
-- Creation date: 29/08/2026

-- patients --> 10 records with a mix of required and optional details.
INSERT INTO patients (
    patient_id,
    p_title,
    p_first_name,
    p_last_name,
    p_date_of_birth,
    p_assigned_sex,
    p_mobile,
    p_method_of_contact,
    p_middle_name,
    p_preferred_name,
    p_maiden_name,
    p_previous_last_name,
    p_international_visitor,
    p_email_address,
    p_landline,
    p_marital_status,
    p_first_nations_heritage,
    p_language_assistance,
    patient_status,
    created_at,
    updated_at
)
VALUES
    (1, 'Mr', 'John', 'Doe', '1980-01-15', 'Male', '0412345678', 'Email', 'Michael', 'James', NULL, NULL, 0, 'john.doe@email.com', '0298765432', 'Single', 'Neither', 0, 'Active', datetime('now'), datetime('now')),
    (2, 'Mrs', 'Sarah', 'Smith', '1985-04-22', 'Female', '0412345679', 'Text', 'Anne', NULL, NULL, 'Jones', 0, 'sarah.smith@email.com', '0298765433', 'Married', 'Unknown', 0, 'Active', datetime('now'), datetime('now')),
    (3, 'Ms', 'Priya', 'Patel', '1991-09-18', 'Female', '0412345680', 'Email', NULL, 'Pri', NULL, NULL, 1, 'priya.patel@email.com', '0298765434', 'Single', 'Neither', 1, 'Inactive', datetime('now'), datetime('now')),
    (4, 'Mr', 'Daniel', 'Brown', '1978-11-05', 'Male', '0412345681', 'Post', 'Craig', NULL, NULL, NULL, 0, 'daniel.brown@email.com', '0298765435', 'Separated', 'Aboriginal', 0, 'Active', datetime('now'), datetime('now')),
    (5, 'Dr', 'Alicia', 'Nguyen', '1968-07-13', 'Female', '0412345682', 'Email', 'Marie', 'Alicia', NULL, NULL, 1, 'alicia.nguyen@email.com', '0298765436', 'Widowed', 'Torres Strait Islander', 1, 'Transferred', datetime('now'), datetime('now')),
    (6, 'Mr', 'Lucas', 'Miller', '1993-02-27', 'Male', '0412345683', 'Text', NULL, NULL, NULL, NULL, 0, 'lucas.miller@email.com', '0298765437', 'Single', 'Unknown', 0, 'Active', datetime('now'), datetime('now')),
    (7, 'Mrs', 'Emily', 'Davis', '1989-06-11', 'Female', '0412345684', 'Email', 'Rose', 'Em', NULL, NULL, 0, NULL, '0298765438', 'Married', 'Neither', 0, 'Active', datetime('now'), datetime('now')),
    (8, 'Mr', 'Omar', 'Hassan', '1974-12-09', 'Male', '0412345685', 'Text', NULL, 'O', NULL, NULL, 1, 'omar.hassan@email.com', NULL, 'Divorced', 'Both', 1, 'Deceased', datetime('now'), datetime('now')),
    (9, 'Ms', 'Harper', 'Wilson', '2001-03-30', 'Alternate', '0412345686', 'Email', NULL, NULL, 'Harper', NULL, 0, 'harper.wilson@email.com', '0298765439', 'Single', 'Neither', 0, 'Active', datetime('now'), datetime('now')),
    (10, 'Mr', 'Ethan', 'Clark', '1982-08-19', 'Male', '0412345687', 'Post', 'James', NULL, NULL, 'White', 0, 'ethan.clark@email.com', '0298765440', 'De-Facto', 'Unknown', 1, 'Inactive', datetime('now'), datetime('now'));

-- patient_addresses --> 10 records, one primary address per patient.
INSERT INTO patient_addresses (
    address_id,
    patient_id,
    address_street,
    address_suburb,
    address_state,
    address_postcode,
    address_is_primary
)
VALUES
    (1, 1, '10 Main Street', 'Sydney', 'New South Wales', '2000', 1),
    (2, 2, '22 River Road', 'Melbourne', 'Victoria', '3000', 1),
    (3, 3, '7 Park Avenue', 'Brisbane', 'Queensland', '4000', 1),
    (4, 4, '15 Garden Lane', 'Perth', 'Western Australia', '6000', 1),
    (5, 5, '88 Harbour View', 'Adelaide', 'South Australia', '5000', 1),
    (6, 6, '12 Pine Crescent', 'Hobart', 'Tasmania', '7000', 1),
    (7, 7, '44 Valley Way', 'Darwin', 'Northern Territory', '0800', 1),
    (8, 8, '99 Capital Drive', 'Canberra', 'Australian Capital Territory', '2600', 1),
    (9, 9, '3 Coastal Drive', 'Gold Coast', 'Queensland', '4217', 1),
    (10, 10, '71 Market Street', 'Newcastle', 'New South Wales', '2300', 1);

-- patient_medical_information --> 10 records.
INSERT INTO patient_medical_information (
    insurance_id,
    patient_id,
    medicare_number,
    medicare_individual_reference_number,
    medicare_expiry_date,
    private_insurance,
    p_centrelink_number
)
VALUES
    (1, 1, '123456780', '1', '2026-12-31', 0, '123456789A'),
    (2, 2, '234567891', '2', '2027-06-30', 1, '234567890B'),
    (3, 3, '345678902', '1', '2028-01-31', 0, '345678901C'),
    (4, 4, '456789013', '3', '2026-09-30', 1, '456789012D'),
    (5, 5, '567890124', '2', '2027-08-31', 1, '567890123E'),
    (6, 6, '678901235', '1', '2028-04-30', 0, '678901234F'),
    (7, 7, '789012346', '2', '2029-11-30', 0, '789012345G'),
    (8, 8, '890123457', '1', '2027-05-31', 1, '890123456H'),
    (9, 9, '901234568', '1', '2028-02-28', 0, '901234567I'),
    (10, 10, '012345679', '3', '2029-07-31', 1, '012345678J');

-- patient_contacts --> 10 records.
INSERT INTO patient_contacts (
    contact_id,
    patient_id,
    contact_primary,
    contact_first_name,
    contact_last_name,
    contact_date_of_birth,
    contact_relationship,
    contact_address_same_as_patient,
    contact_address,
    contact_mobile,
    contact_landline,
    contact_email
)
VALUES
    (1, 1, 1, 'Jane', 'Doe', '1982-05-20', 'Spouse', 0, '10 Main Street, Sydney, New South Wales, 2000', '0412345679', '0298765433', 'jane.doe@email.com'),
    (2, 2, 1, 'Michael', 'Smith', '1983-09-12', 'Partner', 1, '22 River Road, Melbourne, Victoria, 3000', '0412345688', '0398765434', 'michael.smith@email.com'),
    (3, 3, 0, 'Ravi', 'Patel', '1988-02-14', 'Brother', 0, '7 Park Avenue, Brisbane, Queensland, 4000', '0412345689', '0734567890', 'ravi.patel@email.com'),
    (4, 4, 1, 'Laura', 'Brown', '1979-10-07', 'Sibling', 1, '15 Garden Lane, Perth, Western Australia, 6000', '0412345690', '0898765435', 'laura.brown@email.com'),
    (5, 5, 1, 'Henry', 'Nguyen', '1965-11-30', 'Son', 0, '88 Harbour View, Adelaide, South Australia, 5000', '0412345691', '0887654321', 'henry.nguyen@email.com'),
    (6, 6, 0, 'Sophia', 'Miller', '1994-01-22', 'Friend', 1, '12 Pine Crescent, Hobart, Tasmania, 7000', '0412345692', '0365432109', NULL),
    (7, 7, 1, 'Nathan', 'Davis', '1987-07-18', 'Husband', 0, '44 Valley Way, Darwin, Northern Territory, 0800', '0412345693', '0897654322', 'nathan.davis@email.com'),
    (8, 8, 1, 'Leah', 'Hassan', '1976-08-25', 'Daughter', 1, '99 Capital Drive, Canberra, Australian Capital Territory, 2600', '0412345694', '0265432198', 'leah.hassan@email.com'),
    (9, 9, 0, 'Theo', 'Wilson', '2003-04-16', 'Parent', 0, '3 Coastal Drive, Gold Coast, Queensland, 4217', '0412345695', '0754321098', 'theo.wilson@email.com'),
    (10, 10, 1, 'Emma', 'Clark', '1985-03-13', 'Partner', 0, '71 Market Street, Newcastle, New South Wales, 2300', '0412345696', '0243210987', 'emma.clark@email.com');

-- patient_admin_notes --> 10 records.
INSERT INTO patient_admin_notes (
    note_id,
    patient_id,
    note_text,
    created_at
)
VALUES
    (1, 1, 'Patient has a known allergy to penicillin.', datetime('now')),
    (2, 2, 'Follow-up appointment requested for blood pressure review.', datetime('now', '-2 days')),
    (3, 3, 'Interpreter required for Spanish consultations.', datetime('now', '-5 days')),
    (4, 4, 'Patient requested non-smoking room preference.', datetime('now', '-8 days')),
    (5, 5, 'International visitor status confirmed with passport on file.', datetime('now', '-12 days')),
    (6, 6, 'Patient prefers text message reminders.', datetime('now', '-15 days')),
    (7, 7, 'Medication list updated after pharmacy review.', datetime('now', '-20 days')),
    (8, 8, 'Advance care directive copied to patient file.', datetime('now', '-24 days')),
    (9, 9, 'Patient is accompanied by guardian during visits.', datetime('now', '-28 days')),
    (10, 10, 'Insurance details verified and copied to records.', datetime('now', '-30 days'));

-- admissions --> 10 records.
INSERT INTO admissions (
    admission_id,
    patient_id,
    admission_date,
    discharge_date,
    admission_status
)
VALUES
    (1, 1, '2026-08-01', '2026-08-05', 'Completed'),
    (2, 2, '2026-08-03', NULL, 'Active'),
    (3, 3, '2026-07-15', '2026-07-18', 'Completed'),
    (4, 4, '2026-08-12', NULL, 'Pending'),
    (5, 5, '2026-08-10', '2026-08-14', 'Completed'),
    (6, 6, '2026-08-18', NULL, 'Active'),
    (7, 7, '2026-07-28', '2026-08-02', 'Completed'),
    (8, 8, '2026-08-20', NULL, 'Pending'),
    (9, 9, '2026-08-15', '2026-08-17', 'Completed'),
    (10, 10, '2026-08-25', NULL, 'Active');