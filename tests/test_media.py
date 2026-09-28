"""Real container decoding without downloading/loading a speech model."""
import shutil
import subprocess

import pytest

from audio_md import transcribe

pytest.importorskip("av")
pytest.importorskip("faster_whisper")


@pytest.mark.parametrize("extension", ["mp4", "mov", "mkv", "webm", "ogg"])
def test_audio_only_decoding_from_media(tmp_path, extension):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg needed to generate media fixture")
    from faster_whisper.audio import decode_audio

    path = tmp_path / f"fixture.{extension}"
    cmd = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.2"]
    if extension != "ogg":
        cmd += ["-f", "lavfi", "-i", "color=size=16x16:duration=0.2", "-c:v",
                "libvpx" if extension == "webm" else "mpeg4"]
    cmd += ["-c:a", "libopus" if extension in ("webm", "ogg") else "aac", str(path)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=10)
    # Uploads are stored without an extension; use the actual container.
    uploaded = path.rename(tmp_path / "000")
    assert transcribe.inspect_media(uploaded)["has_video"] == (extension != "ogg")
    assert len(decode_audio(str(uploaded))) > 0


def test_no_audio_and_corrupt_media(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg needed to generate media fixture")
    path = tmp_path / "silent.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "color=size=16x16:duration=0.1", "-c:v", "mpeg4", str(path)],
                   check=True, capture_output=True, timeout=10)
    with pytest.raises(ValueError, match="sem faixa de áudio"):
        transcribe.inspect_media(path)
    path.write_bytes(b"not media")
    with pytest.raises(ValueError, match="corrompida"):
        transcribe.inspect_media(path)
