"""Offline regression checks.

Based on binaural-voice by Saekisui - https://github.com/Saekisui/binaural-voice
"""
import base64
import io
import json
import os
from pathlib import Path
import random
import struct
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from scipy.io import wavfile

from binaural_voice import MAX_FRAMES, MAX_WAV_BYTES, render, validate_cues
from speak import aligned_cues, speak, split_tags


class TagTests(unittest.TestCase):
    def test_strip_english_chinese_and_preserve_performance(self):
        text, marks = split_tags('[right][whispers] Hi [脑后]😀[left]')
        self.assertEqual(text, '[whispers] Hi 😀')
        self.assertEqual(marks, [('right', 0), ('behind', 14), ('left', 15)])

    def test_alignment_including_trailing_tag(self):
        text, marks = split_tags('[right]Hi[left]')
        alignment = {'characters': list(text), 'character_start_times_seconds': [0, .1], 'character_end_times_seconds': [.1, .2]}
        self.assertEqual(aligned_cues(text, marks, alignment), [{'tag': 'right', 't': 0}, {'tag': 'left', 't': .2}])
        for bad in ({}, {**alignment, 'characters': ['X', 'i']}, {**alignment, 'character_start_times_seconds': [0, float('nan')]}, {**alignment, 'character_start_times_seconds': [.2, .1]}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                aligned_cues(text, marks, bad)

    def test_invalid_cues(self):
        for cues in ({}, [None], [{'tag': 'unknown', 't': 0}], [{'tag': 'left', 't': -1}], [{'tag': 'left', 't': float('nan')}], [{'tag': 'left', 't': True}], [{'tag': 'left', 't': 3}], [{'tag': [], 't': 0}], [{}] * 1001):
            with self.subTest(cues=cues), self.assertRaises(ValueError):
                validate_cues(cues, 2)

    def test_validation_before_network(self):
        with patch('urllib.request.urlopen') as request, patch.dict(os.environ, {}, clear=True):
            for text, fmt in [('', 'pcm_24000'), ('[left]', 'pcm_24000'), ('hello', 'mp3'), ('hello', 'pcm_24000'), ('x' * 5001, 'pcm_24000')]:
                with self.assertRaises(ValueError):
                    speak(text, 'unused.wav', fmt)
            request.assert_not_called()


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.source = str(Path(self.directory.name) / 'input.wav')
        self.output = str(Path(self.directory.name) / 'output.wav')

    def signal(self, fs, seconds=.25):
        t = np.arange(int(fs * seconds)) / fs
        return np.int16(8000 * np.sin(2 * np.pi * 440 * t))

    def test_sample_rates_and_stereo(self):
        for fs in (8000, 16000, 22050, 24000, 44100, 48000, 96000):
            with self.subTest(fs=fs):
                Path(self.output).unlink(missing_ok=True)
                wavfile.write(self.source, fs, self.signal(fs))
                render(self.source, self.output, [{'t': 0, 'tag': 'right'}])
                actual_fs, y = wavfile.read(self.output)
                self.assertEqual(actual_fs, fs)
                self.assertEqual(y.dtype, np.int16)
                self.assertEqual(y.shape[1], 2)
                self.assertGreater(len(y), fs // 4)
                self.assertGreater(np.max(np.abs(y)), 0)
                self.assertLessEqual(np.max(np.abs(y.astype(float))), 29164)
                self.assertFalse(np.array_equal(y[:, 0], y[:, 1]))

    def test_chinese_compatibility(self):
        wavfile.write(self.source, 24000, self.signal(24000))
        random.seed(17)
        render(self.source, self.output, [{'t': 0, 'tag': 'left'}])
        expected = wavfile.read(self.output)[1]
        Path(self.output).unlink()
        random.seed(17)
        render(self.source, self.output, [{'t': 0, 'tag': '左耳'}])
        np.testing.assert_array_equal(expected, wavfile.read(self.output)[1])

    def test_silence_short_and_unsigned(self):
        for signal in (np.zeros(240, dtype=np.int16), np.full(240, 128, dtype=np.uint8)):
            Path(self.output).unlink(missing_ok=True)
            wavfile.write(self.source, 24000, signal)
            render(self.source, self.output)
            self.assertFalse(np.any(wavfile.read(self.output)[1]))

    def test_stereo_downmix(self):
        signal = self.signal(24000)
        wavfile.write(self.source, 24000, np.column_stack([signal, -signal]))
        render(self.source, self.output)
        self.assertFalse(np.any(wavfile.read(self.output)[1]))

    def test_motion_distance_and_pause(self):
        fs = 24000
        signal = self.signal(fs, 3)
        signal[fs:fs * 2] = 0
        wavfile.write(self.source, fs, signal)
        render(self.source, self.output, [
            {'t': 0, 'tag': 'left'}, {'t': .2, 'tag': 'behind'},
            {'t': .3, 'tag': 'away'}, {'t': .6, 'tag': 'close'},
            {'t': 2.1, 'tag': 'right'}, {'t': 2.5, 'tag': 'front'},
        ])
        output = wavfile.read(self.output)[1]
        self.assertTrue(np.any(output[:fs]))
        self.assertFalse(np.any(output[fs + 1024:2 * fs - 1024]))
        self.assertTrue(np.any(output[2 * fs:]))

    def test_invalid_audio(self):
        for fs, samples in ((4000, self.signal(4000)), (24000, np.zeros(1, dtype=np.int16)), (24000, np.array([float('nan')] * 240)), (24000, np.zeros((240, 3), dtype=np.int16))):
            with self.subTest(fs=fs, shape=samples.shape):
                wavfile.write(self.source, fs, samples)
                with self.assertRaises(ValueError):
                    render(self.source, self.output)
        with self.assertRaises(ValueError):
            render(self.source, self.source)

    def test_mocked_synthesis_to_stereo(self):
        pcm = self.signal(24000)
        response = {'audio_base64': base64.b64encode(pcm.astype('<i2').tobytes()).decode(), 'alignment': {'characters': list('Hi'), 'character_start_times_seconds': [0, .1], 'character_end_times_seconds': [.1, .2]}}
        with patch.dict(os.environ, {'ELEVENLABS_API_KEY': 'offline-test', 'ELEVENLABS_VOICE_ID': 'test'}), patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps(response).encode())) as request:
            self.assertEqual(speak('[right]Hi', self.output), [{'tag': 'right', 't': 0}])
            self.assertEqual(json.loads(request.call_args.args[0].data)['text'], 'Hi')
        self.assertEqual(wavfile.read(self.output)[1].shape[1], 2)

    def test_existing_paths_preserved(self):
        wavfile.write(self.source, 24000, self.signal(24000))
        original = Path(self.source).read_bytes()
        for kind in ('regular', 'hardlink', 'symlink', 'dangling'):
            with self.subTest(kind=kind):
                if kind == 'regular':
                    Path(self.output).write_bytes(b'keep me')
                elif kind == 'hardlink':
                    os.link(self.source, self.output)
                else:
                    os.symlink(self.source if kind == 'symlink' else self.source + '.missing', self.output)
                with self.assertRaisesRegex(ValueError, 'already exists'):
                    render(self.source, self.output)
                self.assertEqual(Path(self.source).read_bytes(), original)
                if kind == 'regular':
                    self.assertEqual(Path(self.output).read_bytes(), b'keep me')
                Path(self.output).unlink()

    def test_output_race_uses_exclusive_creation(self):
        wavfile.write(self.source, 24000, self.signal(24000))
        from binaural_voice import preflight_output

        def race(path):
            preflight_output(path)
            Path(path).write_bytes(b'created during rendering')

        with patch('binaural_voice.preflight_output', side_effect=race):
            with self.assertRaises(FileExistsError):
                render(self.source, self.output)
        self.assertEqual(Path(self.output).read_bytes(), b'created during rendering')

    def test_metadata_rejected_before_sample_loading(self):
        def riff(fmt, data_size, payload=b''):
            chunks = b'fmt ' + struct.pack('<I', len(fmt)) + fmt + b'data' + struct.pack('<I', data_size) + payload
            return b'RIFF' + struct.pack('<I', len(chunks) + 4) + b'WAVE' + chunks

        fmt = struct.pack('<HHIIHH', 1, 1, 24000, 48000, 2, 16)
        cases = [
            riff(fmt, 480, b'\0' * 2),  # Truncated data.
            riff(struct.pack('<HHIIHH', 1, 3, 24000, 144000, 6, 16), 480, b'\0' * 480),
            riff(struct.pack('<HHIIHH', 1, 1, 24000, 48001, 2, 16), 480, b'\0' * 480),
            riff(struct.pack('<HHIIHH', 6, 1, 24000, 24000, 1, 8), 240, b'\0' * 240),
            riff(fmt, 2 * (MAX_FRAMES + 1), b'\0' * (2 * (MAX_FRAMES + 1))),
            riff(fmt, 2 * (24000 * 120 + 1), b'\0' * (2 * (24000 * 120 + 1))),
        ]
        with patch('binaural_voice.wavfile.read') as read:
            for raw in cases:
                Path(self.source).write_bytes(raw)
                with self.assertRaises(ValueError):
                    render(self.source, self.output)
            # Sparse file proves the file-byte limit without large memory use.
            with open(self.source, 'wb') as source:
                source.truncate(MAX_WAV_BYTES + 1)
            with self.assertRaises(ValueError):
                render(self.source, self.output)
            read.assert_not_called()

    def test_extensible_and_24bit_pcm(self):
        for extensible in (False, True):
            Path(self.output).unlink(missing_ok=True)
            # 240 mono 24-bit samples, alternating small signed amplitudes.
            pcm = (b'\x00\x10\x00' + b'\x00\xf0\xff') * 120
            fmt = struct.pack('<HHIIHH', 0xFFFE if extensible else 1, 1, 24000, 72000, 3, 24)
            if extensible:
                fmt += struct.pack('<HHI', 22, 24, 4) + struct.pack('<I', 1) + bytes.fromhex('00001000800000aa00389b71')
            chunks = b'fmt ' + struct.pack('<I', len(fmt)) + fmt + b'data' + struct.pack('<I', len(pcm)) + pcm
            Path(self.source).write_bytes(b'RIFF' + struct.pack('<I', len(chunks) + 4) + b'WAVE' + chunks)
            render(self.source, self.output)
            self.assertTrue(np.any(wavfile.read(self.output)[1]))

    def test_float_range_and_nonfinite_output(self):
        for amplitude in (1.00001, -1.00001, 1e200):
            wavfile.write(self.source, 24000, np.full(240, amplitude))
            with self.assertRaisesRegex(ValueError, r'within \[-1, 1\]'):
                render(self.source, self.output)
            self.assertFalse(Path(self.output).exists())
        wavfile.write(self.source, 24000, self.signal(24000))
        with patch('binaural_voice.fftconvolve', return_value=np.array([np.nan])):
            with self.assertRaisesRegex(ValueError, 'nonfinite'):
                render(self.source, self.output)
        self.assertFalse(Path(self.output).exists())

    def test_supported_float_and_signed_extremes(self):
        for dtype in (np.float32, np.float64, np.int16, np.int32):
            Path(self.output).unlink(missing_ok=True)
            samples = (np.array([-1, 1] * 120, dtype=dtype) if np.issubdtype(dtype, np.floating)
                       else np.array([np.iinfo(dtype).min, np.iinfo(dtype).max] * 120, dtype=dtype))
            wavfile.write(self.source, 24000, samples)
            render(self.source, self.output)
            self.assertTrue(np.any(wavfile.read(self.output)[1]))

    def test_odd_unsigned_pcm_from_scipy(self):
        wavfile.write(self.source, 24000, np.full(241, 128, dtype=np.uint8))
        render(self.source, self.output)
        self.assertFalse(np.any(wavfile.read(self.output)[1]))


class ProviderSafetyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = str(Path(self.directory.name) / 'output.wav')
        self.environment = patch.dict(os.environ, {'ELEVENLABS_API_KEY': 'offline', 'ELEVENLABS_VOICE_ID': 'test'})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def response(self, ends=None):
        return {'audio_base64': base64.b64encode(b'\0' * 9600).decode(), 'alignment': {
            'characters': ['H', 'i'], 'character_start_times_seconds': [0, .1],
            'character_end_times_seconds': ends or [.1, .2]}}

    def test_existing_output_rejected_before_request(self):
        Path(self.output).write_bytes(b'keep')
        with patch('urllib.request.urlopen') as request:
            with self.assertRaisesRegex(ValueError, 'already exists'):
                speak('Hi', self.output)
            request.assert_not_called()
        self.assertEqual(Path(self.output).read_bytes(), b'keep')

    def test_response_and_encoded_audio_limits(self):
        with patch('speak.MAX_RESPONSE_BYTES', 64), patch('urllib.request.urlopen', return_value=io.BytesIO(b'x' * 65)):
            with self.assertRaisesRegex(ValueError, 'response exceeds'):
                speak('Hi', self.output)
        data = self.response()
        data['audio_base64'] = 'A' * 68
        with patch('speak.MAX_FRAMES', 24), patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps(data).encode())), patch('speak.base64.b64decode') as decode:
            with self.assertRaisesRegex(ValueError, 'oversized'):
                speak('Hi', self.output)
            decode.assert_not_called()

    def test_all_alignment_times_checked_without_tags(self):
        for ends in ([.3, .2], [.1, .251], [.1, 10 ** 400]):
            with patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps(self.response(ends)).encode())):
                with self.assertRaises(ValueError):
                    speak('Hi', self.output)
        alignment = self.response([.1, .24])['alignment']
        self.assertEqual(aligned_cues('Hi', [('left', 2)], alignment, .2), [{'tag': 'left', 't': .2}])


if __name__ == '__main__':
    unittest.main()
