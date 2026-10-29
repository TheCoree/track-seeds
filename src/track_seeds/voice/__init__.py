"""Голосовые оповещения: синтез речи и динамики (IP-камеры Hikvision, звук сервера)."""

from .announcer import Announcer, compose, in_hours, phrase_for
from .speakers import HikvisionSpeaker, LocalSpeaker, Speaker, SpeakerError, build_speaker
from .tts import VOICES, Synthesizer, download_voice, espeak_installed, piper_installed

__all__ = ["VOICES", "Announcer", "HikvisionSpeaker", "LocalSpeaker", "Speaker", "SpeakerError", "Synthesizer",
           "build_speaker", "compose", "download_voice", "espeak_installed", "in_hours", "phrase_for",
           "piper_installed"]
