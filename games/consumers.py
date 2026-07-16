from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from . import services
from .models import GameSession


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

        await database_sync_to_async(self._submit_answer)(
            question_id, choice_ids, response_time_ms
        )
        await self.send_json({'type': 'answer_received'})
        await self.broadcast({
            'type': 'tally_update',
            **await database_sync_to_async(
                lambda: services.answer_counts(self.session, self.session.current_question)
            )(),
        })

    def _submit_answer(self, question_id, choice_ids, response_time_ms):
        self.session.refresh_from_db()
        question = self.session.current_question
        if question is None or question.id != question_id:
            raise services.GameError('That is not the current question.')
        services.submit_answer(self.session, self.player, question, choice_ids, response_time_ms)


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

    def _start_game_and_get_question(self):
        services.start_game(self.session)
        return services.question_payload(self.session, self.session.current_question)

    async def _handle_close_question(self):
        result = await database_sync_to_async(services.close_question)(self.session)
        await self.broadcast({'type': 'question_closed', **result})

    async def _handle_next_question(self):
        result = await database_sync_to_async(services.next_question)(self.session)
        if result['finished']:
            await self.broadcast({'type': 'game_ended', 'leaderboard': result['leaderboard']})
        else:
            await self.broadcast({'type': 'question_started', 'question': result['question']})
