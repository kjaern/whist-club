from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
def test_touch_seating_has_no_duplicate_arrows():
    html = (ROOT / "src/whistapp/templates/partials/new_hand_area.html").read_text(encoding="utf-8")
    assert "pointerdown" in html and "pointermove" in html
    assert "setPointerCapture" in html
    assert "seat-up" not in html and "seat-down" not in html
    assert 'form="seating-order-form"' in html
    assert 'id="seating-order-form"' in html
    assert "form.requestSubmit()" in html

def test_touch_handle_disables_scroll_only_on_handle():
    css = (ROOT / "src/whistapp/static/style.css").read_text(encoding="utf-8")
    assert "touch-action:none" in css

def test_home_drag_uses_row_midpoints_and_fixed_remove_width():
    home=(ROOT / 'src/whistapp/templates/home.html').read_text(encoding='utf-8')
    css=(ROOT / 'src/whistapp/static/style.css').read_text(encoding='utf-8')
    assert 'function moveDrag(e)' in home and "document.addEventListener('pointermove'" in home
    assert 'getBoundingClientRect().height/2' in home
    assert 'setPointerCapture' in home
    assert 'button.seat-remove' in css and 'grid-template-columns:minmax(0,1fr) 66px 30px' in css
    assert 'width:30px; min-width:0; max-width:30px' in css
    assert 'seat-controls' in home and 'seat-move' in home
    assert '@media (pointer:coarse)' in css


def test_mobile_button_selector_and_cache_busting():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    css = (root / 'src/whistapp/static/style.css').read_text(encoding='utf-8')
    base = (root / 'src/whistapp/templates/base.html').read_text(encoding='utf-8')
    assert '.setup-form #selected-seats button.seat-move' in css
    assert 'width:30px !important' in css
    assert "static_url('style.css')" in base  # versioned by file time; see test_layout
