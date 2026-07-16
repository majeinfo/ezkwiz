"""
Server-authoritative game state machine.

Consumers call into these functions rather than mutating models directly, so
the same rules apply regardless of which socket (host or player) triggered a
transition, and the flow can be unit-tested without a websocket.
"""

import random

from django.db import models, transaction
from django.utils import timezone

from .models import Answer, GameSession, Player

BASE_POINTS = 100
BONUS_POINTS_PER_SECOND = 10


class GameError(Exception):
    """Raised when a requested transition isn't legal given the current state."""


def join_game(session, nickname, client_token=None):
    nickname = nickname.strip()
    if not nickname:
        raise GameError('Nickname is required.')

    if client_token:
        player = session.players.filter(client_token=client_token).first()
        if player:
            player.is_connected = True
            player.save(update_fields=['is_connected'])
            return player

    if session.players.filter(nickname__iexact=nickname).exists():
        raise GameError('That nickname is already taken in this game.')

    return session.players.create(nickname=nickname, is_connected=True)


def start_game(session):
    if session.status != GameSession.Status.LOBBY:
        raise GameError('Game has already started.')
    if not session.quiz.questions.exists():
        raise GameError('Quiz has no questions.')

    session.status = GameSession.Status.ACTIVE
    session.current_question_index = 0
    session.started_at = timezone.now()
    session.current_question_started_at = session.started_at
    session.save(update_fields=[
        'status', 'current_question_index', 'started_at', 'current_question_started_at',
    ])
    return session


def _shuffled_choices(session, question):
    """
    Randomize choice order per (session, question) rather than per request, so
    the host and every player see the same layout, and a reconnecting player's
    order doesn't jump around.
    """
    choices = list(question.choices.all())
    random.Random(f'{session.id}:{question.id}').shuffle(choices)
    return choices


def _remaining_seconds(session, question):
    if not session.current_question_started_at:
        return question.time_limit_seconds
    elapsed = (timezone.now() - session.current_question_started_at).total_seconds()
    return max(0.0, question.time_limit_seconds - elapsed)


def question_payload(session, question, *, reveal_correct=False):
    payload = {
        'id': question.id,
        'text': question.text,
        'time_limit_seconds': question.time_limit_seconds,
        'remaining_seconds': round(_remaining_seconds(session, question), 1),
        'choices': [
            {
                'id': choice.id,
                'text': choice.text,
                **({'is_correct': choice.is_correct} if reveal_correct else {}),
            }
            for choice in _shuffled_choices(session, question)
        ],
    }
    if question.media:
        payload['media_url'] = question.media.url
        payload['media_kind'] = question.media_kind
    return payload


def compute_points(question, response_time_ms):
    """100 base points for a correct answer, plus a speed bonus of 10 points
    per second remaining before the question's time limit."""
    response_seconds = response_time_ms / 1000
    bonus = max(0.0, (question.time_limit_seconds - response_seconds) * BONUS_POINTS_PER_SECOND)
    return BASE_POINTS + round(bonus)


def leaderboard_payload(session):
    return [
        {'nickname': p.nickname, 'score': p.score}
        for p in session.players.order_by('-score', 'joined_at')
    ]


def current_state_payload(session):
    """What to replay to a (re)connecting player so their screen matches the game in progress."""
    session.refresh_from_db()
    if session.status == GameSession.Status.ACTIVE:
        return {
            'type': 'question_started',
            'question': question_payload(session, session.current_question),
        }
    if session.status == GameSession.Status.QUESTION_CLOSED:
        return {
            'type': 'question_closed',
            'question': question_payload(session, session.current_question, reveal_correct=True),
            'leaderboard': leaderboard_payload(session),
        }
    if session.status == GameSession.Status.FINISHED:
        return {'type': 'game_ended', 'leaderboard': leaderboard_payload(session)}
    return None


@transaction.atomic
def submit_answer(session, player, question, choice_ids, response_time_ms=0):
    if session.status != GameSession.Status.ACTIVE:
        raise GameError('This question is not open for answers.')
    if session.current_question != question:
        raise GameError('That is not the current question.')

    correct_ids = set(question.choices.filter(is_correct=True).values_list('id', flat=True))
    submitted_ids = set(choice_ids) & set(question.choices.values_list('id', flat=True))
    is_correct = submitted_ids == correct_ids and bool(submitted_ids)
    points = compute_points(question, response_time_ms) if is_correct else 0

    previous_points = Answer.objects.filter(
        player=player, question=question
    ).values_list('points_awarded', flat=True).first() or 0

    answer, _ = Answer.objects.update_or_create(
        player=player, question=question,
        defaults={
            'is_correct': is_correct,
            'points_awarded': points,
            'response_time_ms': response_time_ms,
        },
    )
    answer.choices.set(submitted_ids)

    delta = points - previous_points
    if delta:
        Player.objects.filter(pk=player.pk).update(score=models.F('score') + delta)
        player.refresh_from_db(fields=['score'])

    return answer


def answer_counts(session, question):
    answered = Answer.objects.filter(player__session=session, question=question).count()
    total = session.players.count()
    return {'answered': answered, 'total': total}


def all_players_answered(session, question):
    counts = answer_counts(session, question)
    return counts['total'] > 0 and counts['answered'] >= counts['total']


@transaction.atomic
def close_question(session):
    if session.status != GameSession.Status.ACTIVE:
        raise GameError('No question is currently open.')

    question = session.current_question
    session.status = GameSession.Status.QUESTION_CLOSED
    session.save(update_fields=['status'])

    return {
        'question': question_payload(session, question, reveal_correct=True),
        'leaderboard': leaderboard_payload(session),
    }


@transaction.atomic
def next_question(session):
    if session.status != GameSession.Status.QUESTION_CLOSED:
        raise GameError('Current question has not been closed yet.')

    session.current_question_index += 1
    total_questions = session.quiz.questions.count()

    if session.current_question_index >= total_questions:
        return {'finished': True, **end_game(session)}

    session.status = GameSession.Status.ACTIVE
    session.current_question_started_at = timezone.now()
    session.save(update_fields=[
        'status', 'current_question_index', 'current_question_started_at',
    ])
    return {
        'finished': False,
        'question': question_payload(session, session.current_question),
    }


@transaction.atomic
def end_game(session):
    session.status = GameSession.Status.FINISHED
    session.ended_at = timezone.now()
    session.save(update_fields=['status', 'ended_at'])
    return {'leaderboard': leaderboard_payload(session)}
