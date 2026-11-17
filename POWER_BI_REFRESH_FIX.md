# Power BI refresh and automatic warehouse updates

## Why the cyclic-reference error appears

`/update_plan` is successful when Telegram reports the imported scheduled-row count and `/currect_plan` returns the uploaded workbook. The Power BI message **A cyclic reference was encountered during evaluation** is a Power Query dependency problem inside the `.pbix`; it is not a plan-import error.

The PostgreSQL Gold views do not refer to each other cyclically. Reconnect the two affected Power BI queries directly to PostgreSQL and do not make either query a Reference/Merge of the other.

For `public gold_dim_date_v1`, open **Transform data → Advanced Editor** and use:

```powerquery
let
    Source = PostgreSQL.Database("127.0.0.1:5435", "SA_Alert"),
    Data = Source{[Schema="public", Item="gold_dim_date_v1"]}[Data]
in
    Data
```

For `public gold_fact_road_exception_v1`, use:

```powerquery
let
    Source = PostgreSQL.Database("127.0.0.1:5435", "SA_Alert"),
    Data = Source{[Schema="public", Item="gold_fact_road_exception_v1"]}[Data]
in
    Data
```

If Power BI uses a separate host and port field, keep the same server value that already works for the other Gold views. Delete old `Reference`, `Merge`, or navigation steps that point one of these queries at another Power BI query. Then select **Close & Apply**.

Use one-way model relationships from dimensions (`1`) to facts (`*`). Relate `gold_dim_date_v1[date_key]` to each fact date. Do not create a fact-to-fact relationship. To remove the blocked `LocalDateTable_*` queries, turn off **File → Options and settings → Options → Current File → Data Load → Auto date/time** and use `gold_dim_date_v1` as the single date dimension.

## What now refreshes automatically

The bot runs serialized Bronze–Silver–Gold processing after successful `/update_plan`, `/update_list_so`, `/update_location`, `/sync_so_list`, `/add`, and `/change`. It also refreshes after report/photo/pin processing and after a reason is saved. A scheduled refresh remains as a safety net. The serialization lock prevents overlapping ETL runs when many of the 65 groups send data together.

The database is therefore current automatically. Power BI Desktop in **Import** mode still requires **Refresh**. Power BI Service requires an on-premises data gateway plus a scheduled refresh. DirectQuery reads the latest database data when visuals query it.

## Live pin map

Add `public.gold_fact_live_location_v1` in **DirectQuery** mode. Use Azure Maps
with `latitude` and `longitude`, put `real_name` in Legend/Tooltips, and add
`pin_at`, `activity_status`, `minutes_since_pin`, `dealer`, `province`,
`district`, `commune`, `open_map`, and `open_telegram` to Tooltips. The view
keeps only the newest valid Cambodia pin for each SO/SS. Suggested colors are
Active (0–30 minutes) green, Recent (31–120 minutes) amber, and Inactive red.

Only Telegram Location, a Google Maps URL, or an explicit decimal pair such as
`11.563299,104.912002` is accepted as GPS. Coordinates outside Cambodia are
quarantined before routing and excluded from the live-map view.

## Fix duplicate Region relationship errors

`gold_dim_group_v1` has one row per registered Telegram group. A region such as
`R1` therefore appears in several rows. Power BI must not place `region` on the
`1` side of a relationship.

In **Model view → Manage relationships**, delete every relationship that uses
`gold_dim_group_v1[region]`. Also remove relationships based only on `dealer`,
`telegram_id`, or `chat_id`.

Use these active, single-direction relationships instead:

- `gold_dim_date_v1[date_key]` (1) to each fact date column (*)
- `gold_dim_employee_v1[employee_id]` (1) to each fact `employee_id` (*)
- `gold_dim_dealer_v1[dealer_key]` (1) to each fact `dealer_key` (*)
- `gold_dim_location_v1[location_key]` (1) to `gold_fact_plan_v1[location_key]` (*)
- `gold_dim_location_v1[location_key]` (1) to `gold_fact_wrong_plan_v1[location_key]` (*)
- `gold_dim_location_v1[location_key]` (1) to `gold_fact_road_exception_v1[location_key]` (*)

Never relate a fact table by `commune`, `district`, or `province`. Those names
repeat across visits and cannot be the `1` side of a relationship. If Power BI
reports a duplicate such as `Chi kha Leu`, delete that relationship and rebuild
it with the `location_key` relationships above.

Keep `gold_dim_group_v1` disconnected when `gold_dim_dealer_v1` is used. If
group-name slicing is required, use Group instead of Dealer—not both—and connect
`gold_dim_group_v1[dealer_key]` (1) to each fact `dealer_key` (*).

