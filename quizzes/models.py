import os

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models

MAX_CHOICES_PER_QUESTION = 4

MEDIA_KIND_EXTENSIONS = {
    'image': ['jpg', 'jpeg', 'png', 'gif', 'webp'],
    'audio': ['mp3', 'wav', 'ogg', 'm4a'],
    'video': ['mp4', 'webm', 'mov', 'ogv'],
}
ALLOWED_MEDIA_EXTENSIONS = [
    ext for extensions in MEDIA_KIND_EXTENSIONS.values() for ext in extensions
]
MAX_MEDIA_UPLOAD_SIZE = 20 * 1024 * 1024  # 20 MB


def media_kind_for_name(name):
    """Returns 'image', 'audio', 'video', or None for an unrecognized extension."""
    ext = os.path.splitext(name)[1].lstrip('.').lower()
    for kind, extensions in MEDIA_KIND_EXTENSIONS.items():
        if ext in extensions:
            return kind
    return None


def validate_media_file_size(file):
    if file.size > MAX_MEDIA_UPLOAD_SIZE:
        raise ValidationError(f'File must be under {MAX_MEDIA_UPLOAD_SIZE // (1024 * 1024)} MB.')


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
    media = models.FileField(
        upload_to='question_media/%Y/%m/',
        blank=True,
        validators=[
            FileExtensionValidator(allowed_extensions=ALLOWED_MEDIA_EXTENSIONS),
            validate_media_file_size,
        ],
        help_text='Optional image, sound, or video shown when this question starts.',
    )

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return self.text

    @property
    def media_kind(self):
        if not self.media:
            return None
        return media_kind_for_name(self.media.name)

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
