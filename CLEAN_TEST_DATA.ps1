param(
    [switch]$ConfirmCleanup
)

$ErrorActionPreference = "Stop"

if (-not $ConfirmCleanup) {
    Write-Host "No data changed. Re-run with: .\CLEAN_TEST_DATA.ps1 -ConfirmCleanup"
    exit 1
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backup = "SA_Alert_before_test_cleanup_$stamp.dump"
Write-Host "Creating backup: $backup"
docker compose exec -T postgres pg_dump -U postgres -d SA_Alert -Fc > $backup
if ($LASTEXITCODE -ne 0) { throw "Database backup failed; cleanup cancelled." }

docker compose stop so-bot
if ($LASTEXITCODE -ne 0) { throw "Could not stop so-bot; cleanup cancelled." }

$sql = @'
BEGIN;
CREATE TEMP TABLE test_chats AS
SELECT id FROM so_groups_v1
WHERE lower(trim(title)) = 'test_so' OR upper(trim(code)) LIKE 'R9-%'
UNION SELECT chat_id FROM silver_so_visit_v1 WHERE lower(trim(group_name)) = 'test_so'
UNION SELECT -1004303041907;

DELETE FROM dq_issue_v1
WHERE entity_id IN (
    SELECT id::text FROM so_events_v1 WHERE chat IN (SELECT id FROM test_chats)
    UNION SELECT source_event_id::text FROM silver_so_visit_v1 WHERE chat_id IN (SELECT id FROM test_chats)
);
DELETE FROM so_missing_v1 WHERE chat IN (SELECT id FROM test_chats);
DELETE FROM so_events_v1 WHERE chat IN (SELECT id FROM test_chats);
DELETE FROM silver_so_visit_v1 WHERE chat_id IN (SELECT id FROM test_chats);
DELETE FROM so_states_v1
WHERE EXISTS (
    SELECT 1 FROM test_chats t
    WHERE so_states_v1.key LIKE 'checked:' || t.id || ':%'
       OR so_states_v1.key LIKE 'excluded:' || t.id || ':%'
       OR so_states_v1.key LIKE 'leave:' || t.id || ':%'
       OR so_states_v1.key LIKE 'new_member_alert:' || t.id || ':%'
);
DELETE FROM so_groups_v1 WHERE id IN (SELECT id FROM test_chats);
DELETE FROM so_controllers_v1 WHERE upper(trim(code))='R9' OR upper(trim(code)) LIKE 'R9-%';
COMMIT;
'@

$sql | docker compose exec -T postgres psql -U postgres -d SA_Alert -v ON_ERROR_STOP=1
if ($LASTEXITCODE -ne 0) {
    docker compose start so-bot
    throw "Cleanup failed. The SQL transaction was rolled back. Backup: $backup"
}

docker compose up -d --build so-bot
docker compose exec -T so-bot python -m app.warehouse_cli full
docker compose restart so-bot

Write-Host "Test_SO/R9 data removed. Backup: $backup"
Write-Host "Refresh Power BI to clear its imported cache."
