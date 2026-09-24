# APP-14 — Settings > Approvals: "Approve automatically"

**Status: done.** Asked for on 24 Sep. 13 new tests, 1132 passing.

## Why

`agents.db` has 71 approval boxes from 22 to 24 September, and none were
denied. By source:

* **56** were desktop actions asked for by voice ("open Calculator").
* **11** were an agent using the mouse and keyboard. These always ask.
* **4** were the browser agent, over the "up to medium" ceiling.

The existing setting, "Run without asking, up to", could have removed only
those last 4.

## What it does

The **Approvals** card is now first on the Settings page. With **Approve
automatically** on:

* **Agents** go ahead without the approval box. That covers files,
  commands, the browser, the mouse and keyboard, and a pack's one-click
  steps such as posting to the books. It applies whatever "Run without
  asking" says.
* **Desktop actions asked for by voice** go ahead too. The box does not
  appear, and Tora says the result.

These still ask, every time:

* **Money leaving, or a filing.** These are SENSITIVE calls. They need a
  typed word, and a setting is not a person typing it.
* **A desktop action that sends, posts, pays or deletes**
  (`Risk.SENSITIVE`).
* **A plan,** while "Show me the plan before anything runs" is on. That
  is its own, more particular request to be asked.

Also:

* **Esc** still takes the mouse and keyboard back.
* **On the record.** Every auto-approved agent step is logged in its task's
  activity ("Approval approved automatically: click"), with the reason it
  would have asked. Turning the switch on or off is logged too.
* **Where it is saved.** It is saved like the other switches
  (`TORA_AGENT_AUTO_APPROVE`), applied to the running agents at once, and
  read when the workforce starts.
* **Off by default.**

## Where

* `AgentManager.auto_approve` and `set_auto_approve`.
* `_auto_approves` is the rule: one-click yeses only.
* `request_confirmation(may_auto_approve=)`: the caller knows what the
  action is, and the default is to ask.
* The voice path passes `may_auto_approve` as `risk < SENSITIVE`.
* The bootstrap reads the switch when the workforce is built.
* The Settings page gets the switch through the bridge.
* The voice model's tool description no longer promises a confirmation.

## Proof

`tests/agents/test_e2e_auto_approve.py` (13):

* It is off by default, and agents and voice still ask.
* With it on, the mouse and keyboard and a call over the ceiling go ahead,
  unasked and logged.
* A payment still needs the typed word, and a bare yes is refused.
* A plan still waits.
* A voice action happens at once; sending a message still asks.
* A new caller that does not decide is asked.
* The switch is saved, and read when the workforce starts.
* It is first on the Settings page.

Three mutations were each caught by the test meant for them: dropping the
payment and plan exceptions, dropping the voice exception, and unwiring the
approver.
