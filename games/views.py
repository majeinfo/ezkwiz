from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, render

from .models import GameSession


def play(request, code):
    session = get_object_or_404(GameSession, code=code.upper())
    return render(request, 'games/play.html', {
        'session': session,
        'code': session.code,
    })


@login_required
def host(request, host_token):
    session = get_object_or_404(GameSession, host_token=host_token)
    if session.quiz.owner_id != request.user.id:
        raise Http404
    return render(request, 'games/host.html', {
        'session': session,
    })
