-- =====================================================================
-- Seed data — Room, Bed & Operating Theatre Management
--
-- The unit specification requires a minimum of ten (10) records per
-- table. Counts below: room_types 10, rooms 14, beds 25,
-- room_arrangements 14.
--
-- The data is internally consistent: every 'occupied' bed has exactly
-- one 'In Progress' arrangement, no two active arrangements overlap on
-- the same bed, and every 'In Use' room has at least one occupied bed.
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
-- states the doctors' board must show: in use, free, and unusable.
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
-- beds — 25 allocatable slots (operating tables included)
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
    (14, 8,  'ICU-02-A',    'available'),
    (15, 9,  'HDU-01-A',    'occupied'),
    (16, 9,  'HDU-01-B',    'available'),
    (17, 10, 'ISO-01-A',    'maintenance'),
    (18, 11, 'W1-101-A',    'occupied'),
    (19, 12, 'W1-102-A',    'available'),
    (20, 13, 'W2-201-A',    'occupied'),
    (21, 13, 'W2-201-B',    'occupied'),
    (22, 13, 'W2-201-C',    'available'),
    (23, 13, 'W2-201-D',    'available'),
    (24, 14, 'RH-01-A',     'available'),
    (25, 14, 'RH-01-B',     'available');


-- ---------------------------------------------------------------------
-- room_arrangements — 14 records.
-- Covers both purposes (Surgery / Inpatient stay) and all four
-- statuses, so every UI state can be demonstrated without adding data.
--
-- patient_id values are placeholders and must be reconciled with the
-- Patient & Admission service (Student 1) before integration.
-- ---------------------------------------------------------------------
INSERT INTO room_arrangements
    (arrangement_id, bed_id, patient_id, purpose, procedure_name, surgeon_name,
     patient_requirements, care_category, start_time, end_time, status, arranged_by)
VALUES
    (1,  1,  1001, 'Surgery', 'Laparoscopic appendicectomy', 'Dr. Helena Ward',
         'Emergency abdominal surgery, general anaesthetic', 'Surgical',
         '2026-08-24 08:00', '2026-08-24 11:00', 'In Progress', 'admin.reception'),

    (2,  2,  1002, 'Surgery', 'Total knee replacement', 'Dr. Marcus Oyelaran',
         'Elective orthopaedic surgery, spinal anaesthetic', 'Surgical',
         '2026-08-24 13:00', '2026-08-24 15:30', 'Scheduled', 'admin.reception'),

    (3,  1,  1003, 'Surgery', 'Gallbladder removal', 'Dr. Helena Ward',
         'Elective keyhole surgery, day admission', 'Surgical',
         '2026-08-25 09:00', '2026-08-25 10:30', 'Scheduled', 'admin.reception'),

    (4,  2,  1014, 'Surgery', 'Inguinal hernia repair', 'Dr. Priya Raman',
         'Elective procedure, patient postponed', 'Surgical',
         '2026-08-22 10:00', '2026-08-22 11:30', 'Cancelled', 'admin.reception'),

    (5,  4,  1004, 'Inpatient stay', NULL, NULL,
         'Post-operative monitoring for four hours', 'Surgical',
         '2026-08-24 07:00', NULL, 'In Progress', 'nurse.station.2'),

    (6,  7,  1005, 'Inpatient stay', NULL, NULL,
         'Day surgery recovery, discharge same day', 'Surgical',
         '2026-08-23 09:00', '2026-08-23 16:00', 'Completed', 'nurse.station.1'),

    (7,  10, 1006, 'Inpatient stay', NULL, NULL,
         'Chest pain under observation, cardiac monitoring required', 'Short-term',
         '2026-08-24 05:30', NULL, 'In Progress', 'ed.triage'),

    (8,  13, 1007, 'Inpatient stay', NULL, NULL,
         'Ventilator support, continuous monitoring', 'Short-term',
         '2026-08-22 22:15', NULL, 'In Progress', 'icu.coordinator'),

    (9,  15, 1008, 'Inpatient stay', NULL, NULL,
         'Step-down from ICU, hourly observations', 'Short-term',
         '2026-08-23 14:00', NULL, 'In Progress', 'icu.coordinator'),

    (10, 17, 1012, 'Inpatient stay', NULL, NULL,
         'Airborne infection precautions required', 'Short-term',
         '2026-08-18 08:00', '2026-08-24 06:00', 'Completed', 'nurse.station.3'),

    (11, 18, 1009, 'Inpatient stay', NULL, NULL,
         'Private room requested, extended recovery', 'Long-term',
         '2026-08-20 11:00', NULL, 'In Progress', 'nurse.station.4'),

    (12, 20, 1010, 'Inpatient stay', NULL, NULL,
         'Stable condition, shared ward suitable', 'Long-term',
         '2026-08-19 10:00', NULL, 'In Progress', 'nurse.station.5'),

    (13, 21, 1011, 'Inpatient stay', NULL, NULL,
         'Stable condition, shared ward suitable', 'Long-term',
         '2026-08-21 15:45', NULL, 'In Progress', 'nurse.station.5'),

    (14, 24, 1013, 'Inpatient stay', NULL, NULL,
         'Post-stroke rehabilitation programme', 'Long-term',
         '2026-08-10 09:00', '2026-08-23 12:00', 'Completed', 'rehab.coordinator');
