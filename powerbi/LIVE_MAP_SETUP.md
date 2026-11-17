# Power BI live check-in map (5-minute updates)

## Views

- `gold_fact_live_location_v1`: latest valid Telegram pin per SO/SS for today in Asia/Phnom_Penh.
- `gold_fact_live_location_history_v1`: every valid pin from today, including route order.
- `gold_fact_live_route_v1`: one BI-ready dataset combining sampled OSRM road
  geometry with actual, numbered Telegram check-in markers. Full OSRM geometry
  is automatically reduced to about 150 points per employee/day so Azure Maps
  can render all paths reliably.

Both reject coordinates outside Cambodia and exclude the test group.

## Connect

Use PostgreSQL `127.0.0.1:5435`, database `SA_Alert`, in **DirectQuery** mode.

Set these Power BI column properties:

| Column | Type | Category | Summarization |
|---|---|---|---|
| `latitude` | Decimal number | Latitude | Don't summarize |
| `longitude` | Decimal number | Longitude | Don't summarize |
| `point_key` | Text | Uncategorized | Don't summarize |
| `pin_at` | Date/time | Uncategorized | Don't summarize |
| `minutes_since_pin` | Whole number | Uncategorized | Don't summarize |

## Latest-point map

Use Azure Maps with `latitude` in Latitude, `longitude` in Longitude, and
`activity_status` in Legend. Add these Tooltips in this order:

`real_name`, `pin_time`, `position`, `dealer`, `group_name`, `commune`,
`district`, `province`, `minutes_since_pin`, `open_map`, `open_telegram`.

Remove `Count of real_name`, `First dealer`, `Earliest pin_at`, and `Sum of
minutes_since_pin`. Set every tooltip field to **Don't summarize**. If Power BI
still groups two points, add `point_key` to Tooltips.

## Road-following route map like the reference image

Use `gold_fact_live_route_v1` and assign:

| Azure Maps bucket | Field |
|---|---|
| Latitude | `latitude` |
| Longitude | `longitude` |
| Legend | `user_color_key` |
| Path ID | `path_id` |
| Point order | `point_order` |
| Size | `marker_size` |
| Tooltips | `checkin_label`, `real_name`, `pin_time`, `full_address`, `dealer`, `group_name`, `road_distance_km`, `route_leg`, `open_map`, `open_telegram` |

Turn on **Path layer** and **Markers layer**. Set `marker_size` to **Don't
summarize**. Route geometry has marker size `0`; actual Telegram pins have size
`8`, so only Check-in #1, #2, #3, ... appear as visible points. The first line
starts at Check-in #1 and follows the OSRM road to Check-in #2; subsequent
check-ins continue the same employee/day path. Use `real_name` as a slicer to
inspect one employee, or keep all employees and use `user_color_key` for color.

## Automatic update every 5 minutes

The bot uses `WAREHOUSE_INTERVAL_MINUTES=5`. In Power BI Desktop select the
report page, then open **Format page > Page refresh**, enable **Automatic page
refresh**, and set **5 minutes**.

For Power BI Service, configure an on-premises data gateway for PostgreSQL and
keep the semantic model in DirectQuery. The workspace capacity must allow the
chosen automatic page-refresh interval.

## Verify rows

```powershell
docker compose exec -T postgres psql -U postgres -d SA_Alert -c "SELECT real_name,pin_time,commune,district,province,latitude,longitude,activity_status FROM gold_fact_live_location_v1 ORDER BY pin_at DESC LIMIT 20;"
docker compose exec -T postgres psql -U postgres -d SA_Alert -c "SELECT real_name,pin_time,path_id,point_order,full_address FROM gold_fact_live_location_history_v1 ORDER BY pin_at DESC LIMIT 20;"
docker compose exec -T postgres psql -U postgres -d SA_Alert -c "SELECT real_name,path_id,user_color_key,point_order,latitude,longitude FROM gold_fact_live_route_v1 ORDER BY real_name,point_order LIMIT 20;"
docker compose exec -T postgres psql -U postgres -d SA_Alert -c "SELECT real_name,path_id,checkin_number,pin_time,full_address,latitude,longitude FROM gold_fact_live_route_v1 WHERE is_checkin ORDER BY real_name,checkin_number;"
```

If the latest view returns zero rows, send a Telegram Location pin from a
registered active SO/SS account in a registered production group and refresh
Power BI.
