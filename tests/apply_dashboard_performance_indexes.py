from sqlalchemy import text
from app.db import new_engine
engine=new_engine()
from time import perf_counter
statements=[
 'CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_gp_perf_visit_employee_date ON silver_so_visit_v1 (employee_id,report_date)',
 "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_gp_perf_pin_uid_time ON so_events_v1 (uid,at DESC,mid DESC) WHERE kind='pin'",
 "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_gp_perf_pin_time_uid ON so_events_v1 (at,uid) WHERE kind='pin'",
 'CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_gp_perf_leave_person_range ON so_leave_v1 (employee,uid,start,"end")',
 "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_gp_perf_reason_date ON so_states_v1 ((data->>'date')) WHERE key LIKE 'reason_review:%'"
]
with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as con:
 con.execute(text("SET statement_timeout='30s'"))
 con.execute(text("SET lock_timeout='1s'"))
 for sql in statements:
  name=sql.split('EXISTS ')[1].split(' ON ')[0]
  valid=con.execute(text("SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid WHERE c.relname=:name"),{'name':name}).scalar()
  start=perf_counter()
  if valid is False:con.execute(text('REINDEX INDEX '+name))
  elif valid is None:con.execute(text(sql.replace(' CONCURRENTLY','')))
  print(name,round(perf_counter()-start,3),'s',flush=True)
