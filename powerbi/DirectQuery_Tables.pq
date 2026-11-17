// Create one blank Power Query per table and paste the matching expression.
// Choose DirectQuery when Power BI first connects to PostgreSQL.

// gold_dim_date_v1
let Source = PostgreSQL.Database("127.0.0.1:5435", "SA_Alert", [CreateNavigationProperties=false]),
    Data = Source{[Schema="public", Item="gold_dim_date_v1"]}[Data]
in Data

// gold_dim_dealer_v1
let Source = PostgreSQL.Database("127.0.0.1:5435", "SA_Alert", [CreateNavigationProperties=false]),
    Data = Source{[Schema="public", Item="gold_dim_dealer_v1"]}[Data]
in Data

// gold_dim_employee_v1
let Source = PostgreSQL.Database("127.0.0.1:5435", "SA_Alert", [CreateNavigationProperties=false]),
    Data = Source{[Schema="public", Item="gold_dim_employee_v1"]}[Data]
in Data

// gold_dim_location_v1
let Source = PostgreSQL.Database("127.0.0.1:5435", "SA_Alert", [CreateNavigationProperties=false]),
    Data = Source{[Schema="public", Item="gold_dim_location_v1"]}[Data]
in Data

// gold_fact_plan_v1
let Source = PostgreSQL.Database("127.0.0.1:5435", "SA_Alert", [CreateNavigationProperties=false]),
    Data = Source{[Schema="public", Item="gold_fact_plan_v1"]}[Data]
in Data

// gold_fact_missing_report_v1
let Source = PostgreSQL.Database("127.0.0.1:5435", "SA_Alert", [CreateNavigationProperties=false]),
    Data = Source{[Schema="public", Item="gold_fact_missing_report_v1"]}[Data]
in Data

// gold_fact_wrong_plan_v1
let Source = PostgreSQL.Database("127.0.0.1:5435", "SA_Alert", [CreateNavigationProperties=false]),
    Data = Source{[Schema="public", Item="gold_fact_wrong_plan_v1"]}[Data]
in Data

// gold_fact_road_exception_v1
let Source = PostgreSQL.Database("127.0.0.1:5435", "SA_Alert", [CreateNavigationProperties=false]),
    Data = Source{[Schema="public", Item="gold_fact_road_exception_v1"]}[Data]
in Data

// gold_fact_live_location_v1
let Source = PostgreSQL.Database("127.0.0.1:5435", "SA_Alert", [CreateNavigationProperties=false]),
    Data = Source{[Schema="public", Item="gold_fact_live_location_v1"]}[Data]
in Data

// gold_fact_live_location_history_v1 (today's route points)
let Source = PostgreSQL.Database("127.0.0.1:5435", "SA_Alert", [CreateNavigationProperties=false]),
    Data = Source{[Schema="public", Item="gold_fact_live_location_history_v1"]}[Data]
in Data

// gold_fact_live_route_v1 (OSRM road geometry; one colored road path per user/day)
let Source = PostgreSQL.Database("127.0.0.1:5435", "SA_Alert", [CreateNavigationProperties=false]),
    Data = Source{[Schema="public", Item="gold_fact_live_route_v1"]}[Data]
in Data
