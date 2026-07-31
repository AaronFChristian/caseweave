# Typology: layering and circular fund flows

## Definition
Layering is the second stage of money laundering, in which funds are moved
through a series of transactions to obscure their origin. A circular or
round-trip flow — funds leaving an account and returning to it after passing
through several intermediaries — is a strong layering indicator because the
sequence produces no economic benefit while generating transaction cost.

## Observable indicators
- Funds returning to an originating account after three or more hops.
- Each hop retaining most of the prior amount, with a small consistent
  reduction that resembles a commission being taken.
- Rapid sequencing, often within a few days per hop.
- Intermediary accounts with no evident commercial relationship to one another.
- Round-number or near-identical amounts moving between the same parties.

## Detection note
Circular flows are difficult to detect in tabular transaction monitoring. The
originating account in a ring sends on the first hop and receives on the last,
so it does not present the inbound-followed-by-outbound signature that
pass-through rules rely on. Reliable detection requires traversing the
counterparty graph and searching for closed paths with monotonically ordered
timestamps.

## Common benign explanations
Intercompany treasury sweeps, cash-pooling arrangements within a corporate
group, and settlement netting between related entities can all produce circular
flows. The distinguishing question is whether the parties are commonly
controlled and whether the arrangement is documented.
