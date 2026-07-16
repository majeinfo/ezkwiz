import asyncio

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.layers import get_channel_layer

from . import services
from .models import GameSession

# Fire-and-forget auto-close timers, keyed by task so they aren't garbage
# collected mid-sleep (asyncio only holds a weak reference otherwise).
_pending_auto_close_tasks = set()


def _spawn_auto_close(session_id, question_id, delay_seconds, group_name):
    task = asyncio.create_task(
        _auto_close_after_timeout(session_id, question_id, delay_seconds, group_name)
    )
    _pending_auto_close_tasks.add(task)
    task.add_done_callback(_pending_auto_close_tasks.discard)


async def _auto_close_after_timeout(session_id, question_id, delay_seconds, group_name):
    await asyncio.sleep(delay_seconds)
    result = await database_sync_to_async(_close_if_still_on_question)(session_id, question_id)
    if result is not None:
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            group_name, {'type': 'game.event', 'payload': {'type': 'question_closed', **result}}
        )


def _close_if_still_on_question(session_id, question_id):
    """
    Closes the question only if the session is still actively on it -- avoids a
    stale timer (from a question that was already closed manually, or by the
    all-answered shortcut) reaching in and closing a later question instead.
    """
    session = GameSession.objects.select_related('quiz').filter(pk=session_id).first()
    if session is None or session.status != GameSession.Status.ACTIVE:
        return None
    question = session.current_question
    if question is None or question.id != question_id:
        return None
    try:
        return services.close_question(session)
    except services.GameError:
        return None


class GameGroupConsumer(AsyncJsonWebsocketConsumer):
    """Shared group-join/broadcast plumbing for the host and player consumers."""

    async def game_event(self, event):
        await self.send_json(event['payload'])

    async def broadcast(self, payload):
        await self.channel_layer.group_send(
            self.group_name, {'type': 'game.event', 'payload': payload}
        )

    async def disconnect(self, code):
        if getattr(self, 'group_name', None):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def send_error(self, message):
        await self.send_json({'type': 'error', 'message': message})


class PlayerConsumer(GameGroupConsumer):
    async def connect(self):
        self.code = self.scope['url_route']['kwargs']['code']
        self.session = await database_sync_to_async(
            lambda: GameSession.objects.select_related('quiz').filter(code=self.code).first()
        )()
        if self.session is None:
            await self.close(code=4404)
            return

        self.group_name = f'game_{self.code}'
        self.player = None
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        if getattr(self, 'player', None):
            await database_sync_to_async(self._mark_disconnected)()
        await super().disconnect(code)

    def _mark_disconnected(self):
        self.player.is_connected = False
        self.player.save(update_fields=['is_connected'])

    async def receive_json(self, content, **kwargs):
        action = content.get('action')
        try:
            if action == 'join':
                await self._handle_join(content)
            elif action == 'answer':
                await self._handle_answer(content)
            else:
                await self.send_error(f'Unknown action: {action}')
        except services.GameError as exc:
            await self.send_error(str(exc))

    async def _handle_join(self, content):
        nickname = content.get('nickname', '')
        client_token = content.get('client_token')

        self.player = await database_sync_to_async(services.join_game)(
            self.session, nickname, client_token
        )
        state = await database_sync_to_async(services.current_state_payload)(self.session)

        await self.send_json({
            'type': 'joined',
            'nickname': self.player.nickname,
            'client_token': str(self.player.client_token),
            'status': self.session.status,
        })

        if state:
            await self.send_json(state)

        await self.broadcast({
            'type': 'lobby_update',
            'players': await database_sync_to_async(
                lambda: [p.nickname for p in self.session.players.all()]
            )(),
        })

    async def _handle_answer(self, content):
        if not self.player:
            await self.send_error('Join the game before answering.')
            return

        question_id = content.get('question_id')
        choice_ids = content.get('choice_ids', [])
        response_time_ms = content.get('response_time_ms', 0)

        close_result = await database_sync_to_async(self._submit_answer_and_maybe_close)(
            question_id, choice_ids, response_time_ms
        )
        await self.send_json({'type': 'answer_received'})

        if close_result is not None:
            await self.broadcast({'type': 'question_closed', **close_result})
        else:
            await self.broadcast({
                'type': 'tally_update',
                **await database_sync_to_async(
                    lambda: services.answer_counts(self.session, self.session.current_question)
                )(),
            })

    def _submit_answer_and_maybe_close(self, question_id, choice_ids, response_time_ms):
        self.session.refresh_from_db()
        question = self.session.current_question
        if question is None or question.id != question_id:
            raise services.GameError('That is not the current question.')
        services.submit_answer(self.session, self.player, question, choice_ids, response_time_ms)

        if self.session.quiz.auto_close_when_all_answered and services.all_players_answered(
            self.session, question
        ):
            try:
                return services.close_question(self.session)
            except services.GameError:
                return None
        return None


class HostConsumer(GameGroupConsumer):
    async def connect(self):
        host_token = self.scope['url_route']['kwargs']['host_token']
        user = self.scope.get('user')

        self.session = await database_sync_to_async(
            lambda: GameSession.objects.select_related('quiz').filter(host_token=host_token).first()
        )()

        if self.session is None or not user or not user.is_authenticated:
            await self.close(code=4403)
            return

        is_owner = await database_sync_to_async(lambda: self.session.quiz.owner_id == user.id)()
        if not is_owner:
            await self.close(code=4403)
            return

        self.code = self.session.code
        self.group_name = f'game_{self.code}'
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def receive_json(self, content, **kwargs):
        action = content.get('action')
        try:
            if action == 'start':
                await self._handle_start()
            elif action == 'close_question':
                await self._handle_close_question()
            elif action == 'next_question':
                await self._handle_next_question()
            else:
                await self.send_error(f'Unknown action: {action}')
        except services.GameError as exc:
            await self.send_error(str(exc))

    async def _handle_start(self):
        question = await database_sync_to_async(self._start_game_and_get_question)()
        await self.broadcast({'type': 'question_started', 'question': question})
        _spawn_auto_close(
            self.session.id, question['id'], question['time_limit_seconds'], self.group_name
        )

    def _start_game_and_get_question(self):
        self.session.refresh_from_db()
        services.start_game(self.session)
        return services.question_payload(self.session, self.session.current_question)

    async def _handle_close_question(self):
        result = await database_sync_to_async(self._close_question)()
        await self.broadcast({'type': 'question_closed', **result})

    def _close_question(self):
        self.session.refresh_from_db()
        return services.close_question(self.session)

    async def _handle_next_question(self):
        result = await database_sync_to_async(self._next_question)()
        if result['finished']:
            await self.broadcast({'type': 'game_ended', 'leaderboard': result['leaderboard']})
        else:
            question = result['question']
            await self.broadcast({'type': 'question_started', 'question': question})
            _spawn_auto_close(
                self.session.id, question['id'], question['time_limit_seconds'], self.group_name
            )

    def _next_question(self):
        self.session.refresh_from_db()
        return services.next_question(self.session)
