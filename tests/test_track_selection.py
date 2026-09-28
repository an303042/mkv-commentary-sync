import json
from unittest.mock import patch

from core.track_utils import (
    AudioTrack,
    default_mux_track_ids,
    identify_tracks,
    is_commentary_track,
)


def _track(track_id: int, name: str = "", *, is_commentary: bool = False) -> AudioTrack:
    return AudioTrack(track_id, "eng", "AC-3", 2, name, is_commentary)


def test_commentary_flag_is_preferred_even_without_a_track_name():
    assert is_commentary_track(_track(2, is_commentary=True))


def test_commentary_name_is_detected_case_insensitively():
    assert is_commentary_track(_track(2, "Audio COMMENTARY by the director"))
    assert is_commentary_track(_track(3, "Cast commentaries"))


def test_default_mux_tracks_selects_all_commentaries_but_not_reference():
    tracks = [
        _track(1, "Main audio"),
        _track(2, "Director commentary"),
        _track(3, "Producer Commentary"),
    ]

    assert default_mux_track_ids(tracks, reference_audio_index=0) == [2, 3]
    assert default_mux_track_ids(tracks, reference_audio_index=1) == [3]


def test_default_mux_tracks_does_not_guess_when_commentary_is_unknown():
    tracks = [_track(1, "Main audio"), _track(2, "English stereo")]

    assert default_mux_track_ids(tracks, reference_audio_index=0) == []


def test_identify_tracks_reads_matroska_commentary_flag():
    output = {
        "tracks": [
            {
                "id": 2,
                "type": "audio",
                "codec": "AC-3",
                "properties": {
                    "codec_id": "A_AC3",
                    "language": "eng",
                    "audio_channels": 2,
                    "flag_commentary": True,
                },
            }
        ]
    }

    with patch("core.track_utils.resolve_tool_path", return_value="mkvmerge"), patch(
        "core.track_utils.subprocess.run"
    ) as run:
        run.return_value.returncode = 0
        run.return_value.stdout = json.dumps(output)
        run.return_value.stderr = ""

        tracks = identify_tracks("source.mkv")

    assert tracks == [_track(2, is_commentary=True)]
