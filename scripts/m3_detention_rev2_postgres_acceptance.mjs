// Isolated embedded PostgreSQL migration acceptance. Requires @electric-sql/pglite.
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const {PGlite}=await import(process.env.M3_PGLITE_MODULE || '@electric-sql/pglite');
const db=new PGlite();
await db.exec(`CREATE TABLE transaction_records(id bigint PRIMARY KEY);
CREATE TABLE jobs(id bigint PRIMARY KEY);
CREATE TABLE containers(id bigint PRIMARY KEY);
CREATE TABLE md_config(id bigint PRIMARY KEY,config_key text UNIQUE NOT NULL,config_value text NOT NULL,value_type text,scope text,status text,version integer);
INSERT INTO transaction_records VALUES(1); INSERT INTO jobs VALUES(1); INSERT INTO containers VALUES(1);`);
const sql=await fs.readFile('migrations/M3_DETENTION_REV2_001_segments.sql','utf8');
await db.exec(sql);
await db.exec("UPDATE md_config SET config_value='false' WHERE config_key='DETENTION_CALCULATION_ENABLED'");
await db.exec(sql);
assert.deepEqual((await db.query('SELECT count(*)::int n FROM md_config')).rows,[{n:1}]);
assert.equal((await db.query('SELECT config_value FROM md_config')).rows[0].config_value,'false');
const columns=['segment_ref','calculation_ref','transaction_id','job_id','container_id','detention_ref','commercial_direction','stage','sequence','covered_from','covered_till','segment_days','cumulative_days','segment_amount','cumulative_amount','cumulative_base_amount','document_currency','fx_rate','fx_rate_date','base_currency','base_amount','rule_ref','tariff_version','tariff_snapshot_json','tariff_charge_code','rate_group','rate','rate_basis','free_days','calculated_at','calculated_by','calculation_hash'];
const values=['SEG-1','CALC-1',1,1,1,'DET-1','AGENT_TO_CUSTOMER','ADVANCE',1,'2026-10-01','2026-10-02',2,2,20,20,20,'USD',1,'2026-10-07','USD',20,'RULE-1',1,'{}','DET','RULE-1',10,'PER_DAY',0,'2026-10-07','test','HASH-1'];
await db.query(`INSERT INTO detention_segments(${columns.join(',')}) VALUES(${columns.map((_,i)=>'$'+(i+1)).join(',')})`,values);
for (const statement of ["UPDATE detention_segments SET segment_amount=1",'DELETE FROM detention_segments']) {
  await assert.rejects(db.exec(statement),/DETENTION_SEGMENT_IMMUTABLE/);
}
await db.exec('CREATE ROLE rev2_untrusted; GRANT USAGE ON SCHEMA public TO rev2_untrusted; GRANT SELECT,INSERT,UPDATE,DELETE ON detention_segments TO rev2_untrusted; GRANT USAGE ON SEQUENCE detention_segments_id_seq TO rev2_untrusted; SET ROLE rev2_untrusted;');
assert.equal((await db.query('SELECT count(*)::int n FROM detention_segments')).rows[0].n,0);
await assert.rejects(db.query(`INSERT INTO detention_segments(${columns.join(',')}) VALUES(${columns.map((_,i)=>'$'+(i+1)).join(',')})`,values.map((v,i)=>i===0?'SEG-2':i===8?2:i===31?'HASH-2':v)),/row-level security/);
await db.exec('RESET ROLE');
assert.equal((await db.query('SELECT count(*)::int n FROM detention_segments')).rows[0].n,1);
const version=(await db.query('SELECT version() version')).rows[0].version;
console.log(JSON.stringify({engine:version,first_application:'PASS',migration_replay:'PASS',disabled_switch_preserved:'PASS',immutable_update:'PASS',immutable_delete:'PASS',untrusted_select:'PASS',untrusted_insert:'PASS',production_target_touched:false}));
await db.close();
