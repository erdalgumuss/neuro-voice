"""Filesystem layout for one fine-tune project.

A "project" is one voice's training run end-to-end: source audio,
transcription cache, segmented clips, manifests, training config,
checkpoints, evaluation outputs, and the zip archives the operator
hands off afterwards. All paths derive from ``ProjectLayout(root,
voice_id)`` so every module in :mod:`finetune` reads from the same
convention.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectLayout:
    """Resolves every path a fine-tune step needs from one root + voice_id.

    ``root`` is typically ``/path/to/finetune/`` (Colab uses Google Drive,
    local dev points at the working tree). ``voice_id`` is the catalog
    slug the resulting LoRA will register against once promoted into
    production — e.g. ``tr-warm-storyteller-v1``.
    """

    root: Path
    voice_id: str

    @property
    def project_dir(self) -> Path:
        return self.root / self.voice_id

    @property
    def source_raw_dir(self) -> Path:
        """Operator drops original audio (any supported codec) here."""
        return self.project_dir / "source_raw"

    @property
    def source_audio_dir(self) -> Path:
        """16 kHz mono WAV copies produced by ffmpeg from source_raw."""
        return self.project_dir / "source_audio"

    @property
    def deepgram_dir(self) -> Path:
        """Cached Deepgram JSON responses (one per source file)."""
        return self.project_dir / "deepgram"

    @property
    def audio_dir(self) -> Path:
        """Per-utterance clip WAVs cut out of source_audio."""
        return self.project_dir / "audio"

    @property
    def raw_manifest_path(self) -> Path:
        return self.project_dir / "manifest_raw.jsonl"

    @property
    def segments_review_csv(self) -> Path:
        return self.project_dir / "segments_review.csv"

    @property
    def split_dir(self) -> Path:
        return self.project_dir / "splits"

    @property
    def train_manifest_path(self) -> Path:
        return self.split_dir / "train.jsonl"

    @property
    def val_manifest_path(self) -> Path:
        return self.split_dir / "val.jsonl"

    @property
    def test_manifest_path(self) -> Path:
        return self.split_dir / "test.jsonl"

    @property
    def conf_dir(self) -> Path:
        return self.project_dir / "conf"

    @property
    def lora_config_path(self) -> Path:
        return self.conf_dir / "voxcpm2_lora.yaml"

    @property
    def checkpoint_dir(self) -> Path:
        return self.project_dir / "checkpoints" / "lora"

    @property
    def log_dir(self) -> Path:
        return self.project_dir / "logs" / "lora"

    @property
    def output_dir(self) -> Path:
        return self.project_dir / "outputs"

    @property
    def metadata_path(self) -> Path:
        return self.project_dir / "run_metadata.json"

    def ensure_dirs(self) -> None:
        """Create every directory in the layout. Idempotent."""
        for path in (
            self.project_dir, self.source_raw_dir, self.source_audio_dir,
            self.deepgram_dir, self.audio_dir, self.split_dir,
            self.conf_dir, self.checkpoint_dir, self.log_dir,
            self.output_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
