"""Every POST form starts with a hidden CSRF input, so form layout CSS must not place fields by position."""
import re
from pathlib import Path

import whistapp

CSS = (Path(whistapp.__file__).parent / "static" / "style.css").read_text(encoding="utf-8")


def test_form_children_are_not_placed_by_child_position():
    # :first-child / :nth-child count the hidden CSRF input; :first-of-type / :nth-of-type do not.
    assert not re.findall(r"form\s*>\s*[\w.-]*:(?:first|nth|last)-child", CSS)
    assert ".setup-form>label:first-of-type{grid-column:1/-1}" in CSS
