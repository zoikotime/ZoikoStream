-- Read-only diagnostic: inspect every subscription for organizations named "zoiko".
-- Returns stable organization/subscription/plan IDs and preserves all historical rows.
-- Run only against an explicitly authorized local, test, or read-only database.
SELECT
    o.name AS organization_name,
    o.id AS organization_id,
    s.id AS subscription_id,
    s.plan_id,
    p.name AS plan_name,
    s.status,
    s.started_at,
    s.current_period_end,
    s.created_at,
    COUNT(*) OVER (PARTITION BY o.id) AS subscriptions_for_organization
FROM organizations AS o
JOIN subscriptions AS s ON s.org_id = o.id
LEFT JOIN plans AS p ON p.id = s.plan_id
WHERE LOWER(TRIM(o.name)) = LOWER(TRIM('zoiko'))
ORDER BY o.id, s.started_at DESC, s.created_at DESC, s.id;
