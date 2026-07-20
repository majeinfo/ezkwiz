from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory

from .models import MAX_CHOICES_PER_QUESTION, Choice, Question, Quiz


class QuizForm(forms.ModelForm):
    class Meta:
        model = Quiz
        fields = ['title', 'description', 'theme', 'auto_close_when_all_answered']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'theme': forms.Select(attrs={'class': 'form-select'}),
            'auto_close_when_all_answered': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class QuestionForm(forms.ModelForm):
    class Meta:
        model = Question
        fields = ['text', 'order', 'time_limit_seconds', 'media']
        widgets = {
            'text': forms.TextInput(attrs={'class': 'form-control'}),
            'order': forms.NumberInput(attrs={'class': 'form-control'}),
            'time_limit_seconds': forms.NumberInput(attrs={'class': 'form-control'}),
            'media': forms.ClearableFileInput(attrs={'class': 'form-control'}),
        }


class BaseChoiceFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return

        live_forms = [
            form for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get('DELETE', False)
        ]

        if len(live_forms) < 2:
            raise forms.ValidationError('A question needs at least 2 choices.')
        if len(live_forms) > MAX_CHOICES_PER_QUESTION:
            raise forms.ValidationError(
                f'A question may have at most {MAX_CHOICES_PER_QUESTION} choices.'
            )
        if not any(form.cleaned_data.get('is_correct') for form in live_forms):
            raise forms.ValidationError('At least one choice must be marked correct.')


ChoiceFormSet = inlineformset_factory(
    Question,
    Choice,
    fields=['text', 'is_correct', 'order'],
    formset=BaseChoiceFormSet,
    extra=4,
    max_num=MAX_CHOICES_PER_QUESTION,
    validate_max=True,
    can_delete=True,
    widgets={
        'text': forms.TextInput(attrs={'class': 'form-control'}),
        'order': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),
        'is_correct': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    },
)
