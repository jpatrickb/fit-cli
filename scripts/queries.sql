-- Questions the schema has to answer. Run: sqlite3 data/fitness.db < scripts/queries.sql
.mode box
.headers on

.print '1. Progress per exercise: first vs latest working session'
WITH ranked AS (
    SELECT exercise, date, sets_by_load,
           ROW_NUMBER() OVER (PARTITION BY exercise ORDER BY date)      AS first_rn,
           ROW_NUMBER() OVER (PARTITION BY exercise ORDER BY date DESC) AS last_rn,
           COUNT(*)     OVER (PARTITION BY exercise)                    AS sessions
    FROM v_exercise_session JOIN person p ON p.id = person_id WHERE p.name = 'Sam'
)
SELECT f.exercise, f.sessions,
       f.sets_by_load AS first,
       l.sets_by_load AS latest,
       l.date                           AS latest_date
FROM ranked f JOIN ranked l ON l.exercise = f.exercise AND l.last_rn = 1
WHERE f.first_rn = 1 AND f.sessions > 1
ORDER BY f.exercise;

.print ''
.print '2. Capped: at the top of the equipment AND the top of the rep range'
SELECT exercise, date, sets_by_load, rep_max, max_load FROM v_capped;

.print ''
.print '3. e1RM trend, DB Bench Press: seven formulas, per dumbbell (sets over 12 reps excluded)'
SELECT date, ROUND(epley,1) AS epley, ROUND(brzycki,1) AS brzycki, ROUND(lombardi,1) AS lombardi,
       ROUND(oconner,1) AS oconner, ROUND(mayhew,1) AS mayhew, ROUND(wathan,1) AS wathan,
       ROUND(lander,1) AS lander, ROUND(low,1) || '–' || ROUND(high,1) AS spread
FROM v_e1rm_session WHERE exercise = 'DB Bench Press' ORDER BY date;

.print ''
.print '4. Weekly sets and volume per muscle (primary = 1, secondary = 0.5)'
SELECT week, muscle, sets, CAST(volume_lb AS INT) AS volume_lb FROM v_weekly_muscle
JOIN person p ON p.id = person_id WHERE p.name = 'Sam' AND week = '2026-W12'  -- Mar 16–22
ORDER BY sets DESC;

.print ''
.print '5. Plan vs actual: sessions where the logged exercise had no current prescription'
SELECT s.date, d.label AS day, e.name AS exercise
FROM session_exercise se
JOIN session s     ON s.id = se.session_id
JOIN exercise e    ON e.id = se.exercise_id
LEFT JOIN program_day d ON d.id = s.program_day_id
WHERE s.kind = 'strength' AND se.prescription_id IS NULL
ORDER BY s.date;

.print ''
.print '6. Symptom log: every symptom, across lifting and running'
SELECT s.date, s.kind AS session, COALESCE(e.name, '(whole session)') AS exercise,
       sy.side, sy.kind_id AS kind, sy.severity
FROM symptom sy
JOIN session s ON s.id = sy.session_id
LEFT JOIN session_exercise se ON se.id = sy.session_exercise_id
LEFT JOIN exercise e ON e.id = se.exercise_id
WHERE sy.deleted_at IS NULL
ORDER BY s.date;

.print ''
.print '7. Block 2, day A: the plan as it stands'
SELECT pr.position AS '#', e.name AS exercise, pr.sets,
       COALESCE(pr.rep_min || '–' || pr.rep_max, 'AMRAP') AS reps,
       pr.start_load AS start, pr.tempo
FROM prescription pr
JOIN program_day d ON d.id = pr.program_day_id
JOIN program pg    ON pg.id = d.program_id
JOIN person p      ON p.id = pg.person_id
JOIN exercise e    ON e.id = pr.exercise_id
WHERE p.name = 'Sam' AND pg.name = 'Block 2' AND d.label = 'A'
  AND pr.active_from <= '2026-04-22' AND (pr.active_until IS NULL OR pr.active_until >= '2026-04-22')
ORDER BY pr.position;

.print ''
.print '8. What can I do here? Exercises whose every equipment slot is covered at the location'
SELECT e.name AS exercise,
       group_concat(DISTINCT eq.name) AS using_equipment
FROM exercise e
JOIN location l ON l.name = 'Home gym'
LEFT JOIN exercise_equipment ee ON ee.exercise_id = e.id
LEFT JOIN equipment eq ON eq.kind_id = ee.equipment_kind_id AND eq.location_id = l.id
WHERE NOT EXISTS (                       -- no slot left uncovered
    SELECT 1 FROM exercise_equipment need
    WHERE need.exercise_id = e.id
      AND NOT EXISTS (SELECT 1 FROM exercise_equipment alt
                      JOIN equipment have ON have.kind_id = alt.equipment_kind_id AND have.location_id = l.id
                      WHERE alt.exercise_id = e.id AND alt.slot = need.slot))
GROUP BY e.id
ORDER BY e.name;

.print ''
.print '9. What next? Days of the current program, the one done longest ago first'
SELECT d.label, d.name, MAX(s.date) AS last_done
FROM program pg
JOIN person p ON p.id = pg.person_id
JOIN program_day d ON d.program_id = pg.id
LEFT JOIN session s ON s.person_id = p.id AND s.deleted_at IS NULL
     AND s.program_day_id IN (SELECT d2.id FROM program_day d2 JOIN program pg2 ON pg2.id = d2.program_id
                              WHERE pg2.person_id = p.id AND d2.label = d.label)
WHERE p.name = 'Sam' AND pg.name = 'Block 1'
GROUP BY d.id
ORDER BY last_done IS NOT NULL, last_done;

.print ''
.print '10. Find exercises: hamstrings as a primary muscle, doable at the home gym'
SELECT e.name AS exercise, e.movement_pattern AS pattern, e.laterality,
       (SELECT group_concat(m2.muscle_id || CASE WHEN m2.set_credit < 1 THEN ' (½)' ELSE '' END, ', ')
          FROM exercise_muscle m2 WHERE m2.exercise_id = e.id) AS muscles
FROM exercise e
JOIN exercise_muscle em ON em.exercise_id = e.id AND em.muscle_id = 'hamstrings' AND em.set_credit = 1
WHERE e.deleted_at IS NULL
  AND NOT EXISTS (                       -- every equipment slot covered at the location
    SELECT 1 FROM exercise_equipment need
    WHERE need.exercise_id = e.id
      AND NOT EXISTS (SELECT 1 FROM exercise_equipment alt
                      JOIN equipment have ON have.kind_id = alt.equipment_kind_id
                      JOIN location l ON l.id = have.location_id AND l.name = 'Home gym'
                      WHERE alt.exercise_id = e.id AND alt.slot = need.slot))
ORDER BY e.name;

.print ''
.print '11. How long has each slot had its current exercise? (for "time to switch it up")'
SELECT d.label, pr.position AS '#', e.name AS exercise, pr.active_from,
       CAST((julianday('2026-04-22') - julianday(pr.active_from)) / 7 AS INT) AS weeks
FROM prescription pr
JOIN program_day d ON d.id = pr.program_day_id
JOIN program pg ON pg.id = d.program_id AND pg.ends_on IS NULL
JOIN person p ON p.id = pg.person_id AND p.name = 'Sam'
JOIN exercise e ON e.id = pr.exercise_id
WHERE pr.active_until IS NULL
ORDER BY d.label, pr.position;
