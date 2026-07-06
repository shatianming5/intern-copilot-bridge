# J script authoring guide

<!-- METADATA:TYPE=J,STATUS=ReviewGuide,OWNER=intern_ci_lead,SESSION=12 -->

This guide is the review contract for J journey scripts after the F/J CI
refactor. It describes what belongs in a J doc, how to write script steps, and
which workspace migration language is no longer valid.

## J/F boundary

Both F and J are script-driven stories. The difference is cost and user intent:

- F is a deployment capability story. It may use CLI, GUI, relay, daemon, source
  contracts, and visible Feishu/card surfaces, but it must not ask an agent to
  think or act and must not require paid LLM output.
- J is a real user journey. It starts real Codex or Claude work through a user
  prompt or user-visible action, waits for agent behavior, and asserts the
  journey result from user-visible and product evidence.
- Feishu group usage alone is not J. GUI usage alone is not J. Agent work is the
  boundary.
- Agent work alone is also not enough. Generic behavior for generic user
  instructions is out of scope: language preference, table-only formatting,
  minimal diff, refactor style, patch application, generic repro/fix, branch
  summaries, and similar ordinary agent skills should not become standalone J
  cases.
- Built-in prompt behavior may be J only when the doc names the explicit prompt
  contract being tested, such as dirty-worktree preservation, no-web/latest
  lookup policy, destructive-command approval, scoped cleanup, or own-change
  rollback. Plugin/product journeys such as Feishu delivery, session
  lifecycle, goal/helper/peer/slash flows, protected MR/PR lifecycle, and
  provider-specific surfaces remain valid J candidates.

Examples:

- `J_0014` is implemented because it starts live sender/receiver intern sessions
  and validates peer delivery in a receiver pane.
- `J_0033` is implemented because it sends a real `hi` prompt to Codex, waits
  for a reply, then verifies `/exit`, manual resume, and GUI-equivalent restart
  keep the same durable session UUID.

## Document shape

Every J script doc should be reviewable without reading runner code. Include:

- Metadata on line 3 with `TYPE=J`, `ID=J_XXXX`, status, owner, and session.
- Goal and why the case is J.
- Cost profile: paid/agent by default, or an explicit explanation if not.
- Required environment and pre-created shared resources.
- Case-scoped resources, resource locks, and namespace ownership.
- Start-of-case cleanup for the case namespace.
- Retained scene policy and any intentional deletion story.
- Natural user prompts.
- Step-by-step script with explicit `action`, `wait`, and `assert` rows.
- Assertions, report evidence, and product/CI/helper failure classification.
- Cleanup boundaries: what the case may clean and what must remain for review.
- Implementation prerequisites when a helper does not yet exist.

## Script step style

Use a simple table or list where every step is one of:

| Type | Meaning | Examples |
|------|---------|----------|
| `action` | The CI driver or visible user performs an operation. | Send a prompt, start a session, click a card, run a GUI-equivalent command. |
| `wait` | The script waits for asynchronous product state. | Wait for message delivered, turn started, agent reply, PR opened, image message visible. |
| `assert` | The script checks pass/fail evidence. | Assert reply text, session UUID, target revision, card pending state, report redaction. |

Each row should name the expected evidence, not only the product call. Good
steps say what will be visible in Feishu/GUI/tmux/report and where the runner
will find it.

## Prompt rules

J prompts should read like normal user language:

- Do not expose case id, run id, worker id, provider/mode internals, helper
  names, assertion names, or test expectations.
- Do not ask the agent to optimize for the test harness.
- Use fixed tokens only when the product journey itself needs a user-provided
  token, and describe that as a normal user request.
- It is acceptable for CI-visible actions to emit `[CI模拟] ...` audit messages
  before driving a click or callback. The user prompt itself should remain
  natural.

## Resource naming

New J resources must use stage-aware names:

- namespace: `ci_j_XXXX`
- workspace or workspace fixture: `ci_j_XXXX_workspace_<run_id>`
- intern: `intern_ci_j_XXXX_<backend>_<run_id>`
- task or task fixture: `task_ci_j_XXXX_<purpose>_<run_id>`
- report and artifact names: `j_XXXX_<purpose>`

Already implemented or already assigned legacy names do not need renaming in a
docs-only review, but new docs should use the J prefix contract.

## Cleanup and retained scene

Default policy:

- Clean only the case namespace at the start of the case.
- Retain the current Feishu group scene and report artifacts at the end so the
  supervisor can inspect the journey.
- Do not clean lead-owned shared workspaces, shared debug deployments, or other
  cases' resources.

If the story intentionally deletes an intern, group, task, branch, or change
request, the doc must say deletion is part of the user journey and must require
report evidence for that deletion.

## Workspace mode migration

Workspace mode is fixed at add time. Do not write a J success path that uses:

- `workspace mode set`
- daemon `/mode/set`
- relay `/mode/set`
- an in-place "change repo mode" operation

If a future J needs workspace mode migration, write the explicit migration
journey instead:

1. Release or delete the old relay workspace record for the case scope.
2. Run `internctl workspace migrate-mode --repo-url <repo> --target <repo_dotdir|metadata_branch>`.
3. Review and merge the migration PR/MR.
4. Re-add or reuse the workspace in the target mode.
5. Assert task/status/history/knowledge/skill metadata resolve through the new
   mode.

`local_only` cannot migrate into or out of remote modes in J. Refusal and guard
behavior for that boundary belongs in F.

## Implementation prerequisites

Do not weaken a real user story because a helper is missing. Mark the helper as
an implementation prerequisite instead.

Examples:

- Missing real card click/form submit support means `J_0007` needs a card helper
  before implementation.
- Missing turn input capture means `J_0008` needs a turn input helper before it
  can assert that continuation did not receive a full old prompt.
- Missing Claude prompt/reply evidence means Claude skill or resume J cases need
  a Claude real-turn helper before they can pass.

## Status vocabulary

Use these statuses consistently:

- `Implemented`: the J exists in the active J registry and has accepted evidence.
- `ReviewDraft`: the script is proposed for review but not implemented.
- `Retired`: the old story is not a valid success path.
- `Prerequisite`: helper or product capability work needed before the story can
  be implemented.

## Review checklist

Before a J doc is ready for implementation review, confirm:

- It clearly says why the case is J instead of F.
- Prompts are natural user language.
- Setup, start cleanup, retained scene, resource locks, reports, and cleanup
  boundaries are explicit.
- Each step is an `action`, `wait`, or `assert`.
- The report evidence can classify product bugs, CI bugs, and missing helpers.
- No success path depends on in-place workspace mode switching.
