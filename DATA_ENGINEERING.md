# SO Alert Bot — Small Data Engineering Layer

## Architecture

1. **Bronze/raw** — existing bot source tables remain unchanged; `bronze_location_all_v1` preserves every imported Location_All row and batch.
2. **Silver/clean** — `silver_location_v1` standardizes geography; `silver_so_visit_v1` produces one logical report/visit row with typed columns instead of JSON.
3. **Gold/Power BI** — PostgreSQL views expose relationship-ready Date, Employee, Location, Dealer and Group dimensions plus visit, plan, missing-report, wrong-plan and road-exception facts.
4. **Quality** — `dq_issue_v1` records errors and warnings without deleting evidence.
5. **Reconciliation** — `reconciliation_v1` checks Bronze vs Silver locations and logical visits vs Silver visits.
6. **Audit** — `etl_run_v1` records status, inserted/updated/unchanged/rejected counts and metrics.

The transformation is idempotent. It calculates a SHA-256 signature for every clean visit. A repeat run leaves unchanged rows untouched; edited source events are updated.

## Commands

Telegram global-admin commands:

- `/update_location` — reply to its prompt with `Location_All.xlsx`.
- `/warehouse_refresh` — run incremental cleaning, DQ and reconciliation.
- `/warehouse_status` — show the most recent run.

Windows commands:

```powershell
.\RUN_WAREHOUSE.bat import-location
.\RUN_WAREHOUSE.bat refresh
.\RUN_WAREHOUSE.bat full
.\RUN_WAREHOUSE.bat status
```

When `WAREHOUSE_ENABLED=true`, the bot refreshes automatically every `WAREHOUSE_INTERVAL_MINUTES` (default 5).

## Location columns

`silver_location_v1`:

- `commune_code`, `province_code`, `province_name`, `province_name_kh`
- `district_code`, `district_name`, `district_name_kh`
- `commune_name`, `commune_name_kh`, `commune_district_kh`
- `dealer`, `region`, `source_hash`, `batch`, `updated_at`

## Clean SO visit columns

`silver_so_visit_v1` (the internal clean visit layer):

- Keys/audit: `visit_id`, `source_event_id`, `chat_id`, `group_name`, `message_id`, `source_hash`, `processed_at`
- Employee: `telegram_id`, `employee_id`, `real_name`, `username`, `region`, `dealer`, `position`
- Time: `submitted_at`, `report_date`, `report_time`, `photo_time`, `delay_minutes`, `time_status`
- Outlet form: `outlet_name`, `phone`, `outlet_type`, `banner_type`, `posm`, `program`, `emergency_outlet`, `attacking_stock`, `tour_program_outlet`, `key_wholesale`, `water`, `c_cola`, `ize_pet_300ml`, `ize_pet_1500ml`, `product_display`
- GPS/geography: `latitude`, `longitude`, `actual_province`, `actual_province_kh`, `actual_district`, `actual_district_kh`, `actual_commune`, `actual_commune_kh`, `actual_commune_code`
- Validation: `plan_check`, `plan_reason`, `route_leg`, `road_distance_km`, `route_status`, `missing_reason`, `reason_status`, `remark_issue`, `gps_match`, `ocr_issue`
- Links/evidence: `open_map`, `open_image`, `open_telegram`, `raw_text`

## Data-quality rules

| Rule | Severity | Meaning |
|---|---|---|
| `DQ_EMPLOYEE_REQUIRED` | Error | Employee ID is missing |
| `DQ_GPS_REQUIRED` | Error | Logical visit has no matched GPS pin |
| `DQ_GPS_RANGE` | Error | Latitude/longitude is invalid |
| `DQ_GPS_COUNTRY` | Error | Coordinate is outside Cambodia and is quarantined |
| `DQ_LOCATION_LOOKUP` | Warning | Commune code is absent from Location_All |
| `DQ_PLAN_INCORRECT` | Warning | Actual commune does not match the plan |
| `DQ_ROUTE_UNAVAILABLE` | Warning | Driving route could not be calculated |

## Power BI sources

- `gold_dim_date_v1`
- `gold_dim_employee_v1`
- `gold_dim_location_v1`
- `gold_dim_dealer_v1`
- `gold_dim_group_v1`
- `gold_fact_plan_v1`
- `gold_fact_missing_report_v1`
- `gold_fact_user_al_v1` (durable full/morning/afternoon leave history from commands, standalone AL, and Morning Talk)
- `gold_fact_wrong_plan_v1`
- `gold_fact_road_exception_v1`
- `gold_fact_live_location_v1` (latest valid pin per SO/SS for a live map)

The focused exception facts use business-friendly date and employee fields. Technical monitoring tables (`etl_run_v1`, `dq_issue_v1`, `reconciliation_v1`) are optional and should stay out of the normal business model. Use a read-only PostgreSQL account for Power BI in production.

Recommended single-direction, one-to-many relationships:

| Dimension | Dimension key | Fact tables |
|---|---|---|
| `gold_dim_date_v1` | `date_key` | Plan `date_key`; Missing `date_miss`; User AL `date_al`; Wrong Plan `date_wrong_plan`; Road Exception `date_over_10km`; Live Location `date_key` |
| `gold_dim_employee_v1` | `employee_id` | Plan, Missing, User AL, Wrong Plan, Road Exception and Live Location `employee_id` |
| `gold_dim_location_v1` | `location_key` | Plan, Wrong Plan and Road Exception `location_key` |
| `gold_dim_dealer_v1` | `dealer_key` | Plan, Missing, User AL, Wrong Plan, Road Exception and Live Location `dealer_key` |
| `gold_dim_group_v1` | `dealer_key` | Optional alternative to Dealer for group-name filtering; do not connect both dimensions to the same facts |

Never use `region`, `dealer`, `telegram_id`, or `chat_id` as the `1` side of a
relationship unless a separate dimension guarantees that key is unique. In
particular, `gold_dim_group_v1[region]` repeats once per dealer group and is not
a relationship key.
