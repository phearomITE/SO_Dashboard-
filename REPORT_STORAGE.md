# Telegram report and location storage

PostgreSQL is the durable source of truth. Redis is a temporary cache and rate limiter.

- so_events_v1: original Telegram form/photo/pin events with unique (chat, mid), Telegram user ID, group, message ID, time and JSON data.
- silver_so_visit_v1: linked logical outlet visits. Includes raw_text, outlet_name, phone, outlet_type, banner_type, POSM, program fields, product_display, remark_issue, latitude/longitude, actual district/commune, plan and route checks, Telegram evidence links.
- silver_location_v1: administrative location master (province/district/commune).

The entire submitted caption is retained in raw_text; optional/custom fields such as DMS are retained there even where they have no dedicated Silver column. Raw Telegram events are preserved independently. A report without a matching location stays stored; latitude/longitude remain null until linkage supplies them. A pin without a matching report also remains in raw events.

Do not insert pasted examples as real production reports: they lack trusted Telegram sender/message/time and pin evidence. Back up PostgreSQL and the photo/report storage volume. No duplicate report table is necessary merely to retain these submitted forms.
