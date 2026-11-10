# countryCode — Phase 13 evidence review

**Status: CANDIDATE ONLY — not independently verified against source repository or test executions.**

Ground truth approved: **False**

- JIRA requirement mentions: 6 — CAS-1, CAS-10, CAS-2, CAS-5, CAS-7, CAS-9
- Java method mentions: 18 (10 in main source)
- Mapping row matches: 6
- Imported test case entries: 20; distinct test IDs: 10
- Duplicate mapping source paths: 1

## Production Java method candidates
- `src/main/java/com/codeintelligence/customeraccount/entity/CustomerAccountEntity.java::getCountryCode` (line 26)
- `src/main/java/com/codeintelligence/customeraccount/entity/CustomerAccountEntity.java::setCountryCode` (line 27)
- `src/main/java/com/codeintelligence/customeraccount/mapper/CustomerJpaMapper.java::toEntity` (line 9)
- `src/main/java/com/codeintelligence/customeraccount/mapper/CustomerJpaMapper.java::toModel` (line 22)
- `src/main/java/com/codeintelligence/customeraccount/mapper/CustomerXmlMapper.java::toModel` (line 11)
- `src/main/java/com/codeintelligence/customeraccount/model/Customer.java::getCountryCode` (line 19)
- `src/main/java/com/codeintelligence/customeraccount/model/Customer.java::setCountryCode` (line 20)
- `src/main/java/com/codeintelligence/customeraccount/service/CustomerService.java::validate` (line 120)
- `src/main/java/com/codeintelligence/customeraccount/xml/CustomerXmlRequest.java::getCountryCode` (line 23)
- `src/main/java/com/codeintelligence/customeraccount/xml/CustomerXmlRequest.java::setCountryCode` (line 24)

## Test case provenance (same test may be imported from different sources)
- `CAS2-TC001`: 2 source(s)
- `CAS2-TC002`: 2 source(s)
- `CAS2-TC003`: 2 source(s)
- `CAS2-TC004`: 2 source(s)
- `CAS2-TC005`: 2 source(s)
- `CAS2-TC006`: 2 source(s)
- `CAS2-TC007`: 2 source(s)
- `CAS2-TC008`: 2 source(s)
- `CAS2-TC009`: 2 source(s)
- `CAS2-TC010`: 2 source(s)

## Duplicate mapping source paths
- `d:/ailearning/customer-account-service/docs/customer_account_service_mapping_v1.xlsx`: document IDs [10, 8]

## Mapping requirement conflicts
- `countryCode`: requirement `Exactly two letters` vs mapping `Trim + uppercase; 2-3 letters` in `Customer_Account_Service_Mapping_V1.xlsx`
- `countryCode`: requirement `Exactly two letters` vs mapping `Trim + uppercase; 2-3 letters` in `Customer_Account_Service_Mapping_V3.xlsx`
- `countryCode`: requirement `Exactly two letters` vs mapping `Trim + uppercase; 2-3 letters` in `Customer_Account_Service_Mapping.xlsx`
- `countryCode`: requirement `Exactly two letters` vs mapping `Trim + uppercase; 2-3 letters` in `Customer_Account_Service_Mapping_V1.xlsx`
- `countryCode`: requirement `Exactly two letters` vs mapping `Trim + uppercase; 2-3 letters` in `Customer_Account_Service_Mapping_V2.xlsx`
- `countryCode`: requirement `Exactly two letters` vs mapping `Trim + uppercase; 2-3 letters` in `Customer_Account_Service_Mapping_V4.xlsx`

## Evidence limitations
- `code_impact.status` is NOT_CHECKED
- Git verified changes: 0
- Test execution verified: 0
- Baseline impact is not composed in this response.
- Candidate call paths are not AST/CFG or verified field-level data flow.

## Next verification actions
1. Open the Java files above and confirm each method really transforms, validates, persists, or exposes countryCode.
2. Confirm JIRA relationships against requirements; a text mention alone is not a dependency.
3. Confirm the test IDs and execution results in test documents/CI.
4. Confirm mapping version metadata and determine whether duplicate source records are historical or erroneous.
5. Only then set approved=true and calculate precision/recall against independently reviewed labels.
