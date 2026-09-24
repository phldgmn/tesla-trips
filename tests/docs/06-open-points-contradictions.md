# Open Points and Structural Tensions in the Plan

These points are not merely formal issues — they influence how modules must be sliced and how interfaces must be designed. They should be decided consciously before or during implementation of the respectively affected modules.

## 1. Circular Dependency: ETA ↔ Weather ↔ Energy/Charging Plan

The weather query needs the expected transit time at every route point. That time, however, depends on energy consumption and charging planning — which in turn depend on the weather. This is a genuine circular dependency, not a trivial detail.

**Solution in this document set:** iterative two-phase computation with a convergence threshold (see `02-architecture.md`, section "Iterative Time/Weather Resolution"). To be clarified: the concrete threshold for a follow-up iteration and the maximum number of iterations — currently intended as a configurable parameter, not anchored as a fixed value in the plan. => This is a good first approach.

## 2. Road Route Is Fixed Independently of Energy Consumption

GraphHopper computes the route by classic criteria (time/distance/weighting) before any energy or charging information exists. The actual "trip optimization" (charging planning) can no longer change this route afterwards — it only plans charging stops on the already-fixed road course.

**Consequence:** If an alternative, slightly longer road route is energetically cheaper (e.g. less gradient, more favorable wind), the system does not find it automatically. This is not an unsolvable inconsistency, but a deliberate limitation of the current scope, now explicitly named in the document set (see `02-architecture.md`). A later extension with several GraphHopper route alternatives, from which the optimization layer picks the energetically cheapest, would be a clean, non-invasive upgrade step — if that is desired, it should be recorded as a design decision now, not discovered at implementation time. => Please keep this upgrade step in mind as a later option, but do not implement it yet.

## 3. Data Origin of "Tesla Supercharger" Is Not Specified

The plan stipulates that only Tesla Superchargers are considered — open is **where** this data is technically sourced from. Options with different implications:

- Unofficial/community APIs that extract Tesla location data from the official Tesla website/app (legally/stability-wise uncertain, but common in ABRP & Co.)
- Static, manually maintained dataset (stable, but maintenance-intensive, becomes outdated quickly)
- Filtered view of OpenChargeMap, restricted to entries with operator "Tesla" (was originally mentioned as a generic source, but per the current directive no longer intended, since explicitly *only* Tesla chargers are wanted, not "also OpenChargeMap filtered on Tesla")

The `charging_infrastructure` module is deliberately encapsulated behind a provider interface so that this decision does not touch the rest of the architecture — the concrete source should nevertheless be fixed before implementing this module. => For Tesla chargers, a crawler will be added in an expansion stage as a kind of "plugin"/module; for now the assumption is that the data exists locally (which it will later, just with a crawler for collection).

## 4. Relationship Between Waypoint ↔ Charging Stop Is Ambiguous in the Original Text

"Intermediate stops" were added as a new requirement without originally defining whether they mean (a) arbitrary mandatory waypoints (e.g. a visit, an overnight stay) or (b) an alternative term for charging stops. This document set adopted variant (a): intermediate stops are standalone mandatory waypoints, optionally with a dwell duration, that exist independently of charging planning but may coincide with a charging stop (see `01-project-specifications.md`, section "Intermediate Stops"). This assumption should be confirmed before the optimization layer (`optimization` module) is implemented, since it directly affects the state-space modeling. => Option (a) is correct.

## 5. Calibratability vs. "No Speculation About the Future"

The original objective explicitly states that the consumption model should later be calibrated from one's own driving data. Content-wise this is a statement about future usage, but at the same time an **architectural requirement on the current energy module** (separation of model structure and parameters). It was therefore not removed as a speculative future feature, but retained as a design constraint for the `energy` module (see `03-module-specifications.md`, Module 6). If that is not desired and the energy module may also start with hard-wired parameters, that would be a deliberate simplification that would have to be decided explicitly against this directive. => It may also start with hard-wired parameters; calibratability is kept in mind.

## 6. Construction Data and Planning Lead Time

When planning a trip with larger lead time (e.g. several days before departure), current DATEX II construction data does not necessarily reflect the state at the actual time of travel. This is not a structural inconsistency but an inherent limitation of the data source — however, it should be treated as uncertainty in the optimization (e.g. via the safety reserve provided anyway), not as reliable point information. => This is a general problem affecting ALL data — weather, battery degradation, construction sites, traffic conditions. It is not possible for the optimization to account for all of these uncertainties, therefore only a safety reserve for the battery is considered.

## Recommendation

Points 3 and 4 should be decided explicitly before starting the respective module implementations (`charging_infrastructure` and `optimization`) — both influence interfaces that can only be changed later at additional cost. Point 2 is not a blocker for starting, but should remain documented as a deliberate scope decision so it is not later misunderstood as a bug.
