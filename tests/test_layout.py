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


def test_stylesheet_version_follows_the_file():
    # Regression: a hand-written ?v= tag was not bumped, so phones kept an old style.css.
    import os
    from whistapp.web import ROOT, static_url
    css = ROOT / 'static' / 'style.css'
    assert static_url('style.css') == f'/static/style.css?v={int(css.stat().st_mtime)}'
    stat = css.stat()
    try:
        os.utime(css, (stat.st_atime, stat.st_mtime + 60))
        assert static_url('style.css') == f'/static/style.css?v={int(stat.st_mtime) + 60}'
    finally:
        os.utime(css, (stat.st_atime, stat.st_mtime))
    assert "static_url('style.css')" in (BASE / 'base.html').read_text(encoding='utf-8')
