"""Older automatically added page metadata is no longer an execution gate."""
import pytest
from app import flow_recording
from test_flow_recordings import definition


@pytest.mark.parametrize('metadata', [
    {'identity': {}, 'readiness': {}},
    {'identity': {'text': 'GSCM'}},
    {'identity': {'kind': 'page_title', 'target': {'page': 'missing'}, 'text': 'Old title'}},
    {'readiness': {'trigger_step_id': 'removed-step', 'mode': 'changed_text', 'target': {'page': 'missing'}}},
])
def test_legacy_page_metadata_does_not_block_recording(metadata):
    value=definition()
    value.update(metadata)
    assert flow_recording.validate_definition(value) is value
