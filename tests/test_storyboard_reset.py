from session.storyboard import Storyboard


def test_storyboard_reset_cancels_pending_bursts(tmp_path):
    story = Storyboard(feed_dir=str(tmp_path), offline=True)
    story.bursts = [{"label": "old event", "generation": story.generation}]
    story.count = 3

    story.reset()

    assert story.bursts == []
    assert story.count == 0
    assert story.generation == 1
