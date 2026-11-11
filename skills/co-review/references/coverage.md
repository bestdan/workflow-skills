# Coverage — what the review actually read

A file with no finding looks the same as a file nobody read. A reader who sees
three reviewers marked ✅ assumes the whole diff was covered. The Coverage line
on the output template says which parts were not.

## Fill it after step 7

Sort the reviewable files, the ones `pr-complexity.py` did not exclude, into
three tiers by path or glob:

- **full**: read line by line by at least one reviewer, you included;
- **skim**: looked at, with some claims checked, but not every line;
- **none**: not reviewed.

Base the tiers on what reviewers did, not on what they were sent. Every
reviewer gets the whole diff. A reviewer's output shows what it engaged with:
the files its findings cite, and the files it says it checked. Your own review
counts as `full` only for the files you read line by line.

## When it is required

Always, in every mode. When every reviewable file got a full review, the line
says ✅ **full**. Otherwise it names the `skim` and `none` paths: an unreviewed
area is stated, never implied. At `large` and `very-large` expect at least one
`skim` or `none` entry, and look again before claiming ✅ **full**.

Under `--post`, the review `body` carries the Coverage line after the report
lines, so the author and every later reader see it too. Under
`--non-interactive`, it goes on section 1's reviewer line beside the
`COMPLEXITY:` line.
