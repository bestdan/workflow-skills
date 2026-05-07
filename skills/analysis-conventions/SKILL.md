---
name: analysis-conventions
description: Coding conventions for analysis work — marimo vs plain scripts, uv for execution, scratchpad patterns, notebook structure, input/formula/source organization. Use when writing notebooks or analysis scripts.
user-invocable: false
---

# Analysis Conventions

**Note**: Project-level preferences (CLAUDE.md, AGENTS.md) override these.

## When to Use Plain Scripts vs Marimo 

- **Plain `.py` script**: deterministic pipeline steps, one-shot computations, CI/automation contexts where a notebook adds unnecessary complexity.
- **Marimo notebook**: exploratory analysis, interactive parameter tweaking, work that benefits from visible intermediate outputs and tables. Good default for analysis work.

When in doubt, start with a plain script. Refactor to marimo if the user wants interactivity.

- `uvx marimo edit <path>` — interactive editing
- `uvx marimo export html <path> -o output.html` — executes cells and renders output (use for generating reports as side effects)
- `uvx marimo export md <path>` — exports source code as markdown (does NOT execute cells)

## Script Execution

Use `uv run` for executing plain scripts. Use `uvx` for running CLI tools (like marimo). Do not use bare `python` — `uv` handles dependency resolution and virtual environments.

## Scratchpad Scripts

When testing setup calls or ephemerally computing numbers during a conversation, MUST write a `temp/_.py` file and re-use it. Don't inline multi-line Python in bash commands that need to be re-approved each time.

Once the numbers are incorporated into a notebook or model, delete the scratchpad — its purpose is to make a single approval all that's required, keep intermediate work visible, not to be a permanent artifact. If there's no model to fold it into, the scratchpad *is* the artifact and should be kept. The agent should clean up `temp/` files before ending the conversation unless they are the final output.

MUST NOT embed raw calculated numbers in markdown without a visible formula or a reference to the model that produced them.

## Notebook/Script Structure

- **Inputs first** — key parameters, inputs, specs etc with source links, rates, costs as simple variable assignments
- **Formulas as functions** — named, with docstrings showing the formula
- **Show your work** — render tables with the formula and result side by side (e.g., `(370/1000) x 92 x 24 = 817`)
- **Sources at the bottom** — document where inputs came from
