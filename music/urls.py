# music/urls.py
from django.urls import path
from . import views

urlpatterns = [
    # DJ Views
    path('dj/', views.dj_dashboard, name='dj_dashboard'),
    path('api/dj/toggle/', views.toggle_dj_session, name='toggle_dj_session'),
    path('api/dj/add-song/', views.add_song, name='add_song'),
    path('api/dj/mark-played/<int:playlist_id>/', views.mark_played, name='mark_played'),
    path('api/dj/clear-playlist/', views.clear_playlist, name='clear_playlist'),
    
    # Customer Views
    path('api/songs/', views.get_songs, name='get_songs'),
    path('api/vote/<int:song_id>/', views.vote_song, name='vote_song'),
    path('api/current-playlist/', views.get_playlist, name='get_playlist'),
]