---
name: review-facts
description: Independent fact-check of a completed analysis pipeline. Spawns the `fact-reviewer` subagent (fresh context, read-only tools) to verify links, cited values, reproducibility, number-trace, units, formulas, and recommendation correctness. Use when an analysis (model + structured output + filled narrative document, per analysis-pipeline) is complete and needs an audit before it ships.
user-invocable: true
---

# Review Facts

Independence matters: the analysis was produced by one agent, and a fact-check done by that same agent (or any agent that watched it being built) inherits the author's reasoning and misses what the author missed. This skill enforces independence structurally by delegating the review to a fresh subagent.

## What to do

1. **Identify the analysis directory.** If the user invoked `/review-facts` without an argument, ask which analysis directory to review. Confirm it contains a model file, a structured output, and a filled narrative document (or an explicit subset).

2. **Spawn the `fact-reviewer` subagent via the Agent tool.** Pass it:
   - `subagent_type: fact-reviewer`
   - the absolute path to the analysis directory
   - any specific concerns the user raised (e.g. "I'm worried about the kWh math")

   Do **not** include your own analysis, hypotheses about what's wrong, or summaries of the artifacts in the prompt. The subagent must read the artifacts cold. Your job is to point at the directory, not to brief the reviewer.

   Example:

   ```
   Agent({
     subagent_type: "fact-reviewer",
     description: "Fact-check vendor-comparison analysis",
     prompt: "Audit the analysis in /abs/path/to/vendor-comparison/. Run every applicable check from your checklist. Write fact_review.md in that directory. Return a brief summary."
   })
   ```

3. **Relay the subagent's findings to the user.** Surface the recommendation (ship / fix-then-ship / do-not-ship), the critical-issue count, and the path to `fact_review.md`. Do not re-do the audit yourself.

## When to use this skill

- An analysis pipeline is complete and about to be acted on (decision memo, report, recommendation).
- Numbers in the analysis will be cited externally and need to hold up to scrutiny.
- Inputs were sourced from URLs or documents that should be verified.

Skip this skill for drafts in progress, exploratory notebooks, or single-number conversational answers.

## What this skill does not do

- It does not edit the analysis. The reviewer reports; the author fixes.
- It does not re-do the analysis. If methodology is wrong, it's flagged, not silently rewritten.
- It does not check prose style, tone, or grammar. Out of scope.

The full checklist (links resolve, cited values match sources, output reproducibility, number-trace, units & formulas, source freshness, recommendation matches data, compatibility constraints) lives in the `fact-reviewer` subagent's system prompt — `agents/fact-reviewer.md` in this plugin. Edit there to change what gets checked.
