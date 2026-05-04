# music/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
import json

from .models import DJSession, Song, SongRequest, Playlist
from orders.models import ActiveTableSession


@login_required
def dj_dashboard(request):
    """DJ Dashboard - control music requests"""
    if request.user.role not in ['dj', 'bar', 'manager', 'admin']:
        return redirect('waiter_dashboard')
    
    dj_session, created = DJSession.objects.get_or_create(id=1)
    songs = Song.objects.filter(is_active=True).order_by('artist', 'title')
    playlist = Playlist.objects.filter(is_played=False).order_by('-votes', 'created_at')
    
    # Get vote statistics
    song_votes = {}
    for song in songs:
        vote_count = SongRequest.objects.filter(song=song).count()
        song_votes[song.id] = vote_count
    
    context = {
        'dj_session': dj_session,
        'songs': songs,
        'playlist': playlist,
        'song_votes': song_votes,
    }
    return render(request, 'music/dj_dashboard.html', context)


@login_required
@csrf_exempt
def toggle_dj_session(request):
    """Toggle DJ session on/off"""
    # Allow DJ, bar, manager, admin roles
    if not is_authorized_dj(request.user):
        return JsonResponse({'error': f'Unauthorized. Role: {request.user.role}'}, status=403)
    
    # Get or create DJ session
    dj_session, created = DJSession.objects.get_or_create(
        id=1,
        defaults={
            'is_active': False,
            'activated_by': None
        }
    )
    
    if created:
        print("✅ Created new DJ session")
    
    # Toggle the status
    if dj_session.is_active:
        dj_session.is_active = False
        dj_session.ended_at = timezone.now()
        message = "Music voting deactivated"
        print("🔇 DJ session deactivated")
    else:
        dj_session.is_active = True
        dj_session.started_at = timezone.now()
        dj_session.activated_by = request.user
        dj_session.ended_at = None
        message = "Music voting activated! Customers can now request songs."
        print("🎵 DJ session activated")
    
    dj_session.save()
    
    # Notify all customers via WebSocket
    try:
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            "broadcast",
            {
                "type": "send_notification",
                "data": {
                    "type": "dj_status",
                    "is_active": dj_session.is_active,
                    "message": message
                }
            }
        )
    except Exception as e:
        print(f"WebSocket notification error: {e}")
    
    return JsonResponse({
        'status': 'success',
        'is_active': dj_session.is_active,
        'message': message
    })


# music/views.py - Fix add_song authorization

@login_required
@csrf_exempt
def add_song(request):
    """Add a new song to the library"""
    if not is_authorized_dj(request.user):
        return JsonResponse({'error': f'Unauthorized. Role: {request.user.role}'}, status=403)
    
    # Debug print
    print(f"===== ADD SONG DEBUG =====")
    print(f"User: {request.user.username}")
    print(f"User role: {request.user.role}")
    print(f"Is staff: {request.user.is_staff}")
    print(f"=========================")
    
    # Allow DJ, bar, manager, admin, and staff users
    allowed_roles = ['dj', 'bar', 'manager', 'admin']
    
    # Fix: Check if user has allowed role OR is staff
    if request.user.role not in allowed_roles and not request.user.is_staff:
        return JsonResponse({
            'error': f'Unauthorized. Your role is "{request.user.role}". Allowed roles: {allowed_roles}'
        }, status=403)
    
    try:
        data = json.loads(request.body)
        title = data.get('title')
        artist = data.get('artist')
        genre = data.get('genre', 'pop')
        
        if not title or not artist:
            return JsonResponse({'error': 'Title and artist required'}, status=400)
        
        # Check for existing song
        existing_song = Song.objects.filter(title__iexact=title, artist__iexact=artist).first()
        if existing_song:
            return JsonResponse({'error': f'Song "{title}" by {artist} already exists!'}, status=400)
        
        song = Song.objects.create(
            title=title,
            artist=artist,
            genre=genre,
            is_active=True
        )
        
        print(f"✅ Added song: {song.title} by {song.artist}")
        
        return JsonResponse({
            'status': 'success',
            'song': {
                'id': song.id,
                'title': song.title,
                'artist': song.artist,
                'genre': song.genre
            }
        })
        
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON data'}, status=400)
    except Exception as e:
        print(f"Error adding song: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
def vote_song(request, song_id):
    """Customer votes for a song"""
    if not request.session.get('customer_phone'):
        return JsonResponse({'error': 'Not logged in'}, status=401)
    
    # Check if DJ session is active
    dj_session = DJSession.objects.first()
    if not dj_session or not dj_session.is_active:
        return JsonResponse({'error': 'Music voting is currently inactive. Please check back later!'}, status=400)
    
    session_id = request.session.get('session_id')
    table_number = request.session.get('table_number')
    
    try:
        session = ActiveTableSession.objects.get(id=session_id, is_active=True)
    except ActiveTableSession.DoesNotExist:
        return JsonResponse({'error': 'Session not found'}, status=404)
    
    song = get_object_or_404(Song, id=song_id, is_active=True)
    
    # Check if customer already voted for this song
    existing_vote = SongRequest.objects.filter(song=song, session=session).first()
    
    if existing_vote:
        return JsonResponse({'error': f'You already requested "{song.title}"!'}, status=400)
    
    # Create vote
    vote = SongRequest.objects.create(
        song=song,
        session=session,
        table_number=table_number
    )
    
    # Update or create playlist entry
    playlist_entry, created = Playlist.objects.get_or_create(
        song=song,
        is_played=False,
        defaults={'votes': 1, 'requested_by': session}
    )
    
    if not created:
        playlist_entry.votes += 1
        playlist_entry.save()
    
    # Notify DJ via WebSocket
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        "dj_notifications",
        {
            "type": "send_notification",
            "data": {
                "type": "new_vote",
                "song": song.title,
                "artist": song.artist,
                "votes": playlist_entry.votes,
                "table": table_number,
                "message": f"🎵 {song.title} by {song.artist} - {playlist_entry.votes} votes!"
            }
        }
    )

    async_to_sync(channel_layer.group_send)(
        "station_dj",  # Send to DJ station group
        {
            "type": "send_notification",
            "data": {
                "type": "new_vote",
                "song": song.title,
                "artist": song.artist,
                "votes": playlist_entry.votes,
                "table": table_number,
            }
        }
    )
    
    return JsonResponse({
        'status': 'success',
        'message': f'You requested "{song.title}"!',
        'votes': playlist_entry.votes
    })

def is_authorized_dj(user):
    """Check if user is authorized for DJ operations"""
    allowed_roles = ['dj', 'bar', 'manager', 'admin']
    return user.role in allowed_roles or user.is_staff or user.is_superuser


def get_songs(request):
    """Get available songs for voting"""
    dj_session = DJSession.objects.first()
    is_active = dj_session.is_active if dj_session else False
    
    genre = request.GET.get('genre', '')
    songs = Song.objects.filter(is_active=True)
    if genre:
        songs = songs.filter(genre=genre)
    
    # Get user's votes
    session_id = request.session.get('session_id')
    user_votes = []
    if session_id:
        user_votes = SongRequest.objects.filter(session_id=session_id).values_list('song_id', flat=True)
    
    song_data = []
    for song in songs:
        song_data.append({
            'id': song.id,
            'title': song.title,
            'artist': song.artist,
            'genre': song.genre,
            'duration': song.duration,
            'voted': song.id in user_votes
        })
    
    return JsonResponse({
        'songs': song_data,
        'is_active': is_active
    })


def get_playlist(request):
    """Get current playlist for DJ"""
    playlist = Playlist.objects.filter(is_played=False).select_related('song').order_by('-votes', 'created_at')
    
    playlist_data = []
    for entry in playlist:
        playlist_data.append({
            'id': entry.id,
            'song': entry.song.title,
            'artist': entry.song.artist,
            'votes': entry.votes,
            'requested_by': entry.requested_by.table.number if entry.requested_by else 'Anonymous'
        })
    
    return JsonResponse({'playlist': playlist_data})


@login_required
@csrf_exempt
def mark_played(request, playlist_id):
    """Mark a song as played"""
    if not is_authorized_dj(request.user):
        return JsonResponse({'error': f'Unauthorized. Role: {request.user.role}'}, status=403)
    
    playlist_entry = get_object_or_404(Playlist, id=playlist_id)
    playlist_entry.is_played = True
    playlist_entry.played_at = timezone.now()
    playlist_entry.save()
    
    # Notify customers
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        "broadcast",
        {
            "type": "send_notification",
            "data": {
                "type": "song_played",
                "song": playlist_entry.song.title,
                "artist": playlist_entry.song.artist,
                "message": f"🎶 Now playing: {playlist_entry.song.title} by {playlist_entry.song.artist}"
            }
        }
    )
    
    return JsonResponse({'status': 'success'})


@login_required
@csrf_exempt
def clear_playlist(request):
    """Clear all unplayed songs (for DJ)"""
    if not is_authorized_dj(request.user):
        return JsonResponse({'error': f'Unauthorized. Role: {request.user.role}'}, status=403)
    
    deleted = Playlist.objects.filter(is_played=False).delete()
    
    return JsonResponse({'status': 'success', 'deleted': deleted[0]})