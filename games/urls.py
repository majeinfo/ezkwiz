from django.urls import path

from . import views

app_name = 'games'

urlpatterns = [
    path('play/<str:code>/', views.play, name='play'),
    path('host/<uuid:host_token>/', views.host, name='host'),
]
