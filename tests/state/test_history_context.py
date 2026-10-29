from slate import history, jsonio
from slate.state import status


def test_planning_history_does_not_change_gates_or_the_next_stage(new_film, tmp_path):
    fd = new_film(request='Make a small product film.', request_source='Test Person')
    before = status.compute(fd)
    source = tmp_path / 'history.json'
    jsonio.write_atomic(source, {'patterns': [{'guidance': 'Keep intentional energy.', 'topics': ['motion'],
                                              'evidence': [{'source': 'history:12'}]}]})
    history.use(source)
    after = status.compute(fd)
    assert (after['ok'], after['next'], after['stages']) == (before['ok'], before['next'], before['stages'])
    assert 'read slate history context' in status.format_human(after)
    source.write_text('{invalid')
    unreadable = status.compute(fd)
    assert (unreadable['ok'], unreadable['next']) == (before['ok'], before['next'])
    assert unreadable['creative_history']['patterns'] == 0
