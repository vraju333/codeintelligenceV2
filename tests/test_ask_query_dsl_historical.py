from services.agent.ask_query_dsl import parse_query


def test_short_jira_questions_route_to_history():
    for query in ('CAS-2 jira related code changes please', 'AS-3 related code changes please',
                  'What did we change for CAS-4?', 'Which APIs were impacted by CAS-3?'):
        result = parse_query(query)
        assert result.intent == 'JIRA_HISTORICAL_IMPACT', query
        assert result.jira_keys, query


def test_current_changes_do_not_get_historical_routing():
    assert parse_query('CAS-3 uncommitted code changes').intent == 'CURRENT_CHANGES'
    assert parse_query('Show current working tree changes for CAS-2').intent == 'CURRENT_CHANGES'


def test_explicit_historical_query_with_qa():
    result = parse_query('For JIRA CAS-3 retrieve historical Git commit and imported QA test cases')
    assert result.intent == 'JIRA_HISTORICAL_IMPACT'
    assert result.include_qa
    assert result.jira_keys == ('CAS-3',)


def test_model_cannot_invent_jira():
    result = parse_query('Show email validation changes', model=lambda _: '{"intent":"JIRA_HISTORICAL_IMPACT","include_qa":true}')
    assert result.intent == 'GENERAL'


def test_ordinary_questions_preserved():
    assert parse_query('What is the status of CAS-3?').intent == 'GENERAL'
    assert parse_query('Compare release V1 and V2').intent == 'GENERAL'
