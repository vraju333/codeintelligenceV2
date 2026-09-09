# CodeIntelligence V2 Testing Dashboard changes

This build includes the consolidated testing UX/regression fixes requested for the Student/Employee regression suite.

## Scenario and baseline UX
- Seeds/migrates the current 10-scenario Student/Employee suite.
- Baseline Overview shows every registered scenario at once.
- Active baseline version and missing-baseline status are visible without entering numeric IDs.
- Select a scenario by name to view version history.
- `Capture Missing Baselines` captures flow baselines for all scenarios that do not yet have one.
- Individual scenarios can capture a new baseline version.

## Regression analysis
- Compares Git changes against every registered active scenario baseline.
- Uses both explicit scenario dependencies (`involved_classes`) and stored endpoint-flow dependencies.
- Returns Directly Affected, Possibly Affected, and Unaffected/No Baseline groups.
- Shared mapper changes can impact both Employee and Student scenarios.
- Changed-method line dumps are collapsed under technical details by default.

## Defect investigation
- Shows readable Expected vs Actual cards.
- Shows likely code locations and a compact trace.
- Full JSON is hidden under Technical details.

## Flow UX
- Small endpoint flows render horizontally and compactly.
- Mermaid preview shrinks for very small flows.
- Large View and Download SVG remain available.

## Excel
`Export Excel` downloads `codeintelligence_scenario_regression_report.xlsx` with:
- Summary
- Scenario Registry
- Active Baselines
- Baseline History
- Endpoint Flows
- Changed Methods
- Impact Analysis
