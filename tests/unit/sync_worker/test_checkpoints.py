from threadbare.sync_worker.checkpoints import BackfillProgress, advance_backfill_progress


def test_full_batch_is_not_complete():
    progress = advance_backfill_progress(batch_message_ids=[1, 2, 3], requested_limit=3)

    assert progress == BackfillProgress(last_message_id=3, complete=False)


def test_partial_batch_is_complete():
    progress = advance_backfill_progress(batch_message_ids=[1, 2], requested_limit=3)

    assert progress == BackfillProgress(last_message_id=2, complete=True)


def test_empty_batch_is_complete_with_no_new_checkpoint():
    progress = advance_backfill_progress(batch_message_ids=[], requested_limit=3)

    assert progress == BackfillProgress(last_message_id=None, complete=True)


def test_checkpoint_is_the_max_id_regardless_of_batch_order():
    # discord.py returns oldest_first batches in ascending order, but the
    # checkpoint should be robust to a batch not being perfectly sorted.
    progress = advance_backfill_progress(batch_message_ids=[5, 3, 4], requested_limit=3)

    assert progress.last_message_id == 5
