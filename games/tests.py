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

    def test_submit_answer_full_correct_awards_points(self):
        services.start_game(self.session)
        player = services.join_game(self.session, 'Alice')
        answer = services.submit_answer(
            self.session, player, self.question, [self.paris.id, self.berlin.id]
        )
        self.assertTrue(answer.is_correct)
        self.assertEqual(answer.points_awarded, services.POINTS_PER_CORRECT)
        player.refresh_from_db()
        self.assertEqual(player.score, services.POINTS_PER_CORRECT)

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
        services.submit_answer(self.session, player, self.question, [self.paris.id, self.berlin.id])
        player.refresh_from_db()
        self.assertEqual(player.score, services.POINTS_PER_CORRECT)

    def test_full_question_cycle_ends_game_after_last_question(self):
        services.start_game(self.session)
        player = services.join_game(self.session, 'Alice')
        services.submit_answer(self.session, player, self.question, [self.paris.id, self.berlin.id])

        result = services.close_question(self.session)
        self.assertEqual(result['leaderboard'][0]['score'], services.POINTS_PER_CORRECT)

        next_result = services.next_question(self.session)
        self.assertTrue(next_result['finished'])
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, GameSession.Status.FINISHED)

    def test_answer_rejected_when_question_not_open(self):
        player = services.join_game(self.session, 'Alice')
        with self.assertRaises(services.GameError):
            services.submit_answer(self.session, player, self.question, [self.paris.id])
