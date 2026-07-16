from django.contrib.auth import get_user_model
from django.test import TestCase

from quizzes.models import Choice, Question, Quiz

from . import services
from .models import GameSession

User = get_user_model()


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
