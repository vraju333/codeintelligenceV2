"""Focused offline CAS-2 correlation checks; no live Jira or database required."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from services.agent.current_change_traceability import build, _attributes_from_changes


class CurrentChangeTraceabilityTest(unittest.TestCase):
    def test_constant_getter_links_and_no_fake_implementation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'CustomerService.java'
            source.write_text('''class CustomerService {
private static final Pattern COUNTRY = Pattern.compile("^[A-Z]{2}$");
private void validate(Customer c) { if (!COUNTRY.matcher(c.getCountryCode()).matches()) throw new RuntimeException(); }
public Customer create(CustomerXmlRequest r) { validate(c); return c; }
public Customer replace(String id, CustomerXmlRequest r) { validate(c); return c; }
public Customer updateAccountType(String id, String type) { validate(c); return c; }
public Customer get(String id) { return c; }
}''')
            git = {'git_root': str(root), 'changed_methods': ['CustomerService.validate'],
                   'constant_value_changes': [{'symbol': 'COUNTRY', 'before': 'Pattern.compile("^[A-Z]{2,3}$")', 'after': 'Pattern.compile("^[A-Z]{2}$")'}],
                   'changed_files': [{'file_path': 'CustomerService.java', 'changed_methods': [{'method_name': 'validate'}],
                                      'semantic_analysis': {'constant_changes': [{'symbol': 'COUNTRY', 'before': 'Pattern.compile("^[A-Z]{2,3}$")', 'after': 'Pattern.compile("^[A-Z]{2}$")'}]}}]}
            self.assertEqual(_attributes_from_changes(git)[0]['attribute'], 'countryCode')
            class Mapping:
                def lineage(self, db, attribute):
                    return {'attribute': attribute, 'row': 6}
            with patch('services.agent.current_change_traceability.lookup_attribute_jiras', return_value={'matches': [{'jira_id': 'CAS-2', 'relationship': 'REQUIREMENT_ATTRIBUTE_MENTION'}]}):
                report = build(None, git, Mapping())
            impact = report['attribute_impacts'][0]
            self.assertEqual(impact['related_jira_requirements'][0]['jira_id'], 'CAS-2')
            self.assertEqual({c['method'] for c in impact['same_class_callers']}, {'create', 'replace', 'updateAccountType'})
            self.assertEqual(impact['jira_relation'], 'REQUIREMENT_LEVEL_ONLY')
            self.assertNotIn('get', [c['method'] for c in impact['same_class_callers']])

if __name__ == '__main__':
    unittest.main()

class ControllerOperationTraceTest(unittest.TestCase):
    def test_resolves_spring_routes_from_actual_controller_calls(self):
        from services.agent.current_change_traceability import _controller_operations
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / 'src/main/java/demo/controller'
            folder.mkdir(parents=True)
            (folder / 'CustomerController.java').write_text('''
@RestController
@RequestMapping("/api/customers")
class CustomerController {
@PostMapping(consumes = MediaType.APPLICATION_XML_VALUE)
public Customer create(Request r) { return service.create(r); }
@PutMapping(value = "/{customerId}")
public Customer replace(String id, Request r) { return service.replace(id, r); }
@PatchMapping(value = "/{customerId}/account-type")
public Customer updateAccountType(String id) { return service.updateAccountType(id, "PREMIUM"); }
@GetMapping("/{customerId}")
public Customer get(String id) { return service.get(id); }
}''')
            git = {'git_root': str(root)}
            create = _controller_operations(git, 'create')
            replace = _controller_operations(git, 'replace')
            patch = _controller_operations(git, 'updateAccountType')
            self.assertEqual((create[0]['http_method'], create[0]['endpoint']), ('POST', '/api/customers'))
            self.assertEqual((replace[0]['http_method'], replace[0]['endpoint']), ('PUT', '/api/customers/{customerId}'))
            self.assertEqual(patch[0]['classification'], 'SHARED_VALIDATION_REGRESSION_DEPENDENCY')
            self.assertEqual(patch[0]['endpoint'], '/api/customers/{customerId}/account-type')
            self.assertEqual(_controller_operations(git, 'delete'), [])
