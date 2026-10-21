import pytest
from services.retrieval.retrieval_accuracy import evaluate_cases


def test_metrics():
    def search(query, top_k, project_path):
        return {'results': [{'document_id': 'a', 'project_path': project_path},
                            {'document_id': 'b', 'project_path': project_path}]}
    result = evaluate_cases([{'query': 'countryCode', 'project_path': 'project-a',
                              'relevant_document_ids': ['b', 'c']}], search, k=2)
    assert result['precision_at_k'] == 0.5
    assert result['recall_at_k'] == 0.5
    assert result['mrr_at_k'] == 0.5


def test_project_leak_rejected():
    def search(query, top_k, project_path):
        return {'results': [{'document_id': 'a', 'project_path': 'other'}]}
    with pytest.raises(ValueError, match='isolation'):
        evaluate_cases([{'query': 'x', 'project_path': 'one', 'relevant_document_ids': ['a']}], search)
