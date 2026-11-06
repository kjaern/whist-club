"""Vejledning: what each kind of user can do, and where the less obvious features are."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..web import can_create_club, render

router = APIRouter()


@router.get("/guide", response_class=HTMLResponse)
def guide(request: Request):
    return render(request, "guide.html", can_create=can_create_club(request))
