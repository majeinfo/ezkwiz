import os
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

from .forms import ChoiceFormSet, QuestionForm
from .models import MAX_MEDIA_UPLOAD_SIZE, Question, Quiz, media_kind_for_name, validate_media_file_size

User = get_user_model()

TEST_MEDIA_ROOT = tempfile.mkdtemp()


def small_gif():
    """A minimal valid 1x1 transparent GIF, small enough to upload instantly in tests."""
    return SimpleUploadedFile(
        'pixel.gif',
        b'GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x01\x00\x00\x00\x00,'
        b'\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x01D\x00;',
        content_type='image/gif',
    )


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


class MediaKindTests(TestCase):
    def test_detects_image_audio_video_case_insensitively(self):
        self.assertEqual(media_kind_for_name('photo.PNG'), 'image')
        self.assertEqual(media_kind_for_name('clip.mp3'), 'audio')
        self.assertEqual(media_kind_for_name('movie.mp4'), 'video')

    def test_unrecognized_extension_returns_none(self):
        self.assertIsNone(media_kind_for_name('document.pdf'))


class MediaFileSizeValidatorTests(TestCase):
    def test_rejects_file_over_limit(self):
        oversized = type('F', (), {'size': MAX_MEDIA_UPLOAD_SIZE + 1})()
        with self.assertRaises(ValidationError):
            validate_media_file_size(oversized)

    def test_accepts_file_within_limit(self):
        fine = type('F', (), {'size': 1024})()
        validate_media_file_size(fine)  # should not raise


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class QuestionMediaTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        owner = User.objects.create_user(username='mediauser', password='pw')
        self.quiz = Quiz.objects.create(owner=owner, title='Media Quiz')

    def test_question_with_image_reports_image_kind(self):
        question = Question.objects.create(
            quiz=self.quiz, text='Q', order=0, media=small_gif(),
        )
        self.assertEqual(question.media_kind, 'image')

    def test_question_without_media_has_no_kind(self):
        question = Question.objects.create(quiz=self.quiz, text='Q', order=0)
        self.assertFalse(question.media)
        self.assertIsNone(question.media_kind)

    def test_question_form_rejects_disallowed_extension(self):
        bad_file = SimpleUploadedFile(
            'malware.exe', b'not really an exe', content_type='application/octet-stream',
        )
        form = QuestionForm(
            data={'text': 'Q', 'order': 0, 'time_limit_seconds': 20},
            files={'media': bad_file},
        )
        self.assertFalse(form.is_valid())
        self.assertIn('media', form.errors)

    def test_question_form_accepts_valid_image(self):
        form = QuestionForm(
            data={'text': 'Q', 'order': 0, 'time_limit_seconds': 20},
            files={'media': small_gif()},
        )
        self.assertTrue(form.is_valid(), form.errors)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class QuestionMediaCleanupTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.owner = User.objects.create_user(username='cleanupuser', password='pw')
        self.quiz = Quiz.objects.create(owner=self.owner, title='Cleanup Quiz')
        self.client = Client()
        self.client.force_login(self.owner)

    def test_deleting_question_removes_media_file_from_disk(self):
        question = Question.objects.create(quiz=self.quiz, text='Q', order=0, media=small_gif())
        path = question.media.path
        self.assertTrue(os.path.exists(path))

        self.client.post(f'/quizzes/{self.quiz.pk}/questions/{question.pk}/delete/')

        self.assertFalse(os.path.exists(path))

    def test_replacing_media_removes_old_file_from_disk(self):
        question = Question.objects.create(quiz=self.quiz, text='Q', order=0, media=small_gif())
        old_path = question.media.path
        self.assertTrue(os.path.exists(old_path))

        prefix = ChoiceFormSet(instance=question).prefix
        data = {
            'text': 'Q updated', 'order': '0', 'time_limit_seconds': '20',
            'media': SimpleUploadedFile('new.gif', small_gif().read(), content_type='image/gif'),
            f'{prefix}-TOTAL_FORMS': '2', f'{prefix}-INITIAL_FORMS': '0',
            f'{prefix}-MIN_NUM_FORMS': '0', f'{prefix}-MAX_NUM_FORMS': '4',
            f'{prefix}-0-text': 'A', f'{prefix}-0-is_correct': 'on', f'{prefix}-0-order': '0',
            f'{prefix}-1-text': 'B', f'{prefix}-1-order': '0',
        }
        response = self.client.post(f'/quizzes/{self.quiz.pk}/questions/{question.pk}/edit/', data)
        self.assertEqual(response.status_code, 302, getattr(response, 'content', b'')[:2000])

        question.refresh_from_db()
        self.assertFalse(os.path.exists(old_path))
        self.assertTrue(os.path.exists(question.media.path))
        self.assertNotEqual(question.media.name, '')


class QuestionReorderTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='reorderer', password='pw')
        self.quiz = Quiz.objects.create(owner=self.owner, title='Reorder Quiz')
        self.q0 = Question.objects.create(quiz=self.quiz, text='Q0', order=0)
        self.q1 = Question.objects.create(quiz=self.quiz, text='Q1', order=1)
        self.q2 = Question.objects.create(quiz=self.quiz, text='Q2', order=2)
        self.client = Client()
        self.client.force_login(self.owner)

    def _ordered_texts(self):
        return list(self.quiz.questions.order_by('order', 'id').values_list('text', flat=True))

    def test_move_up_swaps_with_previous(self):
        self.client.post(f'/quizzes/{self.quiz.pk}/questions/{self.q1.pk}/move-up/')
        self.assertEqual(self._ordered_texts(), ['Q1', 'Q0', 'Q2'])

    def test_move_down_swaps_with_next(self):
        self.client.post(f'/quizzes/{self.quiz.pk}/questions/{self.q1.pk}/move-down/')
        self.assertEqual(self._ordered_texts(), ['Q0', 'Q2', 'Q1'])

    def test_move_up_first_question_is_a_noop(self):
        self.client.post(f'/quizzes/{self.quiz.pk}/questions/{self.q0.pk}/move-up/')
        self.assertEqual(self._ordered_texts(), ['Q0', 'Q1', 'Q2'])

    def test_move_down_last_question_is_a_noop(self):
        self.client.post(f'/quizzes/{self.quiz.pk}/questions/{self.q2.pk}/move-down/')
        self.assertEqual(self._ordered_texts(), ['Q0', 'Q1', 'Q2'])

    def test_move_renormalizes_order_to_sequential_values(self):
        # Force a tie/gap to make sure moving cleans it up rather than just
        # swapping two already-messy values.
        Question.objects.filter(pk=self.q0.pk).update(order=5)
        Question.objects.filter(pk=self.q1.pk).update(order=5)
        Question.objects.filter(pk=self.q2.pk).update(order=99)

        self.client.post(f'/quizzes/{self.quiz.pk}/questions/{self.q0.pk}/move-down/')

        orders = list(self.quiz.questions.order_by('order', 'id').values_list('order', flat=True))
        self.assertEqual(orders, [0, 1, 2])

    def test_get_request_does_not_move(self):
        self.client.get(f'/quizzes/{self.quiz.pk}/questions/{self.q1.pk}/move-up/')
        self.assertEqual(self._ordered_texts(), ['Q0', 'Q1', 'Q2'])

    def test_non_owner_cannot_move_questions(self):
        User.objects.create_user(username='someone_else', password='pw')
        other_client = Client()
        other_client.force_login(User.objects.get(username='someone_else'))
        response = other_client.post(f'/quizzes/{self.quiz.pk}/questions/{self.q1.pk}/move-up/')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self._ordered_texts(), ['Q0', 'Q1', 'Q2'])
