# Example: Vendor Cost Comparison

Demonstrates the analysis pipeline pattern from `../SKILL.md` — a three-vendor cloud hosting cost comparison that produces a recommendation memo.

## Files

- `model.py` — computes all costs from inputs; writes `model_output.json`
- `memo.template.md` — document structure with `{{key}}` and `{{narrative:*}}` placeholders
- `fill_templates.py` — replaces `{{key}}` placeholders from JSON; writes `memo.filled.md`
- `model_output.json` — committed output of `model.py`
- `memo.filled.md` — committed output of `fill_templates.py`

## How to Run

```sh
# Step 1: compute the model
uv run model.py

# Step 2: fill data placeholders into the template
uv run fill_templates.py

# Step 3 (optional): fill narrative sections via Claude CLI
cat memo.filled.md | claude -p "Fill in the {{narrative:rationale}} section with 2-3 sentences explaining the recommendation. Return the full document." > memo.final.md
```

The first two steps are deterministic. Step 3 regenerates prose from the data-filled document — re-run it anytime without touching the model.
