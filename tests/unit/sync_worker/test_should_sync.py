from threadbare.sync_worker.permissions import should_sync


def test_public_and_indexed_channel_should_sync():
    assert should_sync(is_public=True, indexed=True, visibility_enrolled=False) is True


def test_public_but_not_indexed_channel_should_not_sync():
    assert should_sync(is_public=True, indexed=False, visibility_enrolled=False) is False


def test_non_public_channel_should_not_sync_even_if_indexed():
    assert should_sync(is_public=False, indexed=True, visibility_enrolled=False) is False


def test_non_public_non_indexed_should_not_sync():
    assert should_sync(is_public=False, indexed=False, visibility_enrolled=False) is False


def test_visibility_enrolled_non_public_indexed_channel_should_sync():
    # The Phase 2 case this predicate exists to unblock: a role-gated
    # channel a mod has deliberately enrolled must still be mirrored, or
    # per-user visibility filtering at read time has nothing to filter.
    assert should_sync(is_public=False, indexed=True, visibility_enrolled=True) is True


def test_visibility_enrolled_but_not_indexed_should_not_sync():
    assert should_sync(is_public=False, indexed=False, visibility_enrolled=True) is False
