---
name: analysis-pipeline
description: Reproducible analysis pipelines — separation of narrative and computation, input provenance, document structure, template-driven reports. Use when building analyses where numbers feed into decisions, reports, or documents.
user-invocable: true
---

# Auditable Pipeline

**Note**: Project-level preferences (CLAUDE.md, AGENTS.md) override these.

## When This Applies

Structured analysis where numbers feed into decisions, reports, or ongoing models. Examples: financial projections, cost comparisons, capacity planning, scenario analysis.

Does NOT apply to quick calculations or exploratory conversation. If the user asks "what's 20% of $500k?" just answer.

Rule of thumb: if the analysis has more than ~3 variable inputs or produces a document, use these conventions.

## Canonical Example

`example/` contains the reference implementation. **Copy its structure when building new pipelines.** Specifically:

- Copy `fill_templates.py` and adapt it (the placeholder resolution logic is reusable as-is)
- Follow `model.py`'s pattern: inputs with source comments at top, derived values in the middle, structured JSON output at the bottom
- Follow `memo.template.md`'s pattern: executive summary first, data tables with `{{dotted.key}}` placeholders, `{{narrative:*}}` for prose sections
- Follow `model_output.json`'s structure: top-level `inputs` and `derived` sections, pre-formatted display values

Do NOT search the repo for other implementations to use as reference. This example and the spec below are the complete specification. If the example doesn't cover a case, ask.

## Pipeline Stages

The core principle: **every number in a final document must trace to either an input or a computation in the model — never to a one-time Claude response.** Re-running the pipeline should regenerate everything from current inputs.

For simple analyses, this can be as lightweight as a single script that prints results. For larger projects with narrative reports, a multi-stage pipeline works well:

1. **Model** (notebook or script) computes all values and writes structured output (e.g., `model_report.json`)
2. **Templates** (`.template.md`) contain `{{key}}` placeholders for data
3. **Fill script** deterministically replaces placeholders from the model output
4. **Narrate** (optional) pipes enriched templates through Claude CLI to fill prose sections

Scale the pipeline to the complexity of the analysis. A one-page cost comparison doesn't need four stages.

## Directory Layout

Every pipeline lives in a single directory. Use this structure:

```
analysis-name/
  model.py                  # computation — inputs, derived values, JSON output
  model_output.json         # committed output of model.py
  memo.template.md          # document with {{key}} and {{narrative:*}} placeholders
  fill_templates.py         # deterministic placeholder replacement (copy from example/)
  memo.filled.md            # committed output of fill_templates.py
  inputs/                   # (optional) source data files the model reads
```

Naming: use `model.py` and `model_output.json` unless the pipeline has multiple models, in which case name them descriptively (e.g., `cost_model.py`, `cost_model_output.json`).

## When to Use Plain Scripts vs Marimo

- **Plain `.py` script** (default for pipelines): deterministic pipeline steps, one-shot computations, anything that writes JSON output for the template fill step.
- **Marimo notebook**: exploratory analysis, interactive parameter tweaking, work that benefits from visible intermediate outputs. Not the default for pipelines — use when the user asks for interactivity or when the analysis is exploratory rather than producing a report.

When building a pipeline that produces a document, always use a plain script for the model step.

## JSON Output Schema

The model's JSON output should follow this structure:

```json
{
  "inputs": {
    "section_name": {
      "field": "value",
      "source": "where this came from (URL, date checked)"
    }
  },
  "derived": {
    "section_name": {
      "field": "$1,234.56"
    }
  }
}
```

- Pre-format display values (currency, percentages, dates) in the JSON so the fill step is pure string substitution.
- Use descriptive section names that match the template's placeholder paths (e.g., `derived.vendor_a.monthly_cost` maps to `{{derived.vendor_a.monthly_cost}}`).
- Add additional top-level keys as needed (e.g., `recommendation`, `sensitivity`) — the fill script handles arbitrary nesting.

## Input Provenance

Every value in an analysis is either an **input** or **derived**. Keep this distinction explicit.

- **Inputs** MUST document their source (bill, spec sheet, calculator, regulation, assumption). Label them clearly in both markdown and code.
- **Derived values** MUST be computed from inputs, never hardcoded after a one-time calculation. Use functions or lazy properties so they recalculate when inputs change.

If an input is an assumption rather than a measured/sourced fact, say so — e.g., "assumed 5% annual rate increase (no source, revisit)."

External parameters MUST include a source link or note (vendor URL, spec sheet, date checked) in code comments. Inputs without sources should be flagged as assumptions.

## Separation of Concerns

MUST NOT derive numbers in markdown files. Markdown is for narrative, decisions, qualitative analysis, system descriptions, assumptions, and open questions.

MUST keep all derived numbers (calculations, savings, sensitivity) in a notebook or script. Markdown may show summary tables with max/realistic ranges but MUST reference the model for derivation.

## Document Structure

When an analysis produces a narrative document (memo, recommendation, report):

- **Executive summary first** with key numbers and outstanding decisions.
- **Qualitative content** (why, how, what) in the markdown body.
- **Data tables** rendered from model data via templates (not hand-written).
- **Financial analysis sections** are brief: summary table with max/realistic, then a link to the model.
- **Sources** as footnoted references in an appendix.

Bad (numbers derived in markdown):
> The system produces 817 kWh/month, saving $142/month.

Good (reference to model):
> See [model.py] for production estimates. Summary: 750-820 kWh/month,
> $128-$145/month savings (sensitivity table in model).

Bad (structure buries the conclusion):
> We analyzed three vendors across 12 dimensions... [3 pages] ...Vendor B is recommended.

Good (lead with the decision):
> **Recommendation: Vendor B.** Best cost/reliability tradeoff at $X/mo.
> See comparison model for full breakdown across 12 dimensions.

## Template-Driven Reports

When an analysis produces a narrative document, use templates to keep numbers tied to the model.

- **`.template.md` files** contain the document structure with two kinds of placeholders:
  - `{{key}}` — **data placeholders**, filled deterministically from the model's JSON output. These are reproducible: re-run the model, re-fill, get the same numbers.
  - `{{narrative:section_name}}` — **prose placeholders**, filled by piping the enriched template through Claude CLI. These are regenerable but not deterministic — the data they reference is pinned, but the wording may vary.

- **Fill step**: a script (e.g., `fill_templates.py`, a `jq` one-liner, whatever fits) reads `model_report.json` and replaces `{{key}}` placeholders. This step has no LLM involvement and should be trivially verifiable.

- **Narrate step** (optional): passes the data-filled template to Claude CLI to expand `{{narrative:*}}` blocks into prose. The LLM sees the real numbers in context, so the narrative stays consistent with the model.

This separation matters because data fill is deterministic (same inputs = same output) while narrative fill is not. Keeping them as distinct steps means you can re-run just the narrative without re-running the model, or update the model and verify the numbers changed before regenerating prose.

## Model vs. Reality

A model that computes correct numbers from inputs can still describe an infeasible system. Examples: a financial projection assuming two contradictory tax treatments, a capacity plan where ingest rate exceeds storage throughput, a staffing model that assumes 10 hours/day per person.

When the model depends on components working together, make compatibility visible in the model (assertions, checks, comments) — not just in narrative. If an incompatibility exists, the model should surface it rather than silently producing numbers as if everything works.

## Structured Data Lives in Code

Inputs, specs, rates, costs, and parameters belong in the model as named variables (or in JSON files the model reads) — not as prose assertions in markdown. This applies to:

- **Raw inputs**: bill data, rate schedules, source data — store in JSON alongside the models that consume them
- **Technical specs**: units, capacities, constraints — store as named variables with source links
- **Compatibility constraints**: when components must work together, document these as assertions or checks in the model

This keeps data auditable, updatable in one place, and prevents drift between model and narrative.

## Running a Pipeline

```sh
# Step 1: compute the model
uv run model.py

# Step 2: fill data placeholders into the template
uv run fill_templates.py

# Step 3 (optional): fill narrative sections via Claude CLI
cat memo.filled.md | claude -p "Fill in the {{narrative:*}} sections. Return the full document." > memo.final.md
```

Always use `uv run`, never bare `python`. Commit `model_output.json` and `memo.filled.md` so the outputs are auditable without re-running.
