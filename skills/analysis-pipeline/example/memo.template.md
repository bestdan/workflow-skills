# Cloud Hosting Vendor Recommendation

## Executive Summary

**Recommendation: {{recommendation.vendor}}** at {{recommendation.monthly_cost}}/month ({{recommendation.annual_cost}}/year).

Switching from the most expensive option saves up to {{recommendation.max_annual_savings}} annually under current usage assumptions.

The lead over the runner-up ({{recommendation.runner_up}}) is {{recommendation.margin_verdict}}: {{recommendation.margin_vs_runner_up}}/year, or {{recommendation.margin_vs_runner_up_pct}}. The word "{{recommendation.margin_verdict}}" is computed in `model.py` against a stated threshold, so re-pricing changes it along with the figures.

---

## Cost Comparison

| Vendor          | Monthly Cost                             | Annual Cost                             | Annual Savings vs. Most Expensive                            |
| --------------- | ---------------------------------------- | --------------------------------------- | ------------------------------------------------------------ |
| Nimbus Cloud    | {{derived.Nimbus Cloud.monthly_cost}}    | {{derived.Nimbus Cloud.annual_cost}}    | {{derived.Nimbus Cloud.annual_savings_vs_most_expensive}}    |
| Stratus Hosting | {{derived.Stratus Hosting.monthly_cost}} | {{derived.Stratus Hosting.annual_cost}} | {{derived.Stratus Hosting.annual_savings_vs_most_expensive}} |
| CumuloStack     | {{derived.CumuloStack.monthly_cost}}     | {{derived.CumuloStack.annual_cost}}     | {{derived.CumuloStack.annual_savings_vs_most_expensive}}     |

Costs computed from `model.py` using usage assumptions below. Re-run the model if usage estimates change.

---

## Rationale

{{narrative:rationale}}

---

## Assumptions and Sources

**Usage assumptions** (revisit with infra team before finalizing):

- Storage: {{inputs.usage_assumptions.storage_gb}} GB/month
- API requests: {{inputs.usage_assumptions.monthly_api_requests}} requests/month
- Decisive margin: a lead of {{inputs.reporting_thresholds.decisive_margin_pct}} or more over the runner-up

**Vendor pricing sources:**

- Nimbus Cloud: {{inputs.vendors.Nimbus Cloud.source}}
- Stratus Hosting: {{inputs.vendors.Stratus Hosting.source}}
- CumuloStack: {{inputs.vendors.CumuloStack.source}}
