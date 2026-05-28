"""LoRA fine-tune pipeline for VoxCPM2-based voices.

This package replaces ``notebooks/04-voxcpm2-lora-finetune-colab.ipynb``
(archived after ADR-8) with first-class modular code. The notebook was
the original runner; everything that can plausibly live outside the
notebook now does, so a non-Colab operator can fine-tune via
``python -m scripts.finetune <command> --project <slug>`` instead.

Pipeline:

    raw audio  -> transcribe.py   (Deepgram + ffmpeg -> per-utterance clips)
               -> manifest.py     (validate durations, build raw JSONL,
                                   split train/val/test, ref_audio mixing)
               -> config.py       (write voxcpm2_lora.yaml; VRAM-aware
                                   batch_size + step count)
               -> train.py        (subprocess into VoxCPM's training
                                   script with the generated config)
               -> inference.py    (load LoRA checkpoint, score eval prompts)
               -> export.py       (metadata.json + zip artifacts for
                                   archival / handoff)

Project layout is encoded in :class:`project.ProjectLayout` so paths
stay consistent across the eight steps without each module guessing.

The only Colab-specific bits left in the notebook were:
    * google.colab.drive.mount + getpass for the Deepgram key
    * IPython.display.Audio for inline playback
    * The %cd magic before the train subprocess

Those translate to CLI flags + structured stdout in this package.
"""
