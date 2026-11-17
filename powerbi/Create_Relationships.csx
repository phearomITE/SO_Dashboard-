// Tabular Editor Advanced Scripting: create the SO Alert Power BI star schema.
// Supports names loaded as "gold_..." or "public gold_...".
using System;
using System.Collections.Generic;
using System.Linq;

Func<string, TabularEditor.TOMWrapper.Table> FindTable = logicalName =>
{
    var table = Model.Tables.FirstOrDefault(t =>
        string.Equals(t.Name, logicalName, StringComparison.OrdinalIgnoreCase));
    if (table != null) return table;
    table = Model.Tables.FirstOrDefault(t =>
        string.Equals(t.Name, "public " + logicalName, StringComparison.OrdinalIgnoreCase));
    if (table != null) return table;
    return Model.Tables.FirstOrDefault(t =>
        t.Name.EndsWith(logicalName, StringComparison.OrdinalIgnoreCase));
};

var specs = new List<string[]> {
    new [] { "gold_dim_date_v1", "date_key", "gold_fact_plan_v1", "date_key" },
    new [] { "gold_dim_date_v1", "date_key", "gold_fact_missing_report_v1", "date_miss" },
    new [] { "gold_dim_date_v1", "date_key", "gold_fact_wrong_plan_v1", "date_wrong_plan" },
    new [] { "gold_dim_date_v1", "date_key", "gold_fact_road_exception_v1", "date_over_10km" },
    new [] { "gold_dim_date_v1", "date_key", "gold_fact_live_location_v1", "date_key" },
    new [] { "gold_dim_date_v1", "date_key", "gold_fact_live_location_history_v1", "date_key" },
    new [] { "gold_dim_date_v1", "date_key", "gold_fact_live_route_v1", "date_key" }
};
var dimensions = new [] {
    new [] { "gold_dim_employee_v1", "employee_id" },
    new [] { "gold_dim_dealer_v1", "dealer_key" },
    new [] { "gold_dim_location_v1", "location_key" }
};
var facts = new [] {
    "gold_fact_plan_v1", "gold_fact_missing_report_v1", "gold_fact_wrong_plan_v1",
    "gold_fact_road_exception_v1", "gold_fact_live_location_v1",
    "gold_fact_live_location_history_v1", "gold_fact_live_route_v1"
};

// Remove stale relationships between Gold tables. This prevents old manual,
// many-to-many, bidirectional, and fact-to-fact links from remaining in the
// model before the canonical star schema is rebuilt.
var goldRelationships = Model.Relationships
    .Where(r =>
        r.FromTable.Name.IndexOf("gold_", StringComparison.OrdinalIgnoreCase) >= 0 &&
        r.ToTable.Name.IndexOf("gold_", StringComparison.OrdinalIgnoreCase) >= 0)
    .ToList();
foreach (var oldRelationship in goldRelationships)
    oldRelationship.Delete();

foreach (var d in dimensions)
    foreach (var fact in facts)
        specs.Add(new [] { d[0], d[1], fact, d[1] });

var created = 0;
var existing = 0;
var skipped = new List<string>();
foreach (var s in specs)
{
    var oneTable = FindTable(s[0]);
    var manyTable = FindTable(s[2]);
    if (oneTable == null || manyTable == null)
    {
        skipped.Add(s[0] + " -> " + s[2] + " (table not loaded)");
        continue;
    }
    var one = oneTable.Columns.FirstOrDefault(c =>
        string.Equals(c.Name, s[1], StringComparison.OrdinalIgnoreCase));
    var many = manyTable.Columns.FirstOrDefault(c =>
        string.Equals(c.Name, s[3], StringComparison.OrdinalIgnoreCase));
    if (one == null || many == null)
    {
        skipped.Add(oneTable.Name + "[" + s[1] + "] -> " +
                    manyTable.Name + "[" + s[3] + "] (column not loaded)");
        continue;
    }
    var duplicate = Model.Relationships.Any(r =>
        (r.FromColumn == many && r.ToColumn == one) ||
        (r.FromColumn == one && r.ToColumn == many));
    if (duplicate) { existing++; continue; }

    var rel = Model.AddRelationship(many, one);
    rel.Name = oneTable.Name + "__" + manyTable.Name + "__" + s[3];
    rel.IsActive = true;
    rel.CrossFilteringBehavior = CrossFilteringBehavior.OneDirection;
    created++;
}
Info("SO Alert relationships: created=" + created +
     ", already existing=" + existing +
     ", skipped=" + skipped.Count + "." +
     (skipped.Count == 0 ? "" : "\n\nSkipped:\n" + string.Join("\n", skipped)));
