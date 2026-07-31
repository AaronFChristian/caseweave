# Governance expectations for automated investigation tools

## Scope
Where an institution uses automated tooling to triage alerts, assemble
evidence, or draft regulatory filings, that tooling falls within the
institution's model risk management framework. Generative and agentic systems
raise additional questions because their outputs are not deterministic.

## Core expectations
- **Inventory and tiering.** Every model in use is recorded with an owner and
  tiered by the materiality of the decisions it influences.
- **Conceptual soundness.** The design rationale is documented, including why
  the chosen approach fits the problem and what its known limitations are.
- **Outcomes analysis.** Performance is measured against a labelled reference
  set before deployment and monitored on an ongoing basis afterwards.
- **Ongoing monitoring.** Input drift, output quality, and error rates are
  tracked, with defined thresholds that trigger review.
- **Independent challenge.** A function independent of the developers reviews
  the model and records its findings and their resolution.

## Specific to generative systems
Outputs presented to a regulator should be attributable to source evidence.
Where a system drafts prose, the institution should be able to demonstrate
which underlying record supports each assertion. Prompt versions, model
versions, and retrieval configurations are part of the model definition and
must be versioned, because an output that cannot be reproduced cannot be
validated.

## Human oversight
The degree of automation permitted should be tied to demonstrated performance
and to the consequences of error. Automated closure of alerts carries different
risk from automated drafting subject to human approval, and the controls should
differ accordingly. Overrides by human reviewers should be captured with a
structured reason so that the override population itself becomes an input to
ongoing monitoring.
