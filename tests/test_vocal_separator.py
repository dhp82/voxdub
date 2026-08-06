import os
from unittest import mock

from pydub import AudioSegment
from pydub.generators import Sine

from autodub.media import vocal_separator


def _make_wav(path: str, duration_ms: int = 200):
    Sine(220).to_audio_segment(duration=duration_ms).export(path, format="wav")


def test_separate_vocals_short_circuits_when_outputs_exist(tmp_path):
    work_dir = str(tmp_path)
    _make_wav(os.path.join(work_dir, "vocals.wav"))
    _make_wav(os.path.join(work_dir, "no_vocals.wav"))
    input_wav = str(tmp_path / "original_audio.wav")
    _make_wav(input_wav)

    with mock.patch("autodub.media.vocal_separator._run_demucs") as run, \
         mock.patch("autodub.media.vocal_separator.subprocess.run") as ffmpeg:
        result = vocal_separator.separate_vocals(input_wav, work_dir)

    run.assert_not_called()
    ffmpeg.assert_not_called()
    assert result["no_vocals"] == os.path.join(work_dir, "no_vocals.wav")
    assert result["vocals"] == os.path.join(work_dir, "vocals.wav")


def test_separate_vocals_returns_none_when_input_missing(tmp_path):
    work_dir = str(tmp_path)
    result = vocal_separator.separate_vocals(
        str(tmp_path / "missing.wav"), work_dir
    )
    assert result == {"vocals": None, "no_vocals": None}


def test_separate_vocals_returns_none_when_demucs_raises(tmp_path):
    work_dir = str(tmp_path)
    input_wav = str(tmp_path / "original_audio.wav")
    _make_wav(input_wav)

    def boom(*args, **kwargs):
        raise RuntimeError("model load failed")

    with mock.patch("autodub.media.vocal_separator._run_demucs_gpu_worker",
                    return_value=False), \
         mock.patch("autodub.media.vocal_separator._run_demucs", side_effect=boom):
        result = vocal_separator.separate_vocals(input_wav, work_dir)

    assert result == {"vocals": None, "no_vocals": None}
    assert not os.path.exists(os.path.join(work_dir, "no_vocals.wav"))
    # Raw scratch files should be cleaned up after a failed run
    assert not os.path.exists(os.path.join(work_dir, "_vocals_raw.wav"))
    assert not os.path.exists(os.path.join(work_dir, "_no_vocals_raw.wav"))


def test_gpu_worker_used_when_available(tmp_path):
    """When the GPU venv exists, separation goes through the GPU worker and
    the in-process CPU path is never touched."""
    work_dir = str(tmp_path)
    input_wav = str(tmp_path / "original_audio.wav")
    _make_wav(input_wav)

    def fake_worker(src, vocals_out, no_vocals_out, model_name):
        _make_wav(vocals_out)
        _make_wav(no_vocals_out)
        return True

    def fake_ffmpeg(cmd, **kwargs):
        src = cmd[cmd.index("-i") + 1]
        AudioSegment.from_wav(src).export(cmd[-1], format="wav")
        return mock.Mock(returncode=0, stderr="")

    with mock.patch("autodub.media.vocal_separator._run_demucs_gpu_worker",
                    side_effect=fake_worker), \
         mock.patch("autodub.media.vocal_separator._run_demucs") as cpu_run, \
         mock.patch("autodub.media.vocal_separator.subprocess.run",
                    side_effect=fake_ffmpeg):
        result = vocal_separator.separate_vocals(input_wav, work_dir)

    cpu_run.assert_not_called()
    assert result["no_vocals"] == os.path.join(work_dir, "no_vocals.wav")


def test_separate_vocals_normalizes_and_cleans_up(tmp_path):
    """Mocks a successful Demucs run plus the ffmpeg normalize step and
    verifies the final stems land in work_dir while raw scratch files are
    removed."""
    work_dir = str(tmp_path / "work")
    os.makedirs(work_dir)
    input_wav = str(tmp_path / "original_audio.wav")
    _make_wav(input_wav)

    def fake_demucs(src, vocals_out, no_vocals_out, model_name):
        _make_wav(vocals_out)
        _make_wav(no_vocals_out)

    captured = []

    def fake_ffmpeg(cmd, **kwargs):
        captured.append(cmd)
        src = cmd[cmd.index("-i") + 1]
        dst = cmd[-1]
        AudioSegment.from_wav(src).export(dst, format="wav")
        return mock.Mock(returncode=0, stderr="")

    with mock.patch("autodub.media.vocal_separator._run_demucs_gpu_worker",
                    return_value=False), \
         mock.patch("autodub.media.vocal_separator._run_demucs", side_effect=fake_demucs), \
         mock.patch("autodub.media.vocal_separator.subprocess.run", side_effect=fake_ffmpeg):
        result = vocal_separator.separate_vocals(input_wav, work_dir)

    assert result["no_vocals"] == os.path.join(work_dir, "no_vocals.wav")
    assert result["vocals"] == os.path.join(work_dir, "vocals.wav")
    assert os.path.exists(result["no_vocals"])
    assert os.path.exists(result["vocals"])
    assert not os.path.exists(os.path.join(work_dir, "_vocals_raw.wav"))
    assert not os.path.exists(os.path.join(work_dir, "_no_vocals_raw.wav"))
    assert sum(1 for c in captured if c[0] == "ffmpeg") == 2


def test_demucs_cache_used_when_it_succeeds(tmp_path):
    """Cache trả True → không đụng đường one-shot GPU lẫn CPU."""
    work_dir = str(tmp_path)
    input_wav = str(tmp_path / "original_audio.wav")
    _make_wav(input_wav)

    cache = mock.Mock(spec=vocal_separator.DemucsCache)

    def fake_separate(src, vocals_out, no_vocals_out, chunked):
        _make_wav(vocals_out)
        _make_wav(no_vocals_out)
        return True

    cache.separate.side_effect = fake_separate

    def fake_ffmpeg(cmd, **kwargs):
        src = cmd[cmd.index("-i") + 1]
        AudioSegment.from_wav(src).export(cmd[-1], format="wav")
        return mock.Mock(returncode=0, stderr="")

    with mock.patch("autodub.media.vocal_separator._run_demucs_gpu_worker") as gpu, \
         mock.patch("autodub.media.vocal_separator._run_demucs") as cpu, \
         mock.patch("autodub.media.vocal_separator.subprocess.run",
                    side_effect=fake_ffmpeg):
        result = vocal_separator.separate_vocals(
            input_wav, work_dir, demucs_cache=cache)

    cache.separate.assert_called_once()
    gpu.assert_not_called()
    cpu.assert_not_called()
    assert result["no_vocals"] == os.path.join(work_dir, "no_vocals.wav")


def test_demucs_cache_failure_falls_back_to_one_shot(tmp_path):
    """Cache trả False → rơi về đường one-shot như không có cache."""
    work_dir = str(tmp_path)
    input_wav = str(tmp_path / "original_audio.wav")
    _make_wav(input_wav)

    cache = mock.Mock(spec=vocal_separator.DemucsCache)
    cache.separate.return_value = False

    def fake_worker(src, vocals_out, no_vocals_out, model_name):
        _make_wav(vocals_out)
        _make_wav(no_vocals_out)
        return True

    def fake_ffmpeg(cmd, **kwargs):
        src = cmd[cmd.index("-i") + 1]
        AudioSegment.from_wav(src).export(cmd[-1], format="wav")
        return mock.Mock(returncode=0, stderr="")

    with mock.patch("autodub.media.vocal_separator._run_demucs_gpu_worker",
                    side_effect=fake_worker), \
         mock.patch("autodub.media.vocal_separator._run_demucs") as cpu, \
         mock.patch("autodub.media.vocal_separator.subprocess.run",
                    side_effect=fake_ffmpeg):
        result = vocal_separator.separate_vocals(
            input_wav, work_dir, demucs_cache=cache)

    cache.separate.assert_called_once()
    cpu.assert_not_called()
    assert result["no_vocals"] == os.path.join(work_dir, "no_vocals.wav")


def test_demucs_cache_ensure_fails_without_gpu_venv():
    """Không có venv GPU → _ensure trả False một lần rồi nhớ luôn."""
    cache = vocal_separator.DemucsCache()
    with mock.patch("autodub.media.vocal_separator.gpu_venv_python",
                    return_value="") as probe:
        assert cache.separate("in.wav", "v.wav", "nv.wav", False) is False
        assert cache.separate("in.wav", "v.wav", "nv.wav", False) is False
    probe.assert_called_once()  # lần hai đã _failed, không dò lại
    cache.close()
