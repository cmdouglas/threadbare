from threadbare.sync_worker.permissions import should_sync


def test_public_and_indexed_channel_should_sync():
    assert should_sync(is_public=True, indexed=True) is True


def test_public_but_not_indexed_channel_should_not_sync():
    assert should_sync(is_public=True, indexed=False) is False


def test_non_public_channel_should_not_sync_even_if_indexed():
    assert should_sync(is_public=False, indexed=True) is False


def test_non_public_non_indexed_should_not_sync():
    assert should_sync(is_public=False, indexed=False) is False
