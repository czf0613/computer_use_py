"""Real codecs, synthetic media only: no desktop/audio device acquisition."""
import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform != 'darwin', reason='macOS codecs')

@pytest.fixture(scope='module')
def probe(tmp_path_factory):
    source = ROOT / 'native_code/osx/src/recording_writer.m'
    assert source.exists(), 'Native recording writer is not implemented'
    target = tmp_path_factory.mktemp('writer-build') / 'probe'
    result = subprocess.run(['xcrun', 'clang', '-fobjc-arc', '-fblocks', '-Wall', '-Wextra', '-Werror',
        '-Wno-deprecated-declarations', '-mmacosx-version-min=13.0',
        '-I', str(ROOT / 'native_code/osx/include'), str(source),
        str(ROOT / 'tests/native/recording_writer_probe.m'), '-o', str(target),
        '-framework', 'Foundation', '-framework', 'AVFoundation', '-framework', 'VideoToolbox',
        '-framework', 'CoreMedia', '-framework', 'CoreVideo', '-framework', 'AudioToolbox'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return target

def run(probe, tmp_path, mode):
    path = tmp_path / 'movie.mp4'
    result = subprocess.run([str(probe), str(path), mode], capture_output=True, text=True, timeout=45)
    if result.returncode == 77:
        pytest.skip('Required hardware H.264 encoder unavailable: ' + result.stderr)
    assert result.returncode == 0, result.stderr
    assert not list(tmp_path.glob('.*.tmp.mp4'))
    assert not list(tmp_path.glob('.scapkit-recording-*'))
    return json.loads(result.stdout), path

@pytest.mark.parametrize('mode,duration,count', [('silent', 1.05, 32), ('tone', 1.05, 32), ('short', .007, 1), ('boundary', 1., 30), ('pre-origin', 1.05, 32), ('planar', 1.05, 32)])
def test_real_mp4_timeline(probe, tmp_path, mode, duration, count):
    info, path = run(probe, tmp_path, mode)
    assert info['video_codec'] == 'avc1'
    assert info['audio_codec'] == 'aac '
    assert info['width'] == 64 and info['height'] == 48
    assert info['frames'] == count
    assert info['decoded_frames'] == count
    assert info['audio_duration'] == pytest.approx(duration, abs=.003)
    assert info['decoded_audio_end'] == pytest.approx(duration, abs=1/48000)
    assert info['first_keyframe'] is True
    assert info['duration'] == pytest.approx(duration, abs=.003)
    assert info['video_end'] == pytest.approx(duration, abs=.003)
    assert info['pts'] == pytest.approx([i / 30 for i in range(count)], abs=.001)
    assert info['size_bytes'] == path.stat().st_size
    assert info['last_duration'] == pytest.approx(duration - (count - 1) / 30, abs=.003)
    if mode in ('tone', 'planar'):
        assert info['early_energy'] < .002
        assert info['tone_energy'] > .05
        assert info['late_energy'] < .002
    elif mode == 'pre-origin':
        assert info['early_energy'] > .05
        assert info['tone_energy'] < .002
    else:
        assert info['tone_energy'] < .002

@pytest.mark.parametrize('mode', ['cancel', 'existing', 'race', 'invalid', 'bad-audio', 'late-audio', 'overflow', 'drop', 'finish-empty', 'drop-after-frame', 'backpressure', 'missing-parent', 'bad-time', 'prevent-publication', 'prevent-during-finish'])
def test_failure_cleanup_and_no_clobber(probe, tmp_path, mode):
    info, path = run(probe, tmp_path, mode)
    assert info['ok'] is True
    if mode in ('existing', 'race'):
        assert path.read_text() == 'KEEP'
    else:
        assert not path.exists()


def test_optional_ffmpeg_decode(probe, tmp_path):
    import shutil
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('Optional ffmpeg tools are not installed')
    _, path = run(probe, tmp_path, 'tone')
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(path)], capture_output=True, text=True, check=True)
    streams = json.loads(result.stdout)['streams']
    video = next(s for s in streams if s['codec_type'] == 'video')
    audio = next(s for s in streams if s['codec_type'] == 'audio')
    assert video['codec_name'] == 'h264' and video['pix_fmt'] == 'yuv420p'
    assert video['color_space'] == 'bt709'
    assert int(video['nb_frames']) == 32
    assert audio['codec_name'] == 'aac' and int(audio['sample_rate']) == 48000
    assert int(audio['channels']) == 2
    assert float(audio['duration']) == pytest.approx(1.05, abs=.001)
    subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-f', 'null', '-'], capture_output=True, text=True, check=True)


def test_publication_gate_returns_committed_result(probe, tmp_path):
    info, path = run(probe, tmp_path, 'published-result')
    assert info['ok'] is True
    assert info['result']['path'] == str(path)
    assert info['result']['size_bytes'] == path.stat().st_size
    assert info['result']['duration_s'] == pytest.approx(1.05)
    assert info['result']['width'] == 64 and info['result']['height'] == 48
    assert info['result']['fps'] == 30


def test_submission_does_not_block_capture_until_encoder_output(probe, tmp_path):
    info, path = run(probe, tmp_path, 'pending-output')
    assert info['returned_while_encoder_pending'] is True
    assert path.stat().st_size > 0


def test_last_owner_released_by_output_queue_cleans_without_deadlock(probe, tmp_path):
    info, path = run(probe, tmp_path, 'drop-pending-output')
    assert info['cleaned'] is True
    assert not path.exists()


def test_quality_controls_real_encoded_size_without_dropping_frames(probe, tmp_path):
    outputs = []
    for mode in ('quality-low', 'quality-high'):
        directory = tmp_path / mode
        directory.mkdir()
        info, path = run(probe, directory, mode)
        assert info['frames'] == info['decoded_frames'] == 32
        assert info['video_codec'] == 'avc1'
        assert info['video_end'] == pytest.approx(1.05, abs=.003)
        outputs.append(path.stat().st_size)
    assert outputs[0] < outputs[1] * .6
