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

from mycroft.configuration import Configuration
from precise_runner import (PreciseRunner, PreciseEngine, ReadWriteStream)


class PreciseHotword:
    """Precice is the default wakeword engine for mycroft.

    Precise is developed by Mycroft AI and produces quite good wake word
    spotting when trained on a decent dataset.
    """
    def __init__(self, key_phrase="hey mycroft", lang="en-us"):

        self.config = Configuration.get()
        self.key_phrase = str(key_phrase).lower()
        self.listener_config = self.config.get("listener", {})
        self.lang = str(self.config.get("lang", lang)).lower()
        trigger_level = self.config.get('trigger_level', 3)
        sensitivity = self.config.get('sensitivity', 0.5)

        precise_exe = "/home/pi/.mycroft/precise/precise-engine/precise-engine"
        precise_model = "/home/pi/.mycroft/precise/hey-mycroft.pb"

        self.has_found = False
        self.stream = ReadWriteStream()

        def on_activation():
            self.has_found = True

        self.runner = PreciseRunner(
            engine=PreciseEngine(exe_file=precise_exe, model_file=precise_model),
            trigger_level=trigger_level,
            sensitivity=sensitivity,
            stream=self.stream,
            on_activation=on_activation,
        )
        self.runner.start()

    def update(self, chunk):
        self.stream.write(chunk)

    def found_wake_word(self):
        if self.has_found:
            self.has_found = False
            return True
        return False

    def stop(self):
        if self.runner:
            self.runner.stop()
