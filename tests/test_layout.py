from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / 'src' / 'whistapp' / 'templates'


def test_rotation_is_per_night():
    assert 'name="rotation_mode"' in (BASE / 'home.html').read_text(encoding='utf-8')
    assert 'name="rotation_mode"' not in (BASE / 'partials' / 'session_content.html').read_text(encoding='utf-8')


def test_compact_hand_form():
    html = (BASE / 'partials' / 'new_hand_area.html').read_text(encoding='utf-8')
    assert 'hand-fields' in html
    assert 'player-roster' in html
    assert 'name="participants"' in html
    assert 'roster-add' in html
    assert any('Gem hånd og resultat' in p.read_text(encoding='utf-8') for p in BASE.rglob('*.html'))


def test_setup_uses_full_available_width():
    css = (Path(__file__).resolve().parents[1] / 'src' / 'whistapp' / 'static' / 'style.css').read_text(encoding='utf-8')
    assert '.setup-card{max-width:none;width:100%' in css
    assert '.setup-form{width:100%;grid-template-columns:' in css
