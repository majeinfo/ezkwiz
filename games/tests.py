import shutil
import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from quizzes.models import Choice, Question, Quiz

from . import services
from .consumers import PlayerConsumer, _close_if_still_on_question
from .models import GameSession

User = get_user_model()

TEST_MEDIA_ROOT = tempfile.mkdtemp()


def small_gif():
    return SimpleUploadedFile(
        'pixel.gif',
        b'GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x01\x00\x00\x00\x00,'
        b'\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x01D\x00;',
        content_type='image/gif',
    )


class GameFlowTests(TestCase):
    def setUp(self):
        owner = User.objects.create_user(username='creator', password='pw')
        self.quiz = Quiz.objects.create(owner=owner, title='Geography')
        self.question = Question.objects.create(quiz=self.quiz, text='Capitals?', order=0)
        self.paris = Choice.objects.create(question=self.question, text='Paris', is_correct=True, order=0)
        self.berlin = Choice.objects.create(question=self.question, text='Berlin', is_correct=True, order=1)
        self.london = Choice.objects.create(question=self.question, text='London', is_correct=False, order=2)
        self.session = GameSession.objects.create(quiz=self.quiz)

    def test_join_creates_player(self):
        player = services.join_game(self.session, 'Alice')
        self.assertEqual(player.nickname, 'Alice')
        self.assertTrue(player.is_connected)

    def test_join_duplicate_nickname_rejected(self):
        services.join_game(self.session, 'Alice')
        with self.assertRaises(services.GameError):
            services.join_game(self.session, 'alice')

    def test_join_with_client_token_resumes_same_player(self):
        player = services.join_game(self.session, 'Alice')
        resumed = services.join_game(self.session, 'Alice', client_token=str(player.client_token))
        self.assertEqual(player.pk, resumed.pk)

    def test_start_game_requires_questions(self):
        empty_quiz = Quiz.objects.create(owner=self.quiz.owner, title='Empty')
        empty_session = GameSession.objects.create(quiz=empty_quiz)
        with self.assertRaises(services.GameError):
            services.start_game(empty_session)

    def test_start_game_sets_active_status(self):
        services.start_game(self.session)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, GameSession.Status.ACTIVE)
        self.assertEqual(self.session.current_question_index, 0)

    def test_submit_answer_full_correct_awards_base_points_plus_speed_bonus(self):
        # time_limit_seconds defaults to 20; answering in 3.6s should award
        # 100 base + (20 - 3.6) * 10 = 264 points.
        services.start_game(self.session)
        player = services.join_game(self.session, 'Alice')
        answer = services.submit_answer(
            self.session, player, self.question,
            [self.paris.id, self.berlin.id], response_time_ms=3600,
        )
        self.assertTrue(answer.is_correct)
        self.assertEqual(answer.points_awarded, 264)
        player.refresh_from_db()
        self.assertEqual(player.score, 264)

    def test_submit_answer_slower_response_awards_smaller_bonus(self):
        services.start_game(self.session)
        player = services.join_game(self.session, 'Alice')
        fast = services.submit_answer(
            self.session, player, self.question,
            [self.paris.id, self.berlin.id], response_time_ms=1000,
        )
        self.assertGreater(
            fast.points_awarded,
            services.compute_points(self.question, response_time_ms=15000),
        )

    def test_submit_answer_response_time_beyond_limit_never_scores_below_base(self):
        services.start_game(self.session)
        player = services.join_game(self.session, 'Alice')
        answer = services.submit_answer(
            self.session, player, self.question,
            [self.paris.id, self.berlin.id], response_time_ms=999_999,
        )
        self.assertEqual(answer.points_awarded, services.BASE_POINTS)

    def test_submit_answer_partial_selection_scores_zero(self):
        services.start_game(self.session)
        player = services.join_game(self.session, 'Alice')
        answer = services.submit_answer(self.session, player, self.question, [self.paris.id])
        self.assertFalse(answer.is_correct)
        self.assertEqual(answer.points_awarded, 0)

    def test_submit_answer_extra_wrong_choice_scores_zero(self):
        services.start_game(self.session)
        player = services.join_game(self.session, 'Alice')
        answer = services.submit_answer(
            self.session, player, self.question,
            [self.paris.id, self.berlin.id, self.london.id],
        )
        self.assertFalse(answer.is_correct)
        self.assertEqual(answer.points_awarded, 0)

    def test_resubmission_adjusts_score_delta_instead_of_stacking(self):
        services.start_game(self.session)
        player = services.join_game(self.session, 'Alice')
        services.submit_answer(self.session, player, self.question, [self.paris.id])
        services.submit_answer(
            self.session, player, self.question,
            [self.paris.id, self.berlin.id], response_time_ms=3600,
        )
        player.refresh_from_db()
        self.assertEqual(player.score, 264)

    def test_full_question_cycle_ends_game_after_last_question(self):
        services.start_game(self.session)
        player = services.join_game(self.session, 'Alice')
        services.submit_answer(
            self.session, player, self.question,
            [self.paris.id, self.berlin.id], response_time_ms=3600,
        )

        result = services.close_question(self.session)
        self.assertEqual(result['leaderboard'][0]['score'], 264)

        next_result = services.next_question(self.session)
        self.assertTrue(next_result['finished'])
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, GameSession.Status.FINISHED)

    def test_answer_rejected_when_question_not_open(self):
        player = services.join_game(self.session, 'Alice')
        with self.assertRaises(services.GameError):
            services.submit_answer(self.session, player, self.question, [self.paris.id])

    def test_question_payload_choice_order_is_deterministic_per_session(self):
        payload1 = services.question_payload(self.session, self.question)
        payload2 = services.question_payload(self.session, self.question)
        self.assertEqual(
            [c['id'] for c in payload1['choices']],
            [c['id'] for c in payload2['choices']],
        )
        self.assertEqual(
            {c['id'] for c in payload1['choices']},
            {self.paris.id, self.berlin.id, self.london.id},
        )

    def test_question_payload_omits_media_fields_when_no_file_attached(self):
        payload = services.question_payload(self.session, self.question)
        self.assertNotIn('media_url', payload)
        self.assertNotIn('media_kind', payload)

    def test_remaining_seconds_near_full_limit_right_after_start(self):
        services.start_game(self.session)
        payload = services.question_payload(self.session, self.question)
        self.assertAlmostEqual(
            payload['remaining_seconds'], self.question.time_limit_seconds, delta=1,
        )

    def test_remaining_seconds_decreases_as_time_elapses(self):
        services.start_game(self.session)
        self.session.current_question_started_at = timezone.now() - timedelta(seconds=5)
        self.session.save(update_fields=['current_question_started_at'])

        payload = services.question_payload(self.session, self.question)
        self.assertAlmostEqual(
            payload['remaining_seconds'], self.question.time_limit_seconds - 5, delta=1,
        )

    def test_remaining_seconds_clamped_to_zero_when_expired(self):
        services.start_game(self.session)
        self.session.current_question_started_at = timezone.now() - timedelta(seconds=1000)
        self.session.save(update_fields=['current_question_started_at'])

        payload = services.question_payload(self.session, self.question)
        self.assertEqual(payload['remaining_seconds'], 0)

    def test_all_players_answered_false_with_no_players(self):
        services.start_game(self.session)
        self.assertFalse(services.all_players_answered(self.session, self.question))

    def test_all_players_answered_false_when_some_unanswered(self):
        services.start_game(self.session)
        alice = services.join_game(self.session, 'Alice')
        services.join_game(self.session, 'Bob')
        services.submit_answer(self.session, alice, self.question, [self.paris.id, self.berlin.id])
        self.assertFalse(services.all_players_answered(self.session, self.question))

    def test_all_players_answered_true_when_everyone_has_answered(self):
        services.start_game(self.session)
        alice = services.join_game(self.session, 'Alice')
        bob = services.join_game(self.session, 'Bob')
        services.submit_answer(self.session, alice, self.question, [self.paris.id, self.berlin.id])
        services.submit_answer(self.session, bob, self.question, [self.paris.id])
        self.assertTrue(services.all_players_answered(self.session, self.question))


class AutoCloseRaceSafetyTests(TestCase):
    """
    _close_if_still_on_question backs both the timeout timer and the
    all-answered shortcut. It must be a safe no-op whenever the game has
    already moved on by the time it runs, since a stale timer for an earlier
    question should never reach in and close whatever is active now.
    """

    def setUp(self):
        owner = User.objects.create_user(username='autocloser', password='pw')
        quiz = Quiz.objects.create(owner=owner, title='Autoclose Quiz')
        self.q1 = Question.objects.create(quiz=quiz, text='Q1', order=0, time_limit_seconds=5)
        Choice.objects.create(question=self.q1, text='A', is_correct=True, order=0)
        Choice.objects.create(question=self.q1, text='B', is_correct=False, order=1)
        self.q2 = Question.objects.create(quiz=quiz, text='Q2', order=1, time_limit_seconds=5)
        Choice.objects.create(question=self.q2, text='C', is_correct=True, order=0)
        Choice.objects.create(question=self.q2, text='D', is_correct=False, order=1)
        self.session = GameSession.objects.create(quiz=quiz)

    def test_closes_when_still_active_on_the_matching_question(self):
        services.start_game(self.session)
        result = _close_if_still_on_question(self.session.id, self.q1.id)
        self.assertIsNotNone(result)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, GameSession.Status.QUESTION_CLOSED)

    def test_noop_when_question_already_closed(self):
        services.start_game(self.session)
        services.close_question(self.session)
        result = _close_if_still_on_question(self.session.id, self.q1.id)
        self.assertIsNone(result)

    def test_noop_when_game_has_moved_to_a_later_question(self):
        services.start_game(self.session)
        services.close_question(self.session)
        services.next_question(self.session)  # now on q2

        result = _close_if_still_on_question(self.session.id, self.q1.id)

        self.assertIsNone(result)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, GameSession.Status.ACTIVE)
        self.assertEqual(self.session.current_question, self.q2)

    def test_noop_when_session_no_longer_exists(self):
        result = _close_if_still_on_question(999999, self.q1.id)
        self.assertIsNone(result)


class AutoCloseOnAllAnsweredTests(TestCase):
    def setUp(self):
        owner = User.objects.create_user(username='allanswered', password='pw')
        self.quiz = Quiz.objects.create(
            owner=owner, title='All Answered Quiz', auto_close_when_all_answered=True,
        )
        self.question = Question.objects.create(quiz=self.quiz, text='Q', order=0)
        self.correct = Choice.objects.create(question=self.question, text='A', is_correct=True, order=0)
        Choice.objects.create(question=self.question, text='B', is_correct=False, order=1)
        self.session = GameSession.objects.create(quiz=self.quiz)
        services.start_game(self.session)

    def _consumer_for(self, player):
        consumer = PlayerConsumer()
        consumer.session = self.session
        consumer.player = player
        return consumer

    def test_auto_closes_once_the_last_player_answers(self):
        alice = services.join_game(self.session, 'Alice')
        consumer = self._consumer_for(alice)

        result = consumer._submit_answer_and_maybe_close(self.question.id, [self.correct.id], 1000)

        self.assertIsNotNone(result)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, GameSession.Status.QUESTION_CLOSED)

    def test_does_not_auto_close_when_quiz_setting_disabled(self):
        self.quiz.auto_close_when_all_answered = False
        self.quiz.save(update_fields=['auto_close_when_all_answered'])
        alice = services.join_game(self.session, 'Alice')
        consumer = self._consumer_for(alice)

        result = consumer._submit_answer_and_maybe_close(self.question.id, [self.correct.id], 1000)

        self.assertIsNone(result)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, GameSession.Status.ACTIVE)

    def test_does_not_auto_close_while_a_player_is_still_missing(self):
        alice = services.join_game(self.session, 'Alice')
        services.join_game(self.session, 'Bob')  # Bob never answers
        consumer = self._consumer_for(alice)

        result = consumer._submit_answer_and_maybe_close(self.question.id, [self.correct.id], 1000)

        self.assertIsNone(result)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, GameSession.Status.ACTIVE)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class QuestionMediaPayloadTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        owner = User.objects.create_user(username='mediahost', password='pw')
        quiz = Quiz.objects.create(owner=owner, title='Media Quiz')
        self.question = Question.objects.create(
            quiz=quiz, text='Whats this?', order=0, media=small_gif(),
        )
        Choice.objects.create(question=self.question, text='A', is_correct=True, order=0)
        Choice.objects.create(question=self.question, text='B', is_correct=False, order=1)
        self.session = GameSession.objects.create(quiz=quiz)

    def test_question_payload_includes_media_url_and_kind(self):
        payload = services.question_payload(self.session, self.question)
        self.assertEqual(payload['media_kind'], 'image')
        self.assertEqual(payload['media_url'], self.question.media.url)
