"""The source a generated script needs is built once per kind of Flow, not on every save."""
from app import flow_portable


def test_every_way_of_asking_shares_one_entry_per_kind_of_flow():
    flow_portable._execution_sources.cache_clear()
    # A recorded Flow's save asks in all three ways, several times over.
    for _ in range(3):
        default = flow_portable.execution_sources()
        recorded = flow_portable.execution_sources("recorded")
        catalog = flow_portable.execution_sources("catalog")
        flow_portable.execution_hash()
    assert default is recorded
    assert catalog is not recorded and set(catalog) != set(recorded)
    built = flow_portable._execution_sources.cache_info()
    assert built.misses == 2, built  # one build for recorded, one for catalog
    assert built.hits == 10, built


def test_the_kept_result_is_the_same_content_a_fresh_build_gives():
    kept = flow_portable.execution_sources("recorded")
    fresh = flow_portable._execution_sources.__wrapped__("recorded")
    assert kept == fresh
    assert flow_portable.execution_hash() == flow_portable.flow_recording.digest(fresh)
