from pathlib import Path
BASE=Path(__file__).resolve().parents[1]
def test_cancel_and_gradient():
    html=(BASE/'src/whistapp/templates/partials/editor_results.html').read_text(encoding='utf-8')
    assert 'cancel-edit' in html and "closest('details')" in html
    assert 'score_colors[p.id]' in html
def test_date_default():
    assert '{{default_title}}' in (BASE/'src/whistapp/templates/home.html').read_text(encoding='utf-8')
def test_horizontal_limit_restored():
    assert 'main{max-width:1080px;margin-inline:auto}' in (BASE/'src/whistapp/static/style.css').read_text(encoding='utf-8')
