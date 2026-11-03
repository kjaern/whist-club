def test_editor_has_sse_and_refresh_form():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / 'src' / 'whistapp' / 'templates'
    assert 'EventSource' in (root / 'session.html').read_text(encoding='utf-8')
    assert 'rotation-notice' not in (root / 'partials' / 'session_content.html').read_text(encoding='utf-8')
    assert 'editor/new-hand' in (root / 'session.html').read_text(encoding='utf-8')
    assert 'origin_client_id' in (root / 'partials' / 'new_hand_area.html').read_text(encoding='utf-8')
    assert 'id="editor-results"' in (root / 'partials' / 'editor_results.html').read_text(encoding='utf-8')
