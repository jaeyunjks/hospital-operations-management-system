-- =====================================================================
-- Seed data — Room & Bed Management
--
-- The unit specification requires a minimum of ten (10) records per
-- table. Counts: room_types 10, rooms 14, beds 25,
-- room_arrangements 16, shortage_cases 10.
--
-- The data is internally consistent. init_db.py --check enforces the
-- invariants that the API layer must also maintain.
--
-- patient_id / admission_id values are placeholders owned by the
-- Patient & Admission service (Student 1) and must be reconciled with
-- that service's records before integration.
-- =====================================================================


-- ---------------------------------------------------------------------
-- room_types — 10 types across the three care categories
-- ---------------------------------------------------------------------
INSERT INTO room_types
    (type_id, type_name, care_category, default_capacity, requires_monitoring, description)
VALUES
    (1,  'Operating Theatre',           'Surgical',   1, 1, 'Sterile theatre for scheduled and emergency procedures'),
    (2,  'Post-Operative Recovery Bay', 'Surgical',   3, 1, 'Short stay recovery immediately after surgery'),
    (3,  'Day Surgery Bay',             'Surgical',   3, 0, 'Same-day admission and discharge procedures'),
    (4,  'Emergency Observation Room',  'Short-term', 3, 1, 'Short observation for undiagnosed presentations'),
    (5,  'Intensive Care Unit',         'Short-term', 1, 1, 'Critical care with continuous monitoring'),
    (6,  'High Dependency Unit',        'Short-term', 2, 1, 'Step-down care between ICU and general ward'),
    (7,  'Isolation Room',              'Short-term', 1, 1, 'Negative pressure room for infectious patients'),
    (8,  'Single Inpatient Room',       'Long-term',  1, 0, 'Private room for extended inpatient stays'),
    (9,  'Shared Inpatient Ward',       'Long-term',  4, 0, 'Shared ward for stable long-stay patients'),
    (10, 'Rehabilitation Room',         'Long-term',  2, 0, 'Extended stay for rehabilitation programmes');


-- ---------------------------------------------------------------------
-- rooms — 14 rooms.
-- OT-01 / OT-02 / OT-03 deliberately demonstrate the three theatre
-- states the coordinator's board must show: in use, free and unusable.
-- ---------------------------------------------------------------------
INSERT INTO rooms
    (room_id, room_number, ward, floor, type_id, status, notes)
VALUES
    (1,  'OT-01',  'Surgical Suite',      'Level 2', 1,  'In Use',         'Main theatre, full anaesthetic support'),
    (2,  'OT-02',  'Surgical Suite',      'Level 2', 1,  'Available',      'Orthopaedic equipment installed'),
    (3,  'OT-03',  'Surgical Suite',      'Level 2', 1,  'Out of Service', 'Ventilation system under repair until 01/09'),
    (4,  'REC-01', 'Surgical Suite',      'Level 2', 2,  'In Use',         'Adjacent to theatres'),
    (5,  'DS-01',  'Day Surgery',         'Level 1', 3,  'Available',      'Same-day discharge unit'),
    (6,  'ED-01',  'Emergency',           'Ground',  4,  'In Use',         'Cardiac monitoring at every bay'),
    (7,  'ICU-01', 'Critical Care',       'Level 3', 5,  'In Use',         'Ventilator equipped'),
    (8,  'ICU-02', 'Critical Care',       'Level 3', 5,  'Available',      'Ventilator equipped'),
    (9,  'HDU-01', 'Critical Care',       'Level 3', 6,  'In Use',         'Step-down unit'),
    (10, 'ISO-01', 'Infectious Diseases', 'Level 3', 7,  'Cleaning',       'Deep clean after discharge, ready 16:00'),
    (11, 'W1-101', 'General Ward 1',      'Level 4', 8,  'In Use',         'Private room, ensuite'),
    (12, 'W1-102', 'General Ward 1',      'Level 4', 8,  'Available',      'Private room, ensuite'),
    (13, 'W2-201', 'General Ward 2',      'Level 5', 9,  'In Use',         'Four bed shared ward'),
    (14, 'RH-01',  'Rehabilitation',      'Level 5', 10, 'Available',      'Physiotherapy access on same floor');


-- ---------------------------------------------------------------------
-- beds — 25 allocatable slots (operating tables included).
-- ICU-02-A is 'reserved', demonstrating the reserve step of the
-- shortage workflow before the bed is occupied.
-- ---------------------------------------------------------------------
INSERT INTO beds (bed_id, room_id, bed_number, status) VALUES
    (1,  1,  'OT-01-TABLE', 'occupied'),
    (2,  2,  'OT-02-TABLE', 'available'),
    (3,  3,  'OT-03-TABLE', 'maintenance'),
    (4,  4,  'REC-01-A',    'occupied'),
    (5,  4,  'REC-01-B',    'available'),
    (6,  4,  'REC-01-C',    'available'),
    (7,  5,  'DS-01-A',     'available'),
    (8,  5,  'DS-01-B',     'available'),
    (9,  5,  'DS-01-C',     'available'),
    (10, 6,  'ED-01-A',     'occupied'),
    (11, 6,  'ED-01-B',     'available'),
    (12, 6,  'ED-01-C',     'available'),
    (13, 7,  'ICU-01-A',    'occupied'),
    (14, 8,  'ICU-02-A',    'reserved'),
    (15, 9,  'HDU-01-A',    'occupied'),
    (16, 9,  'HDU-01-B',    'available'),
    (17, 10, 'ISO-01-A',    'maintenance'),
    (18, 11, 'W1-101-A',    'occupied'),
    (19, 12, 'W1-102-A',    'available'),
    (20, 13, 'W2-201-A',    'occupied'),
    (21, 13, 'W2-201-B',    'occupied'),
    (22, 13, 'W2-201-C',    'occupied'),
    (23, 13, 'W2-201-D',    'available'),
    (24, 14, 'RH-01-A',     'available'),
    (25, 14, 'RH-01-B',     'available');


-- ---------------------------------------------------------------------
-- room_arrangements — 16 records.
-- Covers both purposes and all four statuses, plus one transfer chain
-- (15 -> 16), so every UI state can be demonstrated from seed data.
-- ---------------------------------------------------------------------
INSERT INTO room_arrangements
    (arrangement_id, bed_id, patient_id, admission_id, purpose, procedure_name, surgeon_name,
     patient_requirements, care_category, start_time, end_time, status, transferred_from_id, arranged_by)
VALUES
    (1,  1,  1001, 5001, 'Surgery', 'Laparoscopic appendicectomy', 'Dr. Helena Ward',
         'Emergency abdominal surgery, general anaesthetic', 'Surgical',
         '2026-08-24 08:00', '2026-08-24 11:00', 'In Progress', NULL, 'r.okafor'),

    (2,  2,  1002, 5002, 'Surgery', 'Total knee replacement', 'Dr. Marcus Oyelaran',
         'Elective orthopaedic surgery, spinal anaesthetic', 'Surgical',
         '2026-08-24 13:00', '2026-08-24 15:30', 'Scheduled', NULL, 'r.okafor'),

    (3,  1,  1003, 5003, 'Surgery', 'Gallbladder removal', 'Dr. Helena Ward',
         'Elective keyhole surgery, day admission', 'Surgical',
         '2026-08-25 09:00', '2026-08-25 10:30', 'Scheduled', NULL, 'r.okafor'),

    (4,  2,  1014, 5014, 'Surgery', 'Inguinal hernia repair', 'Dr. Priya Raman',
         'Elective procedure, patient postponed', 'Surgical',
         '2026-08-22 10:00', '2026-08-22 11:30', 'Cancelled', NULL, 'r.okafor'),

    (5,  4,  1004, 5004, 'Inpatient stay', NULL, NULL,
         'Post-operative monitoring for four hours', 'Surgical',
         '2026-08-24 07:00', NULL, 'In Progress', NULL, 'n.station2'),

    (6,  7,  1005, 5005, 'Inpatient stay', NULL, NULL,
         'Day surgery recovery, discharge same day', 'Surgical',
         '2026-08-23 09:00', '2026-08-23 16:00', 'Completed', NULL, 'n.station1'),

    (7,  10, 1006, 5006, 'Inpatient stay', NULL, NULL,
         'Chest pain under observation, cardiac monitoring required', 'Short-term',
         '2026-08-24 05:30', NULL, 'In Progress', NULL, 'ed.triage'),

    (8,  13, 1007, 5007, 'Inpatient stay', NULL, NULL,
         'Ventilator support, continuous monitoring', 'Short-term',
         '2026-08-22 22:15', NULL, 'In Progress', NULL, 'icu.coord'),

    (9,  15, 1008, 5008, 'Inpatient stay', NULL, NULL,
         'Step-down from ICU, hourly observations', 'Short-term',
         '2026-08-23 14:00', NULL, 'In Progress', NULL, 'icu.coord'),

    (10, 17, 1012, 5012, 'Inpatient stay', NULL, NULL,
         'Airborne infection precautions required', 'Short-term',
         '2026-08-18 08:00', '2026-08-24 06:00', 'Completed', NULL, 'n.station3'),

    (11, 18, 1009, 5009, 'Inpatient stay', NULL, NULL,
         'Private room requested, extended recovery', 'Long-term',
         '2026-08-20 11:00', NULL, 'In Progress', NULL, 'n.station4'),

    (12, 20, 1010, 5010, 'Inpatient stay', NULL, NULL,
         'Stable condition, shared ward suitable', 'Long-term',
         '2026-08-19 10:00', NULL, 'In Progress', NULL, 'n.station5'),

    (13, 21, 1011, 5011, 'Inpatient stay', NULL, NULL,
         'Stable condition, shared ward suitable', 'Long-term',
         '2026-08-21 15:45', NULL, 'In Progress', NULL, 'n.station5'),

    (14, 24, 1013, 5013, 'Inpatient stay', NULL, NULL,
         'Post-stroke rehabilitation programme', 'Long-term',
         '2026-08-10 09:00', '2026-08-23 12:00', 'Completed', NULL, 'rehab.coord'),

    -- Transfer chain: patient 1015 stepped down from HDU to a shared ward.
    (15, 16, 1015, 5015, 'Inpatient stay', NULL, NULL,
         'Initial step-down placement, hourly observations', 'Short-term',
         '2026-08-21 08:00', '2026-08-23 09:00', 'Completed', NULL, 'icu.coord'),

    (16, 22, 1015, 5015, 'Inpatient stay', NULL, NULL,
         'Transferred from HDU-01-B, condition stable', 'Long-term',
         '2026-08-23 09:00', NULL, 'In Progress', 15, 'n.station5');


-- ---------------------------------------------------------------------
-- shortage_cases — 10 records covering every status and urgency level.
-- Case 1 is the live example: an ICU bed reserved but not yet occupied.
-- ---------------------------------------------------------------------
INSERT INTO shortage_cases
    (case_id, patient_id, admission_id, required_care_category, required_ward, urgency,
     holding_location, opened_at, resolved_at, status, chosen_option, decision_reason,
     resolved_bed_id, opened_by, decided_by)
VALUES
    (1,  1016, 5016, 'Short-term', 'Critical Care', 'Critical',
         'Emergency Department bay 4', '2026-08-24 04:10', NULL, 'Option offered',
         'Reserve ICU-02-A pending transfer out', 'Only compatible ventilator bed within the hour',
         14, 'ed.triage', 's.patel'),

    (2,  1017, 5017, 'Long-term', 'General Ward 1', 'Medium',
         'Recovery bay REC-01-B', '2026-08-24 09:20', NULL, 'Open',
         NULL, NULL, NULL, 'r.okafor', NULL),

    (3,  1018, 5018, 'Short-term', 'Infectious Diseases', 'High',
         'Emergency Department side room', '2026-08-24 06:00', NULL, 'Open',
         NULL, NULL, NULL, 'ed.triage', NULL),

    (4,  1019, 5019, 'Long-term', 'General Ward 2', 'Low',
         'Day Surgery DS-01-B', '2026-08-23 13:45', '2026-08-23 16:30', 'Resolved',
         'Allocate W2-201-D in the alternative ward', 'Patient stable, shared ward clinically suitable',
         23, 'r.okafor', 's.patel'),

    (5,  1020, 5020, 'Surgical', 'Surgical Suite', 'High',
         'Pre-operative holding', '2026-08-22 07:30', '2026-08-22 08:15', 'Resolved',
         'Wait for OT-02 to finish cleaning', 'Theatre free within 45 minutes, procedure not time critical',
         2, 'r.okafor', 's.patel'),

    (6,  1021, 5021, 'Short-term', 'Critical Care', 'Critical',
         'Emergency Department resus', '2026-08-21 23:05', '2026-08-22 01:40', 'Escalated',
         'Escalate to on-call operations manager', 'No compatible bed in the hospital, transfer request raised',
         NULL, 'ed.triage', 'm.chen'),

    (7,  1022, 5022, 'Long-term', 'Rehabilitation', 'Low',
         'General Ward 2', '2026-08-20 10:15', '2026-08-20 14:00', 'Resolved',
         'Allocate RH-01-A once physiotherapy review complete', 'Rehabilitation programme start deferred by one day',
         24, 'rehab.coord', 's.patel'),

    (8,  1023, 5023, 'Short-term', 'Emergency', 'Medium',
         'Emergency Department waiting area', '2026-08-24 11:00', NULL, 'Open',
         NULL, NULL, NULL, 'ed.triage', NULL),

    (9,  1024, 5024, 'Surgical', 'Day Surgery', 'Low',
         'Reception', '2026-08-19 08:00', '2026-08-19 08:50', 'Cancelled',
         NULL, 'Procedure cancelled by the patient before allocation',
         NULL, 'r.okafor', 'r.okafor'),

    (10, 1025, 5025, 'Long-term', 'General Ward 1', 'Medium',
         'Post-operative recovery', '2026-08-18 15:30', '2026-08-18 19:10', 'Resolved',
         'Allocate W1-102-A', 'Private room released earlier than forecast',
         19, 'n.station4', 's.patel');
