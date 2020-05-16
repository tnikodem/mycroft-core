# Copyright 2017 Mycroft AI Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

from collections import deque
import datetime
import os
import pyaudio
import audioop

from speech_recognition import AudioData
from tempfile import gettempdir

from mycroft.configuration import Configuration
from mycroft.session import SessionManager
from mycroft.util import check_for_signal, resolve_resource_file, play_wav
from mycroft.util.log import LOG


class ResponsiveRecognizer:
    def __init__(self, wake_word_recognizer):
        self.config = Configuration.get()
        listener_config = self.config.get('listener')
        self.recording_timeout = listener_config.get('recording_timeout')
        self.recording_timeout_with_silence = listener_config.get('recording_timeout_with_silence')

        self.wake_word_recognizer = wake_word_recognizer
        self.wake_word_name = wake_word_recognizer.key_phrase

        self.audio_file = None
        if self.config.get('confirm_listening'):
            self.audio = pyaudio.PyAudio()

        # Signal statuses
        self._stop_signaled = False
        self._listen_triggered = False

        self.audio_file = resolve_resource_file(self.config.get('sounds').get('start_listening'))

        # Check the config for the flag to save wake words, utterances
        # and for a path under which to save them
        self.save_utterances = listener_config.get('save_utterances', False)
        self.save_wake_words = listener_config.get('record_wake_words', False)
        self.save_path = listener_config.get('save_path', gettempdir())
        self.saved_wake_words_dir = os.path.join(self.save_path, 'mycroft_wake_words')
        if self.save_wake_words and not os.path.isdir(self.saved_wake_words_dir):
            os.mkdir(self.saved_wake_words_dir)
        self.saved_utterances_dir = os.path.join(self.save_path, 'mycroft_utterances')
        if self.save_utterances and not os.path.isdir(self.saved_utterances_dir):
            os.mkdir(self.saved_utterances_dir)

        # Config for recording audio
        self.sec_per_buffer = 0.064  # TODO calculate dynamically
        # dynamic noise level
        self.dynamic_energy_threshold = 13   # only start value here
        self.min_rms_threshold = 1
        self.max_rms_threshold = 40
        # phrase recording
        self.min_loud_sec_per_phrase = 0.5
        self.min_loud_chunks = int(self.min_loud_sec_per_phrase / self.sec_per_buffer)
        self.min_silent_sec_after_phrase = 0.25
        self.min_silent_chunks = int(self.min_silent_sec_after_phrase / self.sec_per_buffer)
        self.max_loud_chunks = int(self.recording_timeout / self.sec_per_buffer)
        self.max_chunks_of_silence = int(self.recording_timeout_with_silence / self.sec_per_buffer)

    def stop(self):
        self._stop_signaled = True

    def trigger_listen(self):
        LOG.debug('Listen triggered from external source.')
        self._listen_triggered = True

    def _wait_until_wake_word(self, source, emitter):
        self.wake_word_recognizer.update(b'\0' * (25*1024))  # flush old wakeword

        # Save last chunks for saving later, about 1s should be fine. 1 chunk is 0.064s
        last_chunks = deque(maxlen=50)  # how big is the precise cache??!!

        skipped_frames = 0
        frame_idx = 0
        while not self._stop_signaled:
            frame_idx += 1
            chunk = source.stream.read(source.CHUNK)

            if len(chunk) != 2048:
                LOG.info(f"! {frame_idx} chunk len {len(chunk)}")

            # dynamic energy threshold: don't ask precise if not loud enough
            if audioop.rms(chunk, source.SAMPLE_WIDTH) > self.dynamic_energy_threshold:
                self.dynamic_energy_threshold = min(self.dynamic_energy_threshold*1.001, self.max_rms_threshold)
                skipped_frames = 0
                if self.save_wake_words:
                    last_chunks.append(chunk)
                self.wake_word_recognizer.update(chunk)  # the heavy work is done in this method
            else:
                skipped_frames += 1
                if skipped_frames > 100:
                    self.dynamic_energy_threshold = max(self.dynamic_energy_threshold*0.99, self.min_rms_threshold)
                    skipped_frames = 0

            if self.wake_word_recognizer.found_wake_word() or self._listen_triggered:
                SessionManager.touch()
                emitter.emit("recognizer_loop:wakeword",
                             dict(utterance=self.wake_word_name, session=SessionManager.get().session_id))

                # TODO put in another thread, do NOT wait for saving!
                if self.save_wake_words:
                    byte_data = b"".join(last_chunks)  # len == 2028 ??!
                    audidata = AudioData(byte_data, source.SAMPLE_RATE, source.SAMPLE_WIDTH)
                    now_str = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                    fn = os.path.join(self.saved_wake_words_dir, f"{now_str}.wav")
                    with open(fn, 'wb') as f:
                        f.write(audidata.get_wav_data())

                return

    def _record_phrase(self, source, stream=None):
        if stream:
            stream.stream_start()  # stream_stop is done outside in a try catch case
        num_chunks = 0
        num_loud_chunks = 0
        num_silent_chunks = 0
        all_chunks = deque(maxlen=self.max_loud_chunks)
        while num_chunks < self.max_loud_chunks:
            num_chunks += 1
            chunk = source.stream.read(source.CHUNK)
            all_chunks.append(chunk)
            if stream:
                stream.stream_chunk(chunk)

            if audioop.rms(chunk, source.SAMPLE_WIDTH) > self.dynamic_energy_threshold * 1.1:
                num_loud_chunks += 1
                num_silent_chunks = 0
            else:
                num_silent_chunks += 1

            if num_loud_chunks > self.max_loud_chunks:
                break
            if num_loud_chunks > self.min_loud_chunks and num_silent_chunks > self.min_silent_chunks:
                break

            if check_for_signal('buttonPress'):
                break
        return b"".join(all_chunks)

    def listen(self, source, emitter, stream_handler=None):
        self._wait_until_wake_word(source=source, emitter=emitter)
        if self._stop_signaled:
            return

        if self.audio_file:
            play_wav(self.audio_file)

        emitter.emit("recognizer_loop:record_begin")
        frame_data = self._record_phrase(
            source=source,
            stream=stream_handler,
        )
        audio_data = AudioData(frame_data, source.SAMPLE_RATE, source.SAMPLE_WIDTH)
        emitter.emit("recognizer_loop:record_end")

        if self.audio_file:
            play_wav(self.audio_file)

        if self.save_utterances:
            now_str = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            fn = os.path.join(self.saved_utterances_dir, f"{now_str}.wav")
            with open(fn, 'wb') as f:
                f.write(audio_data.get_wav_data())

        return audio_data
