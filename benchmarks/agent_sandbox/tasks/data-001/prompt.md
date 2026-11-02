The workspace contains `data/sales.csv` with columns `date,region,product,units,unit_price`.

Produce `outputs/summary.json` with exactly this schema (all numbers as JSON numbers):

```json
{
  "total_revenue": 0.0,
  "revenue_by_region": {"<region>": 0.0},
  "top_product_by_units": "<product name>",
  "row_count": 0
}
```

- `total_revenue`: sum of `units * unit_price` over all rows.
- `revenue_by_region`: the same sum grouped by region, one key per region present in the file.
- `top_product_by_units`: product with the highest total `units` (no ties in this data).
- `row_count`: number of data rows (excluding the header).

Also write `outputs/report.md`: a short report (at least a few sentences) stating the total revenue, which region earned most, and which product sold most units.
