"""
Vendor cost comparison model — cloud hosting (3 vendors).
Writes model_output.json with all inputs, derived values, and recommendation.
"""

import json
from pathlib import Path

# ---------------------------------------------------------------------------
# Inputs: vendor pricing
# Source: vendor spec sheets checked 2026-03-29
# ---------------------------------------------------------------------------

# Nimbus Cloud — nimbus.io/pricing (checked 2026-03-29)
nimbus_monthly_base_usd = 120.00
nimbus_storage_cost_per_gb = 0.023
nimbus_api_cost_per_1k_requests = 0.004

# Stratus Hosting — stratus.io/plans (checked 2026-03-29)
stratus_monthly_base_usd = 85.00
stratus_storage_cost_per_gb = 0.031
stratus_api_cost_per_1k_requests = 0.007

# CumuloStack — cumulostack.com/pricing (checked 2026-03-29)
cumulostack_monthly_base_usd = 95.00
cumulostack_storage_cost_per_gb = 0.019
cumulostack_api_cost_per_1k_requests = 0.005

# ---------------------------------------------------------------------------
# Inputs: usage assumptions
# Source: assumed based on current workload estimates — revisit with infra team
# ---------------------------------------------------------------------------

assumed_storage_gb = 2_400          # assumption: projected peak storage usage
assumed_monthly_api_requests = 8_500_000  # assumption: ~8.5M requests/month at current growth

# ---------------------------------------------------------------------------
# Derived values
# ---------------------------------------------------------------------------

def monthly_cost(base, storage_per_gb, api_per_1k, storage_gb, api_requests):
    return base + (storage_per_gb * storage_gb) + (api_per_1k * (api_requests / 1_000))

nimbus_monthly = monthly_cost(
    nimbus_monthly_base_usd,
    nimbus_storage_cost_per_gb,
    nimbus_api_cost_per_1k_requests,
    assumed_storage_gb,
    assumed_monthly_api_requests,
)

stratus_monthly = monthly_cost(
    stratus_monthly_base_usd,
    stratus_storage_cost_per_gb,
    stratus_api_cost_per_1k_requests,
    assumed_storage_gb,
    assumed_monthly_api_requests,
)

cumulostack_monthly = monthly_cost(
    cumulostack_monthly_base_usd,
    cumulostack_storage_cost_per_gb,
    cumulostack_api_cost_per_1k_requests,
    assumed_storage_gb,
    assumed_monthly_api_requests,
)

nimbus_annual = nimbus_monthly * 12
stratus_annual = stratus_monthly * 12
cumulostack_annual = cumulostack_monthly * 12

most_expensive_annual = max(nimbus_annual, stratus_annual, cumulostack_annual)
nimbus_savings_vs_most_expensive = most_expensive_annual - nimbus_annual
stratus_savings_vs_most_expensive = most_expensive_annual - stratus_annual
cumulostack_savings_vs_most_expensive = most_expensive_annual - cumulostack_annual

monthly_costs = {
    "Nimbus Cloud": nimbus_monthly,
    "Stratus Hosting": stratus_monthly,
    "CumuloStack": cumulostack_monthly,
}
recommended_vendor = min(monthly_costs, key=monthly_costs.get)
recommended_monthly = monthly_costs[recommended_vendor]
recommended_annual = recommended_monthly * 12

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def fmt_usd(value):
    return f"${value:,.2f}"

output = {
    "inputs": {
        "vendors": {
            "Nimbus Cloud": {
                "monthly_base_usd": nimbus_monthly_base_usd,
                "storage_cost_per_gb": nimbus_storage_cost_per_gb,
                "api_cost_per_1k_requests": nimbus_api_cost_per_1k_requests,
                "source": "nimbus.io/pricing (checked 2026-03-29)",
            },
            "Stratus Hosting": {
                "monthly_base_usd": stratus_monthly_base_usd,
                "storage_cost_per_gb": stratus_storage_cost_per_gb,
                "api_cost_per_1k_requests": stratus_api_cost_per_1k_requests,
                "source": "stratus.io/plans (checked 2026-03-29)",
            },
            "CumuloStack": {
                "monthly_base_usd": cumulostack_monthly_base_usd,
                "storage_cost_per_gb": cumulostack_storage_cost_per_gb,
                "api_cost_per_1k_requests": cumulostack_api_cost_per_1k_requests,
                "source": "cumulostack.com/pricing (checked 2026-03-29)",
            },
        },
        "usage_assumptions": {
            "storage_gb": assumed_storage_gb,
            "monthly_api_requests": assumed_monthly_api_requests,
            "note": "assumption — revisit with infra team",
        },
    },
    "derived": {
        "Nimbus Cloud": {
            "monthly_cost": fmt_usd(nimbus_monthly),
            "annual_cost": fmt_usd(nimbus_annual),
            "annual_savings_vs_most_expensive": fmt_usd(nimbus_savings_vs_most_expensive),
        },
        "Stratus Hosting": {
            "monthly_cost": fmt_usd(stratus_monthly),
            "annual_cost": fmt_usd(stratus_annual),
            "annual_savings_vs_most_expensive": fmt_usd(stratus_savings_vs_most_expensive),
        },
        "CumuloStack": {
            "monthly_cost": fmt_usd(cumulostack_monthly),
            "annual_cost": fmt_usd(cumulostack_annual),
            "annual_savings_vs_most_expensive": fmt_usd(cumulostack_savings_vs_most_expensive),
        },
    },
    "recommendation": {
        "vendor": recommended_vendor,
        "monthly_cost": fmt_usd(recommended_monthly),
        "annual_cost": fmt_usd(recommended_annual),
        "most_expensive_annual": fmt_usd(most_expensive_annual),
        "max_annual_savings": fmt_usd(most_expensive_annual - recommended_annual),
    },
}

out_path = Path(__file__).parent / "model_output.json"
out_path.write_text(json.dumps(output, indent=2))
print(f"Wrote {out_path}")
