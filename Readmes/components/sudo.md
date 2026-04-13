# Sudo Commands (flow control + memory tools)

## Purpose
A privileged command layer (prefix: `sudo …`) used inside flows.  
Lets the founder inject control signals, trigger journaling, and operate sandbox safely without polluting normal agent input.

## Roles
- **Handler:** `abilities/common_abilities/sudo_command_handler.py` — central router
- **Result codes:** `abilities/common_abilities/result_codes.py` — constants for flow control (`END`, `REFLECT`, etc.)
- **Orchestrator:** `flow_orchestrator.py` — invokes sudo handler early in `run_agent_with_runtime` to intercept commands

## Categories
- **Flow Control**
  - `sudo start flow <name>`
  - `sudo switch flow <name>`
  - `sudo end flow`
  - `sudo flow status`
- **Journaling & Reflection**
  - `sudo write journals`
  - `sudo reflect`
  - `sudo session flow`
- **Cache Controls**
  - `disabled/deprecated`
- **Vector Controls**
  - `sudo vector status`
  - `sudo vector {agent} {+N}`
  - `sudo inspect vector`
  - `sudo memory hits`
- **Sandbox**
  - `sudo sandbox new|run|promote …`
- **Memory Management**
  - `sudo promote memory` (roll summaries & archive)
  - `sudo amend` (launch amend flow)
- **Meta**
  - `sudo view journal cache`

## Guardrails
- Access is restricted: only `Brandon` is an authorized sudo user:contentReference[oaicite:0]{index=0}.
- Returns constants from `result_codes.py` (`END`, `REFLECT`, etc.) so orchestrator can act cleanly:contentReference[oaicite:1]{index=1}.
- All outputs are echoed into the flow thread for coherence, even if intercepted

## Sudo Command Cheatsheet

Founder-only. Intercepted before any agent turn, echoed into the thread, and returns control signals from `result_codes.py`.

| Command (examples)                         | Category        | Effect / Notes                                | result_code        |
|--------------------------------------------|-----------------|-----------------------------------------------|--------------------|
| `sudo start flow idea_generator`           | Flow control    | Switch/start flow; sets FLOW_CONTEXT           | START_PREFIX+name |
| `sudo switch flow super`                   | Flow control    | Alias for starting another flow                | START_PREFIX+name |
| `sudo end flow`                            | Flow control    | Cleanly end current flow and write journals    | END               |
| `sudo flow status`                         | Flow control    | Show FLOW_CONTEXT status                       | NOOP              |
| `sudo write journals`                      | Journaling      | Trigger session journals now                   | SESSION_JOURNAL   |
| `sudo reflect`                             | Journaling      | Quick GAEL reflection, doesn’t end flow        | REFLECT           |
| `sudo session flow`                        | Journaling      | Write checkpoint journal                       | SESSION_JOURNAL   |
| `sudo vector status`                       | Vector control  | Show vector allowance                          | NOOP              |
| `sudo vector {agent} +1`                   | Vector control  | Grant extra vector use                         | NOOP              |
| `sudo inspect vector` / `sudo memory hits` | Vector control  | Inspect vector hits / provenance               | NOOP              |
| `sudo sandbox new|run|promote …`           | Sandbox         | Create/run/promote sandbox sessions            | NOOP              |
| `sudo promote memory`                      | Maintenance     | Trigger rollup promotions manually             | NOOP              |
| `sudo amend`                               | Maintenance     | Launch amendment overlay flow                  | NOOP              |
| `sudo view journal cache`                  | Meta            | Show last JOURNAL_PATH & parsed cache          | NOOP              |
| (unknown / malformed)                      | Help            | Print usage help, never crashes                | NOOP              |

### Notes
- **Timing:** Checked first in `flow_orchestrator`, before agent turn.  
- **Thread coherence:** Always echoed back into the thread.  
- **Auth:** Founder-only; ignored if unauthorized.  
- **Result codes:** `END`, `REFLECT`, `SESSION_JOURNAL`, `START_PREFIX`, `NOOP`.


