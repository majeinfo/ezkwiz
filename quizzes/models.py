from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

MAX_CHOICES_PER_QUESTION = 4


class Quiz(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='quizzes'
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title


class Question(models.Model):
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name='questions')
    text = models.CharField(max_length=500)
    order = models.PositiveIntegerField(default=0)
    time_limit_seconds = models.PositiveIntegerField(default=20)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return self.text

    def clean(self):
        if self.pk and self.choices.count() > MAX_CHOICES_PER_QUESTION:
            raise ValidationError(
                f'A question may have at most {MAX_CHOICES_PER_QUESTION} choices.'
            )


class Choice(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='choices')
    text = models.CharField(max_length=200)
    is_correct = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return self.text
