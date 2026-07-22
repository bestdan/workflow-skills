#!/bin/bash
# Probe 4 driver — run AS THE AGENT (uid 502). Disposable spike (rule 4).
#
# The installation token arrives on STDIN (never argv, never env, never a file),
# minted maintainer-side by mint.py. Every step prints an explicit
# EXPECT/RESULT/VERDICT so the maintainer can classify. The token is redacted
# from all output. No secrets are persisted; $WORK is deleted at the end.
#
# Invoked attended, e.g.:
#   APP_ID=.. KEY_PATH=.. INSTALLATION_ID=.. uv run --no-project \
#     --with 'pyjwt[crypto]' python mint.py token \
#   | sudo -u agent GH=/opt/homebrew/bin/gh KEY_PATH=<app.pem> bash driver.sh
set -u

TOK="$(cat)"                                  # installation token from stdin
REPO="bestdan/autopilot-p4-test"
NOINSTALL="bestdan/autopilot-p4-noinstall"
GH="${GH:-$(command -v gh || echo /opt/homebrew/bin/gh)}"
KEY_PATH="${KEY_PATH:-/Users/danielegan/src/workflow-skills/fluff/autopilot-p4-test-bestdan.2026-07-22.private-key.pem}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/p4agent.XXXXXX")"
BR="autopilot/probe4-$$"
AUTH="https://x-access-token:${TOK}@github.com"

red(){ sed -e "s#${TOK}#<REDACTED>#g"; }      # strip the token from any output
line(){ printf '\n=== %s ===\n' "$1"; }
verdict(){ printf 'VERDICT\t%s\t%s\n' "$1" "$2"; }   # step, PASS|FAIL

# Isolated identity: no ambient git config, no personal gh keyring, no cred store.
export HOME="$WORK/home"; mkdir -p "$HOME"
export GIT_CONFIG_GLOBAL="$HOME/.gitconfig"
export GIT_CONFIG_NOSYSTEM=1
export GH_CONFIG_DIR="$HOME/gh"               # empty -> no personal gh login
unset GITHUB_TOKEN
git config --global user.email "agent@autopilot.invalid"
git config --global user.name  "autopilot agent"

line "provenance (C) — who am I, what credentials are visible"
printf 'uid\t%s (%s)\n' "$(id -u)" "$(id -un)"
printf 'HOME\t%s\n' "$HOME"
printf 'git_credential_helper\t%s\n' "$(git config --show-origin --get-all credential.helper 2>&1 | red || echo none)"
printf 'GH_TOKEN_env\t%s\n' "${GH_TOKEN:-<unset>}"
printf 'GITHUB_TOKEN_env\t%s\n' "${GITHUB_TOKEN:-<unset>}"

line "C1 — agent must NOT be able to read the App private key"
echo "EXPECT: permission denied"
if cat "$KEY_PATH" >/dev/null 2>"$WORK/keyerr"; then
  echo "RESULT: READ SUCCEEDED (key is agent-readable!)"; verdict C1_key_unreadable FAIL
else
  printf 'RESULT: %s\n' "$(cat "$WORK/keyerr" | red)"; verdict C1_key_unreadable PASS
fi

line "A1 — clone the installed repo over the App token"
echo "EXPECT: success"
if git clone "$AUTH/$REPO.git" "$WORK/repo" 2>"$WORK/a1" ; then
  git -C "$WORK/repo" remote set-url origin "https://github.com/$REPO.git"  # drop token from config
  echo "RESULT: cloned"; verdict A1_clone PASS
else
  cat "$WORK/a1" | red; verdict A1_clone FAIL
fi

line "A2 — commit + push to an autopilot/** branch (allowed by ruleset)"
echo "EXPECT: success"
( cd "$WORK/repo" \
  && echo "probe4 $(date -u +%FT%TZ)" > probe4.txt \
  && git add probe4.txt && git commit -q -m "probe4: agent commit" \
  && git push "$AUTH/$REPO.git" "HEAD:refs/heads/$BR" ) 2>"$WORK/a2"
if [ $? -eq 0 ]; then echo "RESULT: pushed $BR"; verdict A2_push_feature PASS
else cat "$WORK/a2" | red; verdict A2_push_feature FAIL; fi

line "A3 — open PR, comment, close (with GraphQL capture)"
echo "EXPECT: success; capture the GraphQL ops gh issues"
export GH_TOKEN="$TOK"
GH_DEBUG=api "$GH" pr create -R "$REPO" --head "$BR" --base main \
  --title "probe4 PR" --body "disposable" >"$WORK/prurl" 2>"$WORK/a3.debug"
if [ $? -eq 0 ]; then
  PR="$(cat "$WORK/prurl")"; echo "RESULT: PR $PR"
  GH_DEBUG=api "$GH" pr comment "$PR" -R "$REPO" --body "probe4 comment" >/dev/null 2>>"$WORK/a3.debug" && echo "commented"
  "$GH" pr close "$PR" -R "$REPO" >/dev/null 2>>"$WORK/a3.debug" && echo "closed"
  verdict A3_pr_comment_close PASS
else cat "$WORK/a3.debug" | red; verdict A3_pr_comment_close FAIL; fi
echo "--- GraphQL operations gh issued on the A3 path ---"
grep -iE 'graphql|POST https|GET https' "$WORK/a3.debug" | red | sed 's/^/  /' | sort -u | head -40

line "B1 — push to default branch main (ruleset: DENY)"
echo "EXPECT: denied"
( cd "$WORK/repo" && git push "$AUTH/$REPO.git" "HEAD:refs/heads/main" ) 2>"$WORK/b1"
if [ $? -ne 0 ]; then printf 'RESULT(denied): %s\n' "$(grep -iE 'protected|ruleset|denied|rejected|cannot' "$WORK/b1" | red | head -1)"; verdict B1_default_branch_denied PASS
else echo "RESULT: PUSH SUCCEEDED (should have been denied!)"; verdict B1_default_branch_denied FAIL; fi

line "B2 — push to a non-matching branch random/** (ruleset: DENY creation)"
echo "EXPECT: denied"
( cd "$WORK/repo" && git push "$AUTH/$REPO.git" "HEAD:refs/heads/random/foo-$$" ) 2>"$WORK/b2"
if [ $? -ne 0 ]; then printf 'RESULT(denied): %s\n' "$(grep -iE 'protected|ruleset|denied|rejected|cannot' "$WORK/b2" | red | head -1)"; verdict B2_nonmatching_branch_denied PASS
else echo "RESULT: PUSH SUCCEEDED (should have been denied!)"; verdict B2_nonmatching_branch_denied FAIL; fi

line "B3 — operate on a NON-installed repo (token unscoped: DENY)"
echo "EXPECT: denied (token not scoped to $NOINSTALL)"
git clone "$AUTH/$NOINSTALL.git" "$WORK/noinstall" 2>"$WORK/b3"
if [ $? -ne 0 ]; then printf 'RESULT(denied): %s\n' "$(cat "$WORK/b3" | red | head -1)"; verdict B3_noninstalled_repo_denied PASS
else echo "RESULT: CLONE SUCCEEDED (should have been denied!)"; verdict B3_noninstalled_repo_denied FAIL; fi
gh_b3=$("$GH" api "repos/$NOINSTALL" 2>&1 | red | head -1); printf 'gh api %s -> %s\n' "$NOINSTALL" "$gh_b3"

line "C2 — remove the token: ops must FAIL closed (no ambient fallback)"
echo "EXPECT: auth failure (private repo, no credential available)"
unset GH_TOKEN
git clone "https://github.com/$REPO.git" "$WORK/anon" 2>"$WORK/c2"
if [ $? -ne 0 ]; then printf 'RESULT(failed-closed): %s\n' "$(cat "$WORK/c2" | red | head -1)"; verdict C2_no_fallback PASS
else echo "RESULT: ANON CLONE SUCCEEDED (ambient credential leaked in!)"; verdict C2_no_fallback FAIL; fi

line "cleanup — delete the pushed feature branch, wipe workdir"
export GH_TOKEN="$TOK"
"$GH" api --method DELETE "repos/$REPO/git/refs/heads/$BR" >/dev/null 2>&1 && echo "deleted $BR" || echo "branch cleanup skipped"
unset GH_TOKEN
rm -rf "$WORK"
echo
echo "=== SUMMARY (VERDICT lines above are machine-readable) ==="
