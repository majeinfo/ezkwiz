from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    re_path(r'^ws/play/(?P<code>[A-Z0-9]+)/$', consumers.PlayerConsumer.as_asgi()),
    re_path(
        r'^ws/host/(?P<host_token>[0-9a-f-]+)/$',
        consumers.HostConsumer.as_asgi(),
    ),
]
