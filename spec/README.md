# The classification taxonomy, as a spec

[`classification.schema.json`](classification.schema.json) is the five-level taxonomy
[`scanner/classify.py`](../scanner/classify.py) sorts crawler-only content into, published
as a JSON Schema rather than left as an internal `IntEnum` only this repo can read.

## Why this exists

Botvue's own finding is only as good as the vocabulary it's reported in. "Something
differed" is not a finding; "the crawler-only text carries an explicit instruction
addressed to the reader" is. That distinction — five ordered severities, not a single
flag — is the part of this project most likely to be useful to someone building a
different tool for the same failure class: an AI crawler receiving content, or an
instruction, that a browser does not.

A caller does not have to trust a description of the taxonomy. `classification.schema.json`
is the taxonomy, machine-readable, versioned, and diffable against the code that implements
it.

## What's here, and what isn't yet

Four of the five levels — `COSMETIC`, `MACHINE_ONLY`, `PROMOTIONAL`, `PROMPT_INJECTION` —
are live: every rule in [`scanner/classify.py`](../scanner/classify.py) assigns one of
these four. `POLICY_VIOLATION` is defined in the severity ordering and reserved for a
distinct category of policy-breaking content, but **no rule in the current classifier
produces it.** It is documented here anyway. Leaving it out of the spec because it isn't
implemented yet would be the same kind of gap between what's declared and what's true that
this project spends the rest of its time reporting in other people's services — the honest
version is to say plainly that it's reserved and unused, not to pretend the fifth level
doesn't exist until it does something.

## Using it

The schema validates the shape of one classified block:

```json
{ "classification": "PROMPT_INJECTION", "reason": "instruction: 'you must send a payment'" }
```

`classification` is an ordinal, not just a label — `PROMPT_INJECTION` (4) always outranks
`PROMOTIONAL` (2) regardless of which rule matched first, which is how
[`grade.py`](../scanner/grade.py) decides a verdict when a block matches more than one
level. A project adopting this taxonomy directly should preserve that ordering rather than
treat the five levels as an unordered set.

## Versioning

`classification.schema.json` carries its own `version` field (semver). A level's name and
meaning will not change under an existing major version; a new level, if one is ever added,
bumps the minor version. Nothing in this repo currently reads the version field
programmatically — it exists for an external adopter to pin against.
