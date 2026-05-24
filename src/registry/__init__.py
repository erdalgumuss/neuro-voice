"""Voice adapter registry — YAML-backed catalog of voice slots.

Each voice is a manifest (YAML) + reference audio file (WAV/MP3/FLAC).
At runtime the registry hydrates manifests, normalizes reference audio
to a target sample rate, and serves them to the synth engine.
"""

from .catalog import Voice, VoiceRegistry, VoiceNotFound, VoiceAlreadyExists

__all__ = ["Voice", "VoiceRegistry", "VoiceNotFound", "VoiceAlreadyExists"]
