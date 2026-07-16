import random
import string
import uuid

from django.db import models

from quizzes.models import Choice, Question, Quiz

CODE_ALPHABET = ''.join(c for c in string.ascii_uppercase + string.digits if c not in '0O1I')
CODE_LENGTH = 6


def generate_code():
    while True:
        code = ''.join(random.choices(CODE_ALPHABET, k=CODE_LENGTH))
        if not GameSession.objects.filter(code=code).exists():
            return code


class GameSession(models.Model):
    class Status(models.TextChoices):
        LOBBY = 'lobby', 'Lobby'
        ACTIVE = 'active', 'Question active'
        QUESTION_CLOSED = 'question_closed', 'Question closed'
        FINISHED = 'finished', 'Finished'

    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name='sessions')
    code = models.CharField(max_length=CODE_LENGTH, unique=True, default=generate_code)
    host_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.LOBBY)
    current_question_index = models.PositiveIntegerField(default=0)
    current_question_started_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'{self.quiz.title} ({self.code})'

    @property
    def current_question(self):
        questions = list(self.quiz.questions.all())
        if 0 <= self.current_question_index < len(questions):
            return questions[self.current_question_index]
        return None


class Player(models.Model):
    session = models.ForeignKey(GameSession, on_delete=models.CASCADE, related_name='players')
    nickname = models.CharField(max_length=50)
    client_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    score = models.IntegerField(default=0)
    is_connected = models.BooleanField(default=False)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('session', 'nickname')
        ordering = ['-score', 'joined_at']

    def __str__(self):
        return f'{self.nickname} @ {self.session.code}'


class Answer(models.Model):
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name='answers')
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='answers')
    choices = models.ManyToManyField(Choice, blank=True, related_name='answers')
    answered_at = models.DateTimeField(auto_now=True)
    is_correct = models.BooleanField(default=False)
    points_awarded = models.IntegerField(default=0)
    response_time_ms = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ('player', 'question')

    def __str__(self):
        return f'{self.player.nickname} -> {self.question_id}'
