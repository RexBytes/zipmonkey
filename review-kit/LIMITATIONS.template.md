# LIMITATIONS

Deliberate design decisions that produce behaviour a reviewer might mistake for
a defect. Each entry is a decision you would make again, not an accident you
haven't fixed. Grouped by *why* it isn't being changed, because the category
tells a reader whether to challenge the decision or accept it.

**Maintenance rule:** when a limitation is fixed, delete its entry — do not
leave "fixed in vX" breadcrumbs. Git history carries the past; this file
describes only the current state of the library.

Create entries only once a second reviewer flags the same non-bug; don't write
them prophylactically. Cross-reference this file from the README and from any
LLM-consumable docs so agents find the rationale before "fixing" the behaviour.

Each entry is four fields, < 20 lines:
- **Concern** — one sentence: what looks wrong.
- **Decision** — one sentence: what you chose.
- **Rationale** — 2–4 sentences: why the obvious alternative is worse (name the
  specific failure mode it would introduce).
- **Escape hatch** — the exact code/flag a caller who can't accept the default
  should use instead.

---

## Fundamental ambiguity (no correct answer without content understanding)

### <example: short title>
- **Concern:** <one sentence>
- **Decision:** <one sentence>
- **Rationale:** <2–4 sentences naming the failure mode of the alternative>
- **Escape hatch:** <exact override>

## Cost-of-fix exceeds value (a cleaner impl exists but the disruption is worse)

<entries…>

## Behaviour is the contract (changing the default would silently break callers)

<entries…>
