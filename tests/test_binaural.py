"""Offline regression checks.

Based on binaural-voice by Saekisui - https://github.com/Saekisui/binaural-voice
"""
import base64
import io
import json
import os
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from scipy.io import wavfile

from binaural_voice import render, validate_cues
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
        random.seed(17)
        render(self.source, self.output, [{'t': 0, 'tag': '左耳'}])
        np.testing.assert_array_equal(expected, wavfile.read(self.output)[1])

    def test_silence_short_and_unsigned(self):
        for signal in (np.zeros(240, dtype=np.int16), np.full(240, 128, dtype=np.uint8)):
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


if __name__ == '__main__':
    unittest.main()
