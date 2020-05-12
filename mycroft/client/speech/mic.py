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
import audioop
from time import sleep, time as get_time

from collections import deque
import datetime
import json

import pyaudio
import requests

from hashlib import md5
from io import BytesIO, StringIO
from speech_recognition import (
    Microphone,
    AudioSource,
    AudioData
)

from threading import Thread, Lock


from mycroft.configuration import Configuration
from mycroft.session import SessionManager
from mycroft.util import check_for_signal, resolve_resource_file, play_wav
from mycroft.util.log import LOG


class MutableStream:
    def __init__(self, wrapped_stream, format, muted=False):
        assert wrapped_stream is not None
        self.wrapped_stream = wrapped_stream

        self.SAMPLE_WIDTH = pyaudio.get_sample_size(format)
        self.muted_buffer = b''.join([b'\x00' * self.SAMPLE_WIDTH])
        self.read_lock = Lock()

        self.muted = muted
        if muted:
            self.mute()

    def mute(self):
        """Stop the stream and set the muted flag."""
        with self.read_lock:
            self.muted = True
            self.wrapped_stream.stop_stream()

    def unmute(self):
        """Start the stream and clear the muted flag."""
        with self.read_lock:
            self.muted = False
            self.wrapped_stream.start_stream()

    def read(self, size, of_exc=False):
        """Read data from stream.

        Arguments:
            size (int): Number of bytes to read
            of_exc (bool): flag determining if the audio producer thread
                           should throw IOError at overflows.

        Returns:
            (bytes) Data read from device
        """
        frames = deque()
        remaining = size
        with self.read_lock:
            while remaining > 0:
                # If muted during read return empty buffer. This ensures no
                # reads occur while the stream is stopped
                if self.muted:
                    return self.muted_buffer

                to_read = min(self.wrapped_stream.get_read_available(),
                              remaining)
                if to_read <= 0:
                    sleep(.01)
                    continue
                result = self.wrapped_stream.read(to_read,
                                                  exception_on_overflow=of_exc)
                frames.append(result)
                remaining -= to_read

        input_latency = self.wrapped_stream.get_input_latency()
        if input_latency > 0.2:
            LOG.warning("High input latency: %f" % input_latency)
        audio = b"".join(list(frames))
        return audio

    def close(self):
        self.wrapped_stream.close()
        self.wrapped_stream = None

    def is_stopped(self):
        try:
            return self.wrapped_stream.is_stopped()
        except Exception as e:
            LOG.error(repr(e))
            return True  # Assume the stream has been closed and thusly stopped

    def stop_stream(self):
        return self.wrapped_stream.stop_stream()


class MutableMicrophone(Microphone):
    def __init__(self, device_index=None, sample_rate=16000, chunk_size=1024,
                 mute=False):
        Microphone.__init__(self, device_index=device_index,
                            sample_rate=sample_rate, chunk_size=chunk_size)
        self.muted = False
        if mute:
            self.mute()

    def __enter__(self):
        return self._start()

    def _start(self):
        """Open the selected device and setup the stream."""
        assert self.stream is None, \
            "This audio source is already inside a context manager"
        self.audio = pyaudio.PyAudio()
        self.stream = MutableStream(self.audio.open(
            input_device_index=self.device_index, channels=1,
            format=self.format, rate=self.SAMPLE_RATE,
            frames_per_buffer=self.CHUNK,
            input=True,  # stream is an input stream
        ), self.format, self.muted)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return self._stop()

    def _stop(self):
        """Stop and close an open stream."""
        try:
            if not self.stream.is_stopped():
                self.stream.stop_stream()
            self.stream.close()
        except Exception:
            LOG.exception('Failed to stop mic input stream')
            # Let's pretend nothing is wrong...

        self.stream = None
        self.audio.terminate()

    def restart(self):
        """Shutdown input device and restart."""
        self._stop()
        self._start()

    def mute(self):
        self.muted = True
        if self.stream:
            self.stream.mute()

    def unmute(self):
        self.muted = False
        if self.stream:
            self.stream.unmute()

    def is_muted(self):
        return self.muted


class ResponsiveRecognizer:  # (speech_recognition.Recognizer):
    # The minimum seconds of noise before a
    # phrase can be considered complete
    MIN_LOUD_SEC_PER_PHRASE = 0.5

    # The minimum seconds of silence required at the end
    # before a phrase will be considered complete
    MIN_SILENCE_AT_END = 0.25

    def __init__(self, wake_word_recognizer):
        # super().__init__() do we need it??
        self.config = Configuration.get()
        listener_config = self.config.get('listener')
        self.multiplier = listener_config.get('multiplier')
        self.energy_ratio = listener_config.get('energy_ratio')
        self.overflow_exc = listener_config.get('overflow_exception', False)
        # The maximum seconds a phrase can be recorded,
        # provided there is noise the entire time
        self.recording_timeout = listener_config.get('recording_timeout')
        # The maximum time it will continue to record silence
        # when not enough noise has been detected
        self.recording_timeout_with_silence = listener_config.get('recording_timeout_with_silence')

        self.wake_word_recognizer = wake_word_recognizer
        self.wake_word_name = wake_word_recognizer.key_phrase

        self.audio = pyaudio.PyAudio()

        # Signal statuses
        self._stop_signaled = False
        self._listen_triggered = False

    # @staticmethod
    # def calc_energy(sound_chunk, sample_width):
    #     return audioop.rms(sound_chunk, sample_width)
    #
    # def _record_phrase(
    #     self,
    #     source,
    #     sec_per_buffer,
    #     stream=None,
    #     ww_frames=None
    # ):
    #     """Record an entire spoken phrase.
    #
    #     Essentially, this code waits for a period of silence and then returns
    #     the audio.  If silence isn't detected, it will terminate and return
    #     a buffer of self.recording_timeout duration.
    #
    #     Args:
    #         source (AudioSource):  Source producing the audio chunks
    #         sec_per_buffer (float):  Fractional number of seconds in each chunk
    #         stream (AudioStreamHandler): Stream target that will receive chunks
    #                                      of the utterance audio while it is
    #                                      being recorded.
    #         ww_frames (deque):  Frames of audio data from the last part of wake
    #                             word detection.
    #
    #     Returns:
    #         bytearray: complete audio buffer recorded, including any
    #                    silence at the end of the user's utterance
    #     """
    #
    #     num_loud_chunks = 0
    #     noise = 0
    #
    #     max_noise = 25
    #     min_noise = 0
    #
    #     silence_duration = 0
    #
    #     def increase_noise(level):
    #         if level < max_noise:
    #             return level + 200 * sec_per_buffer
    #         return level
    #
    #     def decrease_noise(level):
    #         if level > min_noise:
    #             return level - 100 * sec_per_buffer
    #         return level
    #
    #     # Smallest number of loud chunks required to return
    #     min_loud_chunks = int(self.MIN_LOUD_SEC_PER_PHRASE / sec_per_buffer)
    #
    #     # Maximum number of chunks to record before timing out
    #     max_chunks = int(self.recording_timeout / sec_per_buffer)
    #     num_chunks = 0
    #
    #     # Will return if exceeded this even if there's not enough loud chunks
    #     max_chunks_of_silence = int(self.recording_timeout_with_silence /
    #                                 sec_per_buffer)
    #
    #     # bytearray to store audio in
    #
    #     def get_silence(num_bytes):
    #         return b'\0' * num_bytes
    #     byte_data = get_silence(source.SAMPLE_WIDTH)
    #
    #     if stream:
    #         stream.stream_start()
    #
    #     phrase_complete = False
    #     while num_chunks < max_chunks and not phrase_complete:
    #         if ww_frames:
    #             chunk = ww_frames.popleft()
    #         else:
    #             chunk = source.stream.read(source.CHUNK, self.overflow_exc)
    #         byte_data += chunk
    #         num_chunks += 1
    #
    #         if stream:
    #             stream.stream_chunk(chunk)
    #
    #         energy = self.calc_energy(chunk, source.SAMPLE_WIDTH)
    #         test_threshold = self.energy_threshold * self.multiplier
    #         is_loud = energy > test_threshold
    #         if is_loud:
    #             noise = increase_noise(noise)
    #             num_loud_chunks += 1
    #         else:
    #             noise = decrease_noise(noise)
    #             # self._adjust_threshold(energy, sec_per_buffer)
    #
    #
    #         was_loud_enough = num_loud_chunks > min_loud_chunks
    #
    #         quiet_enough = noise <= min_noise
    #         if quiet_enough:
    #             silence_duration += sec_per_buffer
    #             if silence_duration < self.MIN_SILENCE_AT_END:
    #                 quiet_enough = False  # gotta be silent for min of 1/4 sec
    #         else:
    #             silence_duration = 0
    #         recorded_too_much_silence = num_chunks > max_chunks_of_silence
    #         if quiet_enough and (was_loud_enough or recorded_too_much_silence):
    #             phrase_complete = True
    #
    #         # Pressing top-button will end recording immediately
    #         if check_for_signal('buttonPress'):
    #             phrase_complete = True
    #
    #     return byte_data

    def stop(self):
        self._stop_signaled = True

    def trigger_listen(self):
        LOG.debug('Listen triggered from external source.')
        self._listen_triggered = True

    def _wait_until_wake_word(self, source, sec_per_buffer, emitter):

        while not self._stop_signaled:
            chunk = source.stream.read(source.CHUNK, self.overflow_exc)

            # the heavy work is done here, only update if above energy threshold(!)
            self.wake_word_recognizer.update(chunk)

            if self.wake_word_recognizer.found_wake_word() or self._listen_triggered:
                SessionManager.touch()
                payload = {
                    'utterance': self.wake_word_name,
                    'session': SessionManager.get().session_id,
                }
                emitter.emit("recognizer_loop:wakeword", payload)
                return None

        return None

    def listen(self, source, emitter, stream=None):
        """Listens for chunks of audio that Mycroft should perform STT on.

        This will listen continuously for a wake-up-word, then return the
        audio chunk containing the spoken phrase that comes immediately
        afterwards.

        Args:
            source (AudioSource):  Source producing the audio chunks
            emitter (EventEmitter): Emitter for notifications of when recording
                                    begins and ends.
            stream (AudioStreamHandler): Stream target that will receive chunks
                                         of the utterance audio while it is
                                         being recorded

        Returns:
            AudioData: audio with the user's utterance, minus the wake-up-word
        """
        assert isinstance(source, AudioSource), "Source must be an AudioSource"

        #        bytes_per_sec = source.SAMPLE_RATE * source.SAMPLE_WIDTH
        sec_per_buffer = float(source.CHUNK) / source.SAMPLE_RATE

        LOG.debug("Waiting for wake word...")
        ww_frames = self._wait_until_wake_word(source, sec_per_buffer, emitter)

        self._listen_triggered = False
        if self._stop_signaled:
            return

        LOG.debug("Recording...")
        # If enabled, play a wave file with a short sound to audibly
        # indicate recording has begun.
        if self.config.get('confirm_listening'):
            audio_file = resolve_resource_file(self.config.get('sounds').get('start_listening'))
            if audio_file:
                source.mute()
                play_wav(audio_file).wait()
                source.unmute()
                # Clear frames from wakeword detctions since they're
                # irrelevant after mute - play wav - unmute sequence
                ww_frames = None


        # TODO record audio and send to stt
        # # Notify system of recording start
        # emitter.emit("recognizer_loop:record_begin")
        #
        # frame_data = self._record_phrase(
        #     source,
        #     sec_per_buffer,
        #     stream,
        #     ww_frames
        # )
        # audio_data = AudioData(frame_data, source.SAMPLE_RATE, source.SAMPLE_WIDTH)
        #
        # emitter.emit("recognizer_loop:record_end")
        # if self.save_utterances:
        #     LOG.info("Recording utterance")
        #     stamp = str(datetime.datetime.now())
        #     filename = "/{}/{}.wav".format(
        #         self.saved_utterances_dir,
        #         stamp
        #     )
        #     with open(filename, 'wb') as filea:
        #         filea.write(audio_data.get_wav_data())
        #     LOG.debug("Thinking...")
        #
        # return audio_data
