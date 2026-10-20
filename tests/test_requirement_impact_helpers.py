"""Fast isolated checks: no database, Jira credentials or Neo4j required."""
from services.impact.requirement_impact_service import mentions, norm

def test_attribute_variants():
    assert mentions('Update countryCode validation', 'countryCode')
    assert mentions('Update country code validation', 'countryCode')
    assert mentions('Update country_code validation', 'countryCode')
    assert not mentions('Update countyCode validation', 'countryCode')

def test_normalization():
    assert norm('countryCode') == norm('country_code')
