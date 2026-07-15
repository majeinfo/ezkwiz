from django.contrib.auth import get_user_model
from django.test import TestCase

from .forms import ChoiceFormSet
from .models import Question, Quiz

User = get_user_model()


def formset_data(prefix, forms):
    data = {
        f'{prefix}-TOTAL_FORMS': str(len(forms)),
        f'{prefix}-INITIAL_FORMS': '0',
        f'{prefix}-MIN_NUM_FORMS': '0',
        f'{prefix}-MAX_NUM_FORMS': '4',
    }
    for i, (text, is_correct, order) in enumerate(forms):
        data[f'{prefix}-{i}-text'] = text
        data[f'{prefix}-{i}-order'] = str(order)
        if is_correct:
            data[f'{prefix}-{i}-is_correct'] = 'on'
    return data


class ChoiceFormSetTests(TestCase):
    def setUp(self):
        owner = User.objects.create_user(username='creator', password='pw')
        quiz = Quiz.objects.create(owner=owner, title='Geography')
        self.question = Question.objects.create(quiz=quiz, text='Capitals?', order=0)
        self.prefix = ChoiceFormSet(instance=self.question).prefix

    def test_valid_with_two_choices_one_correct(self):
        data = formset_data(self.prefix, [
            ('Paris', True, 0),
            ('Berlin', False, 1),
        ])
        formset = ChoiceFormSet(data, instance=self.question)
        self.assertTrue(formset.is_valid(), formset.errors)

    def test_rejects_single_choice(self):
        data = formset_data(self.prefix, [('Paris', True, 0)])
        formset = ChoiceFormSet(data, instance=self.question)
        self.assertFalse(formset.is_valid())

    def test_rejects_more_than_four_choices(self):
        data = formset_data(self.prefix, [
            ('A', True, 0), ('B', False, 1), ('C', False, 2), ('D', False, 3), ('E', False, 4),
        ])
        formset = ChoiceFormSet(data, instance=self.question)
        self.assertFalse(formset.is_valid())

    def test_requires_at_least_one_correct_choice(self):
        data = formset_data(self.prefix, [
            ('Paris', False, 0),
            ('Berlin', False, 1),
        ])
        formset = ChoiceFormSet(data, instance=self.question)
        self.assertFalse(formset.is_valid())

    def test_allows_multiple_correct_choices(self):
        data = formset_data(self.prefix, [
            ('Paris', True, 0),
            ('Berlin', True, 1),
            ('London', False, 2),
        ])
        formset = ChoiceFormSet(data, instance=self.question)
        self.assertTrue(formset.is_valid(), formset.errors)
