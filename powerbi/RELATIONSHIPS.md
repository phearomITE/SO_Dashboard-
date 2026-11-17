# Power BI DirectQuery model

Connect directly to PostgreSQL `127.0.0.1:5435`, database `SA_Alert`, and choose
DirectQuery. Use `DirectQuery_Tables.pq` for direct navigation queries. Do not
build Power Query Reference/Merge chains between these tables.

Delete every old relationship first. Create these active relationships with
cardinality `One to many (1:*)` and cross-filter direction `Single`:

| One side | Many side |
|---|---|
| Date `[date_key]` | Plan `[date_key]` |
| Date `[date_key]` | Missing `[date_miss]` |
| Date `[date_key]` | Wrong Plan `[date_wrong_plan]` |
| Date `[date_key]` | Road Exception `[date_over_10km]` |
| Employee `[employee_id]` | Plan, Missing, Wrong Plan, Road Exception `[employee_id]` |
| Dealer `[dealer_key]` | Plan, Missing, Wrong Plan, Road Exception `[dealer_key]` |
| Location `[location_key]` | Plan, Wrong Plan, Road Exception, Live Location, Live History, Live Route `[location_key]` |
| Date `[date_key]` | Live Location, Live History, Live Route `[date_key]` |
| Employee `[employee_id]` | Live Location, Live History, Live Route `[employee_id]` |
| Dealer `[dealer_key]` | Live Location, Live History, Live Route `[dealer_key]` |

Never relate tables using Region, Dealer name, Province, District, Commune,
Telegram ID, or Chat ID. Keep `gold_dim_group_v1` disconnected or omit it.

The database publishes `gold_powerbi_relationship_manifest_v1` as the canonical
relationship list. PostgreSQL views cannot expose physical foreign-key
constraints, so Power BI cannot auto-create these model relationships merely
from the database connection. This is a Power BI semantic-model limitation,
not a warehouse refresh error.

After loading the tables, open the model with Tabular Editor, choose **Advanced
Scripting**, open/paste `Create_Relationships.csx`, and run it once. Then save
the Power BI model. The script recognizes both connector naming styles
(`gold_...` and `public gold_...`), creates active 1:* single-direction
relationships, reports created/skipped totals, and safely ignores relationships
that already exist.

Do not directly connect one fact table to another fact table. All live tables
filter through the same Date, Employee, Dealer and Location dimensions. See
`LIVE_MAP_SETUP.md` for map grain and counting rules.
