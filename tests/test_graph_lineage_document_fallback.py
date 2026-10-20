"""Regression: imported mapping evidence remains visible without Neo4j edges."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from services.lineage.mapping_intelligence_service import MappingIntelligenceService


class GraphLineageDocumentFallbackTest(unittest.TestCase):
    def setUp(self):
        self.service = MappingIntelligenceService()
        self.project = r"D:\AIlearning\customer-account-service"
        self.doc = SimpleNamespace(id=7, project_path=self.project, status="ACTIVE",
            filename="Customer_Account_Service_Mapping.xlsx", title="Customer mapping",
            mapping_family="Customer mapping", document_version=None,
            source_ref=r"D:\AIlearning\customer-account-service\docs\Customer_Account_Service_Mapping.xlsx",
            sheet_count=1, row_count=1, created_at=None)
        self.mapping = SimpleNamespace(id=12, document_id=7, status="ACTIVE",
            source_type="XML", source_system=None, source_path="/CustomerAccount/CountryCode",
            mapping_rule="Trim + uppercase; 2-3 letters", target_class="Customer",
            target_attribute="countryCode", target_expression="Customer.countryCode",
            null_rule=None, validation_rule=None, comments=None,
            sheet_name="Customer Mapping", row_number=6)
        self.db = MagicMock()
        self.db.query.return_value.filter.return_value.all.side_effect = [[self.doc], [self.mapping]]

    def test_sql_mapping_is_partial_when_graph_has_no_match(self):
        with patch.object(self.service, '_project_path', return_value=self.project), \
             patch.object(self.service, 'ensure_active_project_workbooks'), \
             patch.object(self.service, '_graph_lineage_neo4j', return_value={
                 'status': 'NO_MATCH', 'mappings': [], 'mapping_count': 0,
                 'engineering_impact': None}):
            result = self.service.graph_lineage('countryCode', self.project, self.db)
        self.assertEqual(result['status'], 'PARTIAL_MATCH')
        self.assertEqual(result['document_mapping_count'], 1)
        self.assertEqual(result['graph_mapping_count'], 0)
        self.assertEqual(result['mapping_rows'][0]['source_path'], '/CustomerAccount/CountryCode')
        self.assertIsNone(result['engineering_impact'])
        self.assertEqual(result['mapping_count'], 0)

    def test_no_sql_or_graph_match_stays_no_match(self):
        self.db.query.return_value.filter.return_value.all.side_effect = [[], []]
        with patch.object(self.service, '_project_path', return_value=self.project), \
             patch.object(self.service, 'ensure_active_project_workbooks'), \
             patch.object(self.service, '_graph_lineage_neo4j', return_value={
                 'status': 'NO_MATCH', 'mappings': [], 'mapping_count': 0,
                 'engineering_impact': None}):
            result = self.service.graph_lineage('unrelatedAttribute', self.project, self.db)
        self.assertEqual(result['status'], 'NO_MATCH')
        self.assertEqual(result['document_mapping_count'], 0)


if __name__ == '__main__':
    unittest.main()
