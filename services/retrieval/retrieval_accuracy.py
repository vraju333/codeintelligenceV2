"""Deterministic retrieval evaluation; judgments must be supplied by the caller."""
from __future__ import annotations

from typing import Any


def evaluate_cases(cases: list[dict[str, Any]], search, k: int = 10) -> dict[str, Any]:
    if not cases:
        raise ValueError('At least one labeled evaluation case is required')
    if not 1 <= k <= 50:
        raise ValueError('k must be between 1 and 50')
    details = []
    for case in cases:
        query = str(case.get('query') or '').strip()
        project = str(case.get('project_path') or '').strip()
        relevant = {str(x) for x in case.get('relevant_document_ids', []) if str(x)}
        if not query or not project or not relevant:
            raise ValueError('Each case needs query, project_path and relevant_document_ids')
        response = search(query, top_k=k, project_path=project)
        results = response.get('results') or []
        if any(str(r.get('project_path') or '') != project for r in results):
            raise ValueError('Project isolation violation in retrieval results')
        ids = [str(r['document_id']) for r in results[:k]]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate document IDs in retrieval results')
        hits = [i + 1 for i, did in enumerate(ids) if did in relevant]
        reciprocal_rank = 1 / hits[0] if hits else 0.0
        # nDCG rewards relevant evidence near the top of the ranked list.
        from math import log2
        dcg = sum(1.0 / log2(rank + 1) for rank in hits)
        ideal = sum(1.0 / log2(rank + 1) for rank in range(1, min(len(relevant), k) + 1))
        ndcg = dcg / ideal if ideal else 0.0
        details.append({'query': query, 'project_path': project, 'relevant_count': len(relevant),
                        'retrieved_count': len(ids), 'hits': len(hits),
                        'precision_at_k': len(hits) / k, 'recall_at_k': len(hits) / len(relevant),
                        'reciprocal_rank': reciprocal_rank, 'ndcg_at_k': ndcg, 'retrieved_document_ids': ids})
    n = len(details)
    return {'cases': n, 'k': k, 'precision_at_k': round(sum(x['precision_at_k'] for x in details) / n, 4),
            'recall_at_k': round(sum(x['recall_at_k'] for x in details) / n, 4),
            'mrr_at_k': round(sum(x['reciprocal_rank'] for x in details) / n, 4),
            'ndcg_at_k': round(sum(x['ndcg_at_k'] for x in details) / n, 4),
            'details': details, 'judgments': 'USER_SUPPLIED', 'execution_verified': False}
