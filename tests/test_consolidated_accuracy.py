from services.retrieval.retrieval_accuracy import evaluate_cases
import pytest


def test_ndcg_and_mrr_are_grounded_in_labeled_rankings():
    def search(query, top_k, project_path):
        return {'results': [{'document_id': doc, 'project_path': project_path} for doc in ('wrong', 'right')]}
    report = evaluate_cases([{'query': 'countryCode', 'project_path': 'customer', 'relevant_document_ids': ['right']}], search, k=2)
    assert report['precision_at_k'] == 0.5
    assert report['recall_at_k'] == 1.0
    assert report['mrr_at_k'] == 0.5
    assert 0 < report['ndcg_at_k'] < 1


def test_duplicate_retrieval_evidence_is_rejected():
    def search(query, top_k, project_path):
        return {'results': [{'document_id': 'same', 'project_path': project_path}] * 2}
    with pytest.raises(ValueError, match='Duplicate'):
        evaluate_cases([{'query': 'q', 'project_path': 'customer', 'relevant_document_ids': ['same']}], search, k=2)
