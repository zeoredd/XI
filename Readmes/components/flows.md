# Flow Types Cheatsheet

Flows define the runtime loop: who talks, when journals are written, and what triggers exit.  
All flows run under `flow_orchestrator.py` and can be switched with `sudo start flow <name>`.

---

## Quick Reference

| Flow name       | File                          | Agents involved      | Loop summary                               | Exit signals        |
|-----------------|-------------------------------|----------------------|--------------------------------------------|---------------------|
| idea_generator  | `flows/idea_generator_flow.py`| Davinci, Hermes      | User input → Davinci idea → Hermes check → repeat | `sudo end flow`, `sudo switch flow` |

---

## `idea_generator`

**File:** `flows/idea_generator_flow.py`

**Loop:**
1. User (`Brandon`) enters input
2. Davinci generates an idea
3. Mid-flow: writes a journal checkpoint for Davinci (`checkpoint_journal_for`)
4. Hermes validates Davinci’s output ("Is this grounded?")
5. Repeat until `sudo end flow` (or switch/exit)

**Special behaviors:**
- `claims_override`: inserted once as a smoke-test claim (`status=cancelled`) then cleared
- Journals written both mid-flow (checkpoint) and at flow end
- Exit via `sudo end flow` or `sudo switch flow <other>`

**Agents active:** `davinci`, `hermes`

**Use case:** brainstorming + grounding loop (Davinci proposes, Hermes checks)

---

## Adding new flows

1. Create `flows/<name>_flow.py`
2. Implement `run_<name>_flow()` with loop logic
3. Wire in `start_flow("<name>", …)` and orchestrator hooks
4. Add to this cheatsheet’s table + section


