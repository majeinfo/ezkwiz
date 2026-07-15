from django.urls import path

from . import views

app_name = 'quizzes'

urlpatterns = [
    path('', views.QuizListView.as_view(), name='list'),
    path('new/', views.QuizCreateView.as_view(), name='create'),
    path('<int:pk>/', views.QuizDetailView.as_view(), name='detail'),
    path('<int:pk>/edit/', views.QuizUpdateView.as_view(), name='edit'),
    path('<int:pk>/delete/', views.QuizDeleteView.as_view(), name='delete'),
    path('<int:pk>/publish/', views.publish_quiz, name='publish'),
    path('<int:quiz_pk>/questions/new/', views.question_create, name='question_create'),
    path('<int:quiz_pk>/questions/<int:pk>/edit/', views.question_edit, name='question_edit'),
    path('<int:quiz_pk>/questions/<int:pk>/delete/', views.question_delete, name='question_delete'),
]
