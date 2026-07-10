"""
tests/test_feed.py — Mixtape

Tests for the "Friends Listening Now" feed logic.
"""

import pytest
from datetime import datetime, timedelta, timezone
from app import create_app, db
from models import User, Song, ListeningEvent, friendships
from services.feed_service import get_friends_listening_now


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def friends(app):
    """A user with one friend, plus a song the friend can listen to."""
    with app.app_context():
        me = User(username="me", email="me@example.com")
        friend = User(username="friend", email="friend@example.com")
        db.session.add_all([me, friend])
        db.session.flush()

        db.session.execute(friendships.insert().values(user_id=me.id, friend_id=friend.id))
        db.session.execute(friendships.insert().values(user_id=friend.id, friend_id=me.id))

        song = Song(title="Track 1", artist="Various", shared_by=me.id)
        db.session.add(song)
        db.session.commit()

        yield {"me": me, "friend": friend, "song": song}


def test_listening_now_excludes_listen_from_yesterday_evening(app, friends):
    """
    A friend whose last listen was ~12 hours ago (e.g. yesterday evening,
    checked the next morning) should NOT show up as listening "right now".
    """
    with app.app_context():
        now = datetime.now(timezone.utc)
        event = ListeningEvent(
            user_id=friends["friend"].id,
            song_id=friends["song"].id,
            listened_at=now - timedelta(hours=12),
        )
        db.session.add(event)
        db.session.commit()

        feed = get_friends_listening_now(friends["me"].id)
        assert feed == []  # Bug causes the stale friend to appear


def test_listening_now_includes_listen_from_a_few_minutes_ago(app, friends):
    """A friend who listened a few minutes ago should show up as listening now."""
    with app.app_context():
        now = datetime.now(timezone.utc)
        event = ListeningEvent(
            user_id=friends["friend"].id,
            song_id=friends["song"].id,
            listened_at=now - timedelta(minutes=10),
        )
        db.session.add(event)
        db.session.commit()

        feed = get_friends_listening_now(friends["me"].id)
        assert len(feed) == 1
        assert feed[0]["friend"]["id"] == friends["friend"].id
