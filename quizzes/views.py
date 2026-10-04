from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.storage import default_storage
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from games.models import GameSession
from .forms import ChoiceFormSet, QuestionForm, QuizForm
from .models import Question, Quiz


class QuizListView(LoginRequiredMixin, ListView):
    template_name = 'quizzes/quiz_list.html'
    context_object_name = 'quizzes'

    def get_queryset(self):
        return Quiz.objects.filter(owner=self.request.user)


class QuizCreateView(LoginRequiredMixin, CreateView):
    model = Quiz
    form_class = QuizForm
    template_name = 'quizzes/quiz_form.html'

    def form_valid(self, form):
        form.instance.owner = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('quizzes:detail', args=[self.object.pk])


class QuizDetailView(LoginRequiredMixin, DetailView):
    template_name = 'quizzes/quiz_detail.html'
    context_object_name = 'quiz'

    def get_queryset(self):
        return Quiz.objects.filter(owner=self.request.user)


class QuizUpdateView(LoginRequiredMixin, UpdateView):
    form_class = QuizForm
    template_name = 'quizzes/quiz_form.html'

    def get_queryset(self):
        return Quiz.objects.filter(owner=self.request.user)

    def get_success_url(self):
        return reverse('quizzes:detail', args=[self.object.pk])


class QuizDeleteView(LoginRequiredMixin, DeleteView):
    template_name = 'quizzes/quiz_confirm_delete.html'
    success_url = reverse_lazy('quizzes:list')

    def get_queryset(self):
        return Quiz.objects.filter(owner=self.request.user)

    def form_valid(self, form):
        for question in self.object.questions.exclude(media=''):
            default_storage.delete(question.media.name)
        return super().form_valid(form)


def _get_owned_quiz(request, quiz_pk):
    return get_object_or_404(Quiz, pk=quiz_pk, owner=request.user)


@login_required
def question_create(request, quiz_pk):
    quiz = _get_owned_quiz(request, quiz_pk)
    question = Question(quiz=quiz, order=quiz.questions.count())

    if request.method == 'POST':
        form = QuestionForm(request.POST, request.FILES, instance=question)
        if form.is_valid():
            unsaved_question = form.save(commit=False)
            formset = ChoiceFormSet(request.POST, instance=unsaved_question)
            if formset.is_valid():
                unsaved_question.save()
                formset.instance = unsaved_question
                formset.save()
                messages.success(request, 'Question added.')
                return redirect('quizzes:detail', pk=quiz.pk)
        else:
            formset = ChoiceFormSet(request.POST, instance=question)
    else:
        form = QuestionForm(instance=question)
        formset = ChoiceFormSet(instance=question)

    return render(request, 'quizzes/question_form.html', {
        'quiz': quiz, 'form': form, 'formset': formset,
    })


@login_required
def question_edit(request, quiz_pk, pk):
    quiz = _get_owned_quiz(request, quiz_pk)
    question = get_object_or_404(Question, pk=pk, quiz=quiz)
    old_media_name = question.media.name if question.media else ''

    if request.method == 'POST':
        form = QuestionForm(request.POST, request.FILES, instance=question)
        formset = ChoiceFormSet(request.POST, instance=question)
        if form.is_valid() and formset.is_valid():
            form.save()
            formset.save()
            new_media_name = question.media.name if question.media else ''
            if old_media_name and old_media_name != new_media_name:
                default_storage.delete(old_media_name)
            messages.success(request, 'Question updated.')
            return redirect('quizzes:detail', pk=quiz.pk)
    else:
        form = QuestionForm(instance=question)
        formset = ChoiceFormSet(instance=question)

    return render(request, 'quizzes/question_form.html', {
        'quiz': quiz, 'form': form, 'formset': formset, 'question': question,
    })


@login_required
def question_delete(request, quiz_pk, pk):
    quiz = _get_owned_quiz(request, quiz_pk)
    question = get_object_or_404(Question, pk=pk, quiz=quiz)
    if request.method == 'POST':
        media_name = question.media.name if question.media else ''
        question.delete()
        if media_name:
            default_storage.delete(media_name)
        messages.success(request, 'Question deleted.')
    return redirect('quizzes:detail', pk=quiz.pk)


@login_required
def question_move(request, quiz_pk, pk, direction):
    quiz = _get_owned_quiz(request, quiz_pk)
    question = get_object_or_404(Question, pk=pk, quiz=quiz)
    if request.method != 'POST':
        return redirect('quizzes:detail', pk=quiz.pk)

    questions = list(quiz.questions.all())
    index = questions.index(question)
    swap_with = index - 1 if direction == 'up' else index + 1

    if 0 <= swap_with < len(questions):
        questions[index], questions[swap_with] = questions[swap_with], questions[index]
        # Renormalize everyone's order to their new sequential position,
        # rather than just swapping the two `order` values -- this also
        # cleans up any ties/gaps that may have crept in over time, so
        # moves always have a visible effect.
        for position, q in enumerate(questions):
            if q.order != position:
                q.order = position
                q.save(update_fields=['order'])

    return redirect('quizzes:detail', pk=quiz.pk)


@login_required
def publish_quiz(request, pk):
    quiz = _get_owned_quiz(request, pk)
    if request.method != 'POST':
        return redirect('quizzes:detail', pk=quiz.pk)

    if not quiz.questions.exists():
        messages.error(request, 'Add at least one question before publishing.')
        return redirect('quizzes:detail', pk=quiz.pk)

    session = GameSession.objects.create(quiz=quiz)
    return redirect('games:host', host_token=session.host_token)


@login_required
def session_delete(request, quiz_pk, pk):
    quiz = _get_owned_quiz(request, quiz_pk)
    session = get_object_or_404(GameSession, pk=pk, quiz=quiz)
    if request.method == 'POST':
        session.delete()
        messages.success(request, 'Game session deleted.')
    return redirect('quizzes:detail', pk=quiz.pk)
