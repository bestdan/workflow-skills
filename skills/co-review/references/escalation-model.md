# Choosing the model for the step-9 escalation

Step 9 escalates a medium-confidence design call to a fresh, read-only subagent
before it reaches the user. What makes that escalation worth doing is
independence, not the model. The subagent did not write the change, did not
reconcile the findings, and sees only the item, its hunk and the files the item
cites.

So spawn it even when it would run the same model as this session. The fresh
context is what you are buying, and a same-model call is not redundant.

For the model itself, default to the strongest one available. Inherit the
session model, or pin the top tier when this session runs a smaller one. Don't
pick a type whose harness default is a small model (Claude Code's `Explore`, for
example) unless you also pin the model.
