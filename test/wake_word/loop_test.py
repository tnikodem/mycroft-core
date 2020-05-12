from mycroft import dialog
from mycroft.enclosure.api import EnclosureAPI
from mycroft.client.speech.listener import RecognizerLoop
from mycroft.configuration import Configuration
from mycroft.identity import IdentityManager
from mycroft.lock import Lock as PIDLock  # Create/Support PID locking file
from mycroft.messagebus.client import MessageBusClient
from mycroft.messagebus.message import Message
from mycroft.util import create_daemon, wait_for_exit_signal, \
    reset_sigint_handler, create_echo_function
from mycroft.util.log import LOG

def run_test():
    config = Configuration.get(offline=True)

    # Register handlers on internal RecognizerLoop bus
    loop = RecognizerLoop()
    # loop.on('recognizer_loop:utterance', handle_utterance)
    # loop.on('recognizer_loop:speech.recognition.unknown', handle_unknown)
    # loop.on('speak', handle_speak)
    # loop.on('recognizer_loop:record_begin', handle_record_begin)
    # loop.on('recognizer_loop:awoken', handle_awoken)
    # loop.on('recognizer_loop:wakeword', handle_wakeword)
    # loop.on('recognizer_loop:record_end', handle_record_end)
    # loop.on('recognizer_loop:no_internet', handle_no_internet)

    # # Register handlers for events on main Mycroft messagebus
    # bus.on('open', handle_open)
    # bus.on('complete_intent_failure', handle_complete_intent_failure)
    # bus.on('recognizer_loop:sleep', handle_sleep)
    # bus.on('recognizer_loop:wake_up', handle_wake_up)
    # bus.on('mycroft.mic.mute', handle_mic_mute)
    # bus.on('mycroft.mic.unmute', handle_mic_unmute)
    # bus.on('mycroft.mic.get_status', handle_mic_get_status)
    # bus.on('mycroft.mic.listen', handle_mic_listen)
    # bus.on("mycroft.paired", handle_paired)
    # bus.on('recognizer_loop:audio_output_start', handle_audio_start)
    # bus.on('recognizer_loop:audio_output_end', handle_audio_end)
    # bus.on('mycroft.stop', handle_stop)
    # bus.on('message', create_echo_function('VOICE'))
    #
    # create_daemon(bus.run_forever)
    # create_daemon(loop.run)


    loop.run()


if __name__ == '__main__':
    run_test()
