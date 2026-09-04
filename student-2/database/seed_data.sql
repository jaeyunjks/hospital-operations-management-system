-- ============================================================
-- HOMS: Clinical Staff Management Microservice
-- Seed data for schema.sql (SQLite)
-- ============================================================
--
-- STAFF ID MAP (IDs owned by the Staff & Shift Management service;
-- stored here as plain integers, not enforced foreign keys):
--   doctor_id / requesting_doctor_id / reviewed_by_staff_id = 1  -> Dr Daniel Chen   (doctor)
--   specialist_id                                           = 3  -> Dr Emily Brown   (specialist)
--   assigned_nurse_id                                       = 7  -> James Wilson     (nurse, Department A)
-- A few other staff IDs appear for realism:
--   doctor_id = 2 -> Dr Priya Nair (doctor)
--   specialist_id = 4 -> Dr Omar Haddad (cardiology specialist)
--   assigned_nurse_id = 8 -> Aisha Khan (nurse, Department A)
--   reviewed_by_staff_id = 2 -> Dr Priya Nair
--
-- PATIENT / ADMISSION MAP (owned by Patient & Admission service):
--   patient_id 1 = Margaret Doyle   -> admission_id 1 (active),  admission_id 6 (discharged, earlier stay)
--   patient_id 2 = Thomas Reed      -> admission_id 2 (active)
--   patient_id 3 = Sofia Alvarez    -> admission_id 3 (active),  admission_id 7 (discharged)
--   patient_id 4 = Henry Watanabe   -> admission_id 4 (discharged - post-op documentation continues)
--   patient_id 5 = Grace Lin        -> admission_id 5 (active),  admission_id 8 (discharged)
--
-- STORY: five inpatients under Dr Chen's team. Chen assesses each, James
-- Wilson runs the nursing tasks, Emily Brown takes the specialist
-- consults, and every admission gets an AI summary that a human reviews.
-- Henry Watanabe (patient 4 / admission 4) was discharged while Dr Chen
-- was still finishing his notes -> updated_after_discharge = 1, and his
-- open consult was auto-cancelled.
-- ============================================================


-- ------------------------------------------------------------
-- 1. clinical_records
-- One doctor's assessment per patient per admission. record_id 4 is the
-- post-discharge edit (updated_after_discharge = 1).
-- ------------------------------------------------------------
INSERT INTO clinical_records
    (record_id, patient_id, admission_id, doctor_id, assessment_notes, diagnosis_summary, care_plan, status, updated_after_discharge, created_at, updated_at)
VALUES
    (1, 1, 1, 1, 'Patient presents with productive cough, fever 38.6C, reduced air entry left base. SpO2 94% on room air.', 'Community-acquired pneumonia, left lower lobe', 'IV antibiotics, chest physio, review in 48h', 'open', 0, '2026-08-24 08:15:00', '2026-08-24 08:15:00'),
    (2, 1, 6, 1, 'Prior admission: exacerbation of COPD, responded to nebulisers and oral steroids. Discharged stable.', 'COPD exacerbation, resolved', 'Discharged on tapering prednisolone, GP follow-up', 'closed', 0, '2026-06-11 09:40:00', '2026-06-15 11:00:00'),
    (3, 2, 2, 1, 'Acute onset RIF pain, guarding, rebound tenderness. WCC elevated. Nil bowel sounds.', 'Acute appendicitis', 'NBM, IV fluids, surgical referral for appendectomy', 'under_review', 0, '2026-08-26 14:05:00', '2026-08-27 07:30:00'),
    (4, 3, 3, 1, 'Type 2 diabetic admitted with foot ulcer and cellulitis to mid-calf. Pedal pulses present. Afebrile.', 'Diabetic foot infection with ascending cellulitis', 'IV antibiotics, wound debridement, vascular and endocrine input', 'open', 0, '2026-08-25 10:20:00', '2026-08-28 16:45:00'),
    (5, 3, 7, 2, 'Earlier stay for DKA precipitated by gastroenteritis. Resolved with insulin infusion and rehydration.', 'Diabetic ketoacidosis, resolved', 'Discharged on usual basal-bolus regimen, diabetes nurse follow-up', 'archived', 0, '2026-05-02 22:10:00', '2026-05-06 10:15:00'),
    (6, 4, 4, 1, 'Post-op day 1 following elective laparoscopic cholecystectomy. Wounds clean and dry. Pain controlled. Tolerating diet.', 'Cholelithiasis, post cholecystectomy', 'Simple analgesia, mobilise, remove dressing day 3, discharge when eating and mobile', 'closed', 1, '2026-08-22 11:00:00', '2026-08-29 09:30:00'),
    (7, 5, 5, 1, 'Admitted with atrial fibrillation with rapid ventricular response, HR 148, mild breathlessness. BP stable.', 'New atrial fibrillation with RVR', 'Rate control with bisoprolol, anticoagulation assessment, cardiology consult', 'open', 0, '2026-08-27 19:25:00', '2026-08-28 08:00:00'),
    (8, 5, 8, 1, 'Previous admission with vasovagal syncope after prolonged standing. Full cardiac and neuro workup unremarkable.', 'Vasovagal syncope', 'Reassurance, hydration and counter-pressure manoeuvre advice, no medication', 'closed', 0, '2026-04-14 13:30:00', '2026-04-16 09:00:00'),
    (9, 2, 2, 2, 'Second reviewer note: pre-operative anaesthetic assessment. ASA II, no airway concerns, fasted from midnight.', 'Fit for general anaesthesia', 'Proceed to theatre as listed', 'open', 0, '2026-08-27 06:50:00', '2026-08-27 06:50:00'),
    (10, 1, 1, 1, 'Ward round day 2: fever settled, SpO2 97% on room air, cough improving. Repeat CXR shows partial resolution.', 'Community-acquired pneumonia, improving', 'Switch to oral antibiotics, plan discharge tomorrow if stable overnight', 'under_review', 0, '2026-08-26 08:30:00', '2026-08-26 08:30:00');


-- ------------------------------------------------------------
-- 2. consultation_requests
-- Dr Chen asking specialists (mostly Emily Brown, id 3) to review.
-- request_id 3 is cancelled (auto-cancelled after Henry's discharge).
-- request_id 6 is a doctor-withdrawn request raised in error.
-- ------------------------------------------------------------
INSERT INTO consultation_requests
    (request_id, clinical_record_id, patient_id, admission_id, requesting_doctor_id, specialist_id, reason_for_request, recommendation, status, requested_at, completed_at)
VALUES
    (1, 1, 1, 1, 1, 3, 'Please review chest imaging - query underlying mass given smoking history.', 'No mass seen. Consolidation consistent with infection. Repeat CXR in 6 weeks to confirm clearance.', 'completed', '2026-08-24 09:00:00', '2026-08-25 12:30:00'),
    (2, 4, 3, 3, 1, 3, 'Diabetic foot with ascending cellulitis - specialist input on debridement extent and antibiotic choice.', 'Debride to healthy tissue, continue IV flucloxacillin plus benzylpenicillin, MRI to exclude osteomyelitis.', 'completed', '2026-08-25 11:15:00', '2026-08-26 15:00:00'),
    (3, 6, 4, 4, 1, 3, 'Post-op review requested for slow return of bowel function.', NULL, 'cancelled', '2026-08-23 08:00:00', '2026-08-24 07:05:00'),
    (4, 7, 5, 5, 1, 4, 'New AF with RVR - advice on rate vs rhythm control and anticoagulation timing.', 'Continue rate control, start apixaban now (CHA2DS2-VASc 3), review for elective cardioversion in 4 weeks if still in AF.', 'completed', '2026-08-27 20:00:00', '2026-08-28 09:45:00'),
    (5, 3, 2, 2, 1, 3, 'Confirm appendicitis vs alternative intra-abdominal pathology before theatre.', NULL, 'in_review', '2026-08-26 15:30:00', NULL),
    (6, 10, 1, 1, 1, 3, 'Requested pulmonology follow-up - raised in error, patient already has one booked.', NULL, 'cancelled', '2026-08-26 09:10:00', '2026-08-26 09:40:00'),
    (7, 4, 3, 3, 1, 4, 'Vascular assessment of pedal perfusion before further debridement.', 'ABPI 0.9 bilaterally, perfusion adequate. Safe to proceed with surgical debridement.', 'completed', '2026-08-26 10:00:00', '2026-08-27 14:20:00'),
    (8, 2, 1, 6, 1, 3, 'Prior admission: query need for long-term inhaled therapy step-up.', 'Step up to LAMA/LABA combination inhaler, spirometry in 3 months.', 'completed', '2026-06-12 10:00:00', '2026-06-13 16:00:00'),
    (9, 5, 3, 7, 2, 3, 'Prior DKA admission - review for insulin pump candidacy.', 'Not currently a pump candidate, optimise MDI adherence first.', 'completed', '2026-05-03 09:00:00', '2026-05-05 11:30:00'),
    (10, 7, 5, 5, 1, 3, 'Echo requested to assess for structural heart disease underlying new AF.', NULL, 'requested', '2026-08-28 08:15:00', NULL);


-- ------------------------------------------------------------
-- 3. care_tasks
-- Nursing tasks for James Wilson (id 7), some for Aisha Khan (id 8).
-- task_id 2 and 5 and 8 are completed.
-- ------------------------------------------------------------
INSERT INTO care_tasks
    (task_id, clinical_record_id, assigned_nurse_id, task_description, notes, status, due_at, completed_at)
VALUES
    (1, 1, 7, 'Administer IV ceftriaxone 1g and record response.', 'First dose given, no reaction.', 'completed', '2026-08-24 10:00:00', '2026-08-24 10:05:00'),
    (2, 1, 7, 'Record vital signs every 4 hours.', 'Ongoing - obs stable overnight, fever trending down.', 'acknowledged', '2026-08-24 12:00:00', NULL),
    (3, 10, 7, 'Switch antibiotic administration to oral and confirm patient tolerating.', NULL, 'pending', '2026-08-26 12:00:00', NULL),
    (4, 3, 8, 'Keep patient nil by mouth and maintain IV fluids pre-theatre.', 'NBM signage in place, 1L Hartmann running.', 'acknowledged', '2026-08-26 16:00:00', NULL),
    (5, 6, 7, 'Remove abdominal dressing on post-op day 3 and inspect wounds.', 'Wounds clean and dry, no signs of infection. Left exposed.', 'completed', '2026-08-25 09:00:00', '2026-08-25 09:20:00'),
    (6, 6, 7, 'Encourage mobilisation three times daily and document distance.', 'Task closed administratively after discharge.', 'completed', '2026-08-24 09:00:00', '2026-08-29 10:00:00'),
    (7, 4, 7, 'Perform wound dressing change with saline and record ulcer measurements.', 'Ulcer 3cm x 2cm, granulating base, minimal exudate.', 'acknowledged', '2026-08-26 08:00:00', NULL),
    (8, 4, 8, 'Check capillary blood glucose before meals and at bedtime.', 'Readings 6-9 mmol/L range, no hypos.', 'completed', '2026-08-25 07:00:00', '2026-08-28 21:00:00'),
    (9, 7, 7, 'Continuous cardiac monitoring and report HR above 130 or below 50.', 'On telemetry, HR settled to 90s after bisoprolol.', 'acknowledged', '2026-08-27 20:00:00', NULL),
    (10, 7, 8, 'Give first dose of apixaban and provide anticoagulation counselling leaflet.', NULL, 'pending', '2026-08-28 10:00:00', NULL),
    (11, 2, 7, 'Prior admission: administer nebulised salbutamol and ipratropium 6-hourly.', 'Completed for duration of previous stay.', 'completed', '2026-06-11 12:00:00', '2026-06-14 18:00:00'),
    (12, 3, 7, 'Complete pre-operative checklist and confirm consent form signed.', 'Checklist complete, consent signed and witnessed.', 'completed', '2026-08-27 06:00:00', '2026-08-27 06:30:00');


-- ------------------------------------------------------------
-- 4. surgery_requests
-- Surgeries scheduled by Dr Chen (id 1) for active admissions;
-- historic ones from earlier stays are completed or cancelled.
-- ------------------------------------------------------------
--
-- BED / THEATRE ID MAP (owned by Room & Bed Management service; stored
-- here as plain integers, looked up for theatre availability at creation
-- time). bed_id is NULL where no theatre slot was available when the
-- request was raised (request_id 2), or where the row predates the
-- Room & Bed integration (historic completed/cancelled requests).
--   bed_id 21 -> Theatre 1
--   bed_id 22 -> Theatre 2
--   bed_id 23 -> Theatre 3 (day-procedure / cath lab)
INSERT INTO surgery_requests
    (request_id, patient_id, admission_id, doctor_id, bed_id, procedure_type, scheduled_at, status, created_at)
VALUES
    (1, 2, 2, 1, 21, 'Laparoscopic appendectomy', '2026-08-27 13:00:00', 'scheduled', '2026-08-26 15:45:00'),
    (2, 3, 3, 1, 56, 'Surgical debridement of diabetic foot ulcer', '2026-08-29 09:00:00', 'scheduled', '2026-08-27 15:00:00'),
    (3, 4, 4, 1, 72, 'Laparoscopic cholecystectomy', '2026-08-21 08:30:00', 'completed', '2026-08-19 10:00:00'),
    (4, 1, 1, 1, 93, 'Bronchoscopy for airway sampling', '2026-08-25 14:00:00', 'cancelled', '2026-08-24 09:30:00'),
    (5, 5, 5, 1, 23, 'Elective DC cardioversion', '2026-09-25 08:00:00', 'scheduled', '2026-08-28 10:00:00'),
    (6, 3, 7, 2, 45, 'Central line insertion for insulin infusion', '2026-05-02 23:30:00', 'completed', '2026-05-02 22:45:00'),
    (7, 1, 6, 1, 64, 'Diagnostic pleural aspiration', '2026-06-12 11:00:00', 'completed', '2026-06-12 09:00:00'),
    (8, 5, 8, 1, 27, 'Tilt table test under monitoring', '2026-04-15 10:00:00', 'completed', '2026-04-14 16:00:00'),
    (9, 2, 2, 2, 33, 'Diagnostic laparoscopy (standby if appendicitis not confirmed)', '2026-08-27 13:00:00', 'cancelled', '2026-08-26 16:30:00'),
    (10, 3, 3, 1, 22, 'Repeat wound debridement', '2026-09-02 09:00:00', 'scheduled', '2026-08-28 17:00:00');


-- ------------------------------------------------------------
-- 5. ai_summaries
-- One AI summary per admission, each with a human review outcome.
-- review_status covers pending, accepted, edited (and rejected) as required.
-- ------------------------------------------------------------
-- summary_scope is given explicitly on every row and covers all three
-- allowed values: 'clinical' (drawn from the admission's clinical_records),
-- 'consultation' (drawn from its consultation_requests) and 'care_tasks'
-- (drawn from its care_tasks).
INSERT INTO ai_summaries
    (summary_id, admission_id, patient_id, summary_text, model_used, source_reference, summary_scope, generated_at, reviewed_by_staff_id, review_status)
VALUES
    (1, 1, 1, 'Patient admitted with left lower lobe community-acquired pneumonia. Started on IV ceftriaxone with chest physiotherapy. Fever and oxygenation improving by day 2; plan to switch to oral antibiotics and discharge if stable.', 'qwen2.5:0.5b', 'HOMS-ClinGuide-Respiratory-v2', 'clinical', '2026-08-26 09:00:00', 1, 'accepted'),
    (2, 2, 2, 'Patient with acute appendicitis awaiting laparoscopic appendectomy. Kept nil by mouth on IV fluids. Anaesthetic assessment complete, ASA II, listed for theatre.', 'qwen2.5:0.5b', 'HOMS-ClinGuide-Surgical-v2', 'clinical', '2026-08-27 07:00:00', NULL, 'pending'),
    (3, 3, 3, 'Diabetic patient admitted with foot ulcer and ascending cellulitis. On IV antibiotics with specialist and vascular input. Surgical debridement scheduled; glycaemic control stable on sliding scale.', 'qwen2.5:0.5b', 'HOMS-ClinGuide-Diabetes-v3', 'clinical', '2026-08-28 17:30:00', 1, 'edited'),
    (4, 3, 3, 'Specialist and vascular consultations for the diabetic foot: debride to healthy tissue, continue IV flucloxacillin plus benzylpenicillin, MRI to exclude osteomyelitis, perfusion confirmed adequate (ABPI 0.9) so safe to proceed to theatre.', 'qwen2.5:0.5b', 'HOMS-ClinGuide-Diabetes-v3', 'consultation', '2026-08-27 15:00:00', 1, 'accepted'),
    (5, 5, 5, 'Cardiology consultation for new atrial fibrillation with RVR: continue rate control, start apixaban now (CHA2DS2-VASc 3), review for elective cardioversion in four weeks if still in AF; echo requested to assess for structural heart disease.', 'qwen2.5:0.5b', 'HOMS-ClinGuide-Cardiology-v1', 'consultation', '2026-08-28 10:00:00', NULL, 'pending'),
    (6, 4, 4, 'Post-op review requested for slow return of bowel function was auto-cancelled after discharge; no specialist recommendation was recorded before closure.', 'qwen2.5:0.5b', 'HOMS-ClinGuide-Surgical-v2', 'consultation', '2026-08-29 09:45:00', 1, 'rejected'),
    (7, 1, 1, 'Nursing tasks for the pneumonia admission: IV ceftriaxone administered without reaction, 4-hourly vital signs ongoing with fever trending down, switch to oral antibiotics pending confirmation of tolerance.', 'qwen2.5:0.5b', 'HOMS-ClinGuide-Respiratory-v2', 'care_tasks', '2026-08-26 12:00:00', 1, 'accepted'),
    (8, 2, 2, 'Pre-operative nursing tasks for acute appendicitis complete: patient kept nil by mouth on IV Hartmann, pre-op checklist done and consent signed and witnessed, ready for theatre.', 'qwen2.5:0.5b', 'HOMS-ClinGuide-Surgical-v2', 'care_tasks', '2026-08-27 07:00:00', NULL, 'pending'),
    (9, 5, 5, 'Nursing tasks for new AF: continuous cardiac monitoring in place with HR settled to 90s after bisoprolol; first apixaban dose and anticoagulation counselling leaflet still outstanding.', 'qwen2.5:0.5b', 'HOMS-ClinGuide-Cardiology-v1', 'care_tasks', '2026-08-28 10:30:00', NULL, 'pending'),
    (10, 4, 4, 'Nursing tasks for the post-cholecystectomy stay: abdominal dressing removed day 3 with clean dry wounds, mobilisation task closed administratively after discharge.', 'qwen2.5:0.5b', 'HOMS-ClinGuide-Surgical-v2', 'care_tasks', '2026-08-29 09:30:00', 1, 'edited');
