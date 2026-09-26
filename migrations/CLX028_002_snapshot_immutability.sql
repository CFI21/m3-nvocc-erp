-- CLX-028 harden daily snapshot immutability
CREATE OR REPLACE FUNCTION prevent_management_snapshot_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'IMMUTABLE_MANAGEMENT_SNAPSHOT';
END;
$$;

DROP TRIGGER IF EXISTS management_daily_snapshots_no_update ON management_daily_snapshots;
CREATE TRIGGER management_daily_snapshots_no_update
BEFORE UPDATE ON management_daily_snapshots
FOR EACH ROW EXECUTE FUNCTION prevent_management_snapshot_mutation();

DROP TRIGGER IF EXISTS management_daily_snapshots_no_delete ON management_daily_snapshots;
CREATE TRIGGER management_daily_snapshots_no_delete
BEFORE DELETE ON management_daily_snapshots
FOR EACH ROW EXECUTE FUNCTION prevent_management_snapshot_mutation();
