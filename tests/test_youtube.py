"""video_id_of parsing — the one branchy piece of the YouTube flow."""

from audio_md.youtube import video_id_of

VID = "awdC4RZdT8A"


def test_url_forms():
    assert video_id_of(VID) == VID
    assert video_id_of(f"https://www.youtube.com/watch?v={VID}") == VID
    assert video_id_of(f"https://youtube.com/watch?v={VID}&t=42s") == VID
    assert video_id_of(f"youtube.com/watch?v={VID}") == VID
    assert video_id_of(f"https://youtu.be/{VID}") == VID
    assert video_id_of(f"https://youtu.be/{VID}?si=xyz") == VID
    assert video_id_of(f"https://www.youtube.com/shorts/{VID}") == VID
    assert video_id_of(f"https://www.youtube.com/live/{VID}") == VID
    assert video_id_of(f"https://music.youtube.com/watch?v={VID}") == VID


def test_non_matches():
    assert video_id_of("audio.mp3") is None
    assert video_id_of("tooshort") is None
    assert video_id_of("https://vimeo.com/12345678901") is None
    assert video_id_of("https://www.youtube.com/watch") is None
    assert video_id_of("https://www.youtube.com/@somechannel") is None
    assert video_id_of("https://www.youtube.com/playlist?list=PLx") is None
