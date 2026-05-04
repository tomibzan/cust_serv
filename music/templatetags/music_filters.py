# music/templatetags/music_filters.py
from django import template
from django.db.models import Sum
from music.models import Playlist, Song

register = template.Library()

@register.filter
def get_item(dictionary, key):
    """Get an item from a dictionary by key"""
    if dictionary is None:
        return 0
    return dictionary.get(key, 0)

@register.filter
def get_votes_count(song_id):
    """Get total votes for a song from active playlist entries"""
    try:
        total = Playlist.objects.filter(
            song_id=song_id,
            played_at__isnull=True
        ).aggregate(total_votes=Sum('votes'))['total_votes']
        return total if total else 0
    except:
        return 0