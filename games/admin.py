from django.contrib import admin

from .models import Answer, GameSession, Player


class PlayerInline(admin.TabularInline):
    model = Player
    extra = 0
    readonly_fields = ('client_token', 'joined_at')


@admin.register(GameSession)
class GameSessionAdmin(admin.ModelAdmin):
    list_display = ('code', 'quiz', 'status', 'current_question_index', 'created_at')
    list_filter = ('status', 'quiz')
    readonly_fields = ('code', 'host_token', 'created_at', 'started_at', 'ended_at')
    inlines = [PlayerInline]


@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    list_display = ('nickname', 'session', 'score', 'is_connected', 'joined_at')
    list_filter = ('session',)
    readonly_fields = ('client_token', 'joined_at')


@admin.register(Answer)
class AnswerAdmin(admin.ModelAdmin):
    list_display = ('player', 'question', 'is_correct', 'points_awarded', 'answered_at')
    list_filter = ('is_correct',)
