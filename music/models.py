# music/models.py
from django.db import models
from django.conf import settings
from orders.models import ActiveTableSession
from django.utils import timezone

User = settings.AUTH_USER_MODEL

class DJSession(models.Model):
    """Active DJ session - controls whether voting is active"""
    is_active = models.BooleanField(default=False)
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    activated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='dj_sessions')
    
    def __str__(self):
        status = "Active" if self.is_active else "Inactive"
        return f"DJ Session - {status}"
    
    def activate(self, user):
        self.is_active = True
        self.activated_by = user
        self.save()
    
    def deactivate(self):
        self.is_active = False
        self.ended_at = timezone.now()
        self.save()


class Song(models.Model):
    """Song that can be requested"""
    GENRE_CHOICES = (
        ('pop', 'Pop'),
        ('rock', 'Rock'),
        ('jazz', 'Jazz'),
        ('electronic', 'Electronic'),
        ('hiphop', 'Hip Hop'),
        ('rnb', 'R&B'),
        ('classical', 'Classical'),
        ('ethiopian', 'Ethiopian'),
    )
    
    title = models.CharField(max_length=200)
    artist = models.CharField(max_length=200)
    genre = models.CharField(max_length=20, choices=GENRE_CHOICES, default='pop')
    duration = models.IntegerField(help_text="Duration in seconds", default=180)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['artist', 'title']
    
    def __str__(self):
        return f"{self.title} - {self.artist}"


class SongRequest(models.Model):
    """Customer vote for a song"""
    song = models.ForeignKey(Song, on_delete=models.CASCADE, related_name='requests')
    session = models.ForeignKey(ActiveTableSession, on_delete=models.CASCADE, related_name='song_requests')
    voted_at = models.DateTimeField(auto_now_add=True)
    table_number = models.IntegerField()
    
    class Meta:
        unique_together = ['song', 'session']  # One vote per song per session
        ordering = ['-voted_at']
    
    def __str__(self):
        return f"Table {self.table_number} requested {self.song.title}"


class Playlist(models.Model):
    """Queue of songs to be played"""
    song = models.ForeignKey(Song, on_delete=models.CASCADE)
    requested_by = models.ForeignKey(ActiveTableSession, on_delete=models.SET_NULL, null=True)
    votes = models.IntegerField(default=1)
    is_played = models.BooleanField(default=False)
    played_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-votes', 'created_at']
    
    def __str__(self):
        return f"{self.song.title} - {self.votes} votes"