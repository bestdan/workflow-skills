# The records this walk writes

Every literal block the tutorial creates, in the order `SKILL.md` calls for
them. Copy each verbatim — the ids, `destination:` paths, and filenames are
wired to each other and to the validator output quoted in the steps, so an
improvised variant breaks the very failure the walk is built to produce.

Show the learner the block before writing it, every time. Seeing the record
they are about to create is most of the lesson; a file that simply appears is
not.

## R1 — Step 3: the question, filed and open

Appended to `tracks/auth/questions.md`.

````markdown
### Q1. Should login redirect through the SSO gateway before issuing a session token?

```question
id: sso-redirect-required
status: open
blocks: sso-rollout
```

```obligation
none: filing only, no work identified yet
```
````

The bare `none:` is load-bearing, not decoration — see Step 3's prose.

## R2 — Step 4: the same question, answered, owing a destination that does not exist

Replaces the R1 section in place. The `destination:` deliberately points at a
file nobody has created yet — that is what makes the next `validate` fail.

````markdown
### Q1. Should login redirect through the SSO gateway before issuing a session token?

```question
id: sso-redirect-required
status: answered
blocks: sso-rollout
answer: yes — the gateway must own the redirect, so the session token is only issued after SSO succeeds
```

Traced through the current login handler: without the redirect, a client
can request a session token directly and skip SSO entirely. The handler
needs a check added before token issuance.

```obligation
id: sso-redirect-check
owes: the pre-issuance redirect check in the login handler
destination: dev_docs/research/onboarding/tracks/auth/obligations/sso-redirect-check.md
status: open
```
````

## R3 — Step 5: the stub card that fixes it

Written to
`dev_docs/research/onboarding/tracks/auth/obligations/sso-redirect-check.md`.

````markdown
# sso-redirect-check stub

```card
kind: stub
superseded_when: the auth track files its sso-redirect-check implementation task
```
````

## R4 — Step 6: two more rounds, two obligations each

Both sections appended to the same `tracks/auth/questions.md`. Each answer
creating **two** obligations is the point, not padding: real answers fan out
across more than one component, which is how the obligation count outruns the
question that produced it.

````markdown
### Q2. Does the session token need a shorter TTL when SSO is used?

```question
id: sso-session-ttl
status: answered
blocks: sso-rollout
answer: yes — SSO sessions should expire sooner than password sessions, and logout should revoke them immediately
```

SSO sessions inherit trust from the identity provider, so a stale token
is a wider blast radius than a stale password session. Two things follow.

```obligation
id: ttl-config-change
owes: a shorter configurable TTL for SSO-issued sessions
destination: dev_docs/research/onboarding/tracks/auth/obligations/ttl-config-change.md
status: open
```

```obligation
id: revoke-on-logout
owes: immediate session revocation on logout for SSO sessions
destination: dev_docs/research/onboarding/tracks/auth/obligations/revoke-on-logout.md
status: open
```

### Q3. Should password-based login be disabled once SSO is required?

```question
id: password-login-disable
status: answered
blocks: sso-rollout
answer: yes, eventually — but not in the same release as the SSO redirect
```

Turning it off immediately would lock out any account not yet migrated.
The rollout needs a flag and a heads-up to existing users first.

```obligation
id: legacy-password-flag
owes: a feature flag that gates password login off per-account
destination: dev_docs/research/onboarding/tracks/auth/obligations/legacy-password-flag.md
status: open
```

```obligation
id: migration-notice-copy
owes: the in-product notice telling password users to switch to SSO
destination: dev_docs/research/onboarding/tracks/auth/obligations/migration-notice-copy.md
status: open
```
````

## R5 — Step 6: the four stub cards, written up front

Same shape as R3, one file per `destination:` above, each with its own
`superseded_when:`:

- `ttl-config-change.md`
- `revoke-on-logout.md`
- `legacy-password-flag.md`
- `migration-notice-copy.md`

Written **before** `validate` this time — that is what makes Step 6's rounds
pass on the first try, and the contrast with Step 4 is the whole reason the
walk speeds up here.
