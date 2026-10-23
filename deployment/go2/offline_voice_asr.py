#!/usr/bin/env python3
"""Local Chinese speech recognition, without network calls or robot actions.

Run with /home/unitree/go2_voice_venv/bin/python and the downloaded Vosk model.
Inputs are signed 16-bit little-endian PCM or uncompressed mono WAV at 16 kHz.
"""
import argparse
import io
import json
import math
import os
from pathlib import Path
import sys
import wave

DEFAULT_MODEL = "/home/unitree/go2_ros2_ws/models/vosk-model-small-cn-0.22"
SAMPLE_RATE = 16000


class OfflineASR:
    """Keep one local model loaded; create a fresh decoder for each recording."""

    def __init__(self, model_path=None, max_seconds=30.0, vocabulary=None):
        self.model_path = Path(model_path or os.environ.get("GO2_VOSK_MODEL", DEFAULT_MODEL))
        self.max_seconds = float(max_seconds)
        if not math.isfinite(self.max_seconds) or self.max_seconds <= 0:
            raise ValueError("max_seconds must be positive")
        if not (self.model_path / "am" / "final.mdl").is_file():
            raise RuntimeError("Vosk model is missing: %s" % self.model_path)
        try:
            import vosk
        except ImportError as exc:
            raise RuntimeError("Run with /home/unitree/go2_voice_venv/bin/python; Vosk is not installed in this interpreter") from exc
        vosk.SetLogLevel(-1)
        self._vosk = vosk
        try:
            self._model = vosk.Model(str(self.model_path))
        except Exception as exc:
            raise RuntimeError("Could not load local Vosk model: %s" % exc) from exc
        self._grammar = None
        if vocabulary:
            if isinstance(vocabulary, str) or len(vocabulary) > 64:
                raise ValueError("vocabulary must be a list of at most 64 phrases")
            phrases = [self._tokenize_phrase(phrase) for phrase in vocabulary]
            self._grammar = json.dumps(list(dict.fromkeys(phrases)) + ["[unk]"], ensure_ascii=False)

    def _tokenize_phrase(self, phrase):
        # The Chinese model lexicon uses words, not whole unspaced sentences.
        if not isinstance(phrase, str) or not phrase.strip() or len(phrase) > 80:
            raise ValueError("Vocabulary phrases must contain 1 to 80 characters")
        phrase = "".join(phrase.split())
        paths = {len(phrase): []}
        for start in range(len(phrase) - 1, -1, -1):
            for end in range(len(phrase), start, -1):
                if end in paths and self._model.vosk_model_find_word(phrase[start:end]) >= 0:
                    paths[start] = [phrase[start:end]] + paths[end]
                    break
        if 0 not in paths:
            raise ValueError("Phrase is absent from the Chinese model vocabulary: %s" % phrase)
        return " ".join(paths[0])

    def transcribe_pcm(self, pcm, sample_rate=SAMPLE_RATE, channels=1, sample_width=2):
        """Decode a bounded PCM recording and return text plus word confidence."""
        if sample_rate != SAMPLE_RATE or channels != 1 or sample_width != 2:
            raise ValueError("Audio must be 16000 Hz, mono, signed 16-bit PCM")
        if not isinstance(pcm, (bytes, bytearray, memoryview)):
            raise TypeError("pcm must be bytes")
        pcm = bytes(pcm)
        if len(pcm) % 2:
            raise ValueError("PCM byte count must contain whole 16-bit samples")
        duration = len(pcm) / float(SAMPLE_RATE * 2)
        if duration > self.max_seconds:
            raise ValueError("Recording exceeds %.1f seconds" % self.max_seconds)
        try:
            recognizer = (self._vosk.KaldiRecognizer(self._model, SAMPLE_RATE, self._grammar)
                          if self._grammar else self._vosk.KaldiRecognizer(self._model, SAMPLE_RATE))
        except Exception as exc:
            raise RuntimeError("Could not create offline recognizer: %s" % exc) from exc
        recognizer.SetWords(True)
        results = []
        for offset in range(0, len(pcm), 8000):
            if recognizer.AcceptWaveform(pcm[offset:offset + 8000]):
                results.append(json.loads(recognizer.Result()))
        results.append(json.loads(recognizer.FinalResult()))
        words = [word for result in results for word in result.get("result", [])]
        raw_text = " ".join(result.get("text", "").strip() for result in results if result.get("text", "").strip())
        # Chinese tokens are separated by Vosk; consumers need normal command text.
        text = "".join(raw_text.split())
        unknown_speech = "[unk]" in raw_text
        confidence = sum(float(word.get("conf", 0.0)) for word in words) / len(words) if words else 0.0
        if unknown_speech:
            confidence = 0.0
        return {"text": text, "raw_text": raw_text, "confidence": round(confidence, 4),
                "words": words, "duration_seconds": round(duration, 3),
                "sample_rate": SAMPLE_RATE, "engine": "vosk", "offline": True,
                "restricted_vocabulary": self._grammar is not None, "unknown_speech": unknown_speech}

    def transcribe_wav(self, source):
        """Accept a local filename, a binary file object, or WAV bytes."""
        if isinstance(source, (bytes, bytearray, memoryview)):
            source = io.BytesIO(bytes(source))
        elif isinstance(source, Path):
            source = str(source)
        with wave.open(source, "rb") as recording:
            if recording.getcomptype() != "NONE":
                raise ValueError("WAV must contain uncompressed PCM")
            if recording.getframerate() != SAMPLE_RATE or recording.getnchannels() != 1 or recording.getsampwidth() != 2:
                raise ValueError("WAV must be 16000 Hz, mono, signed 16-bit PCM")
            if recording.getnframes() > int(self.max_seconds * SAMPLE_RATE):
                raise ValueError("Recording exceeds %.1f seconds" % self.max_seconds)
            pcm = recording.readframes(recording.getnframes())
            if len(pcm) != recording.getnframes() * 2:
                raise ValueError("WAV data is truncated")
        return self.transcribe_pcm(pcm)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", default="-", help="WAV or PCM file; '-' reads stdin")
    parser.add_argument("--format", choices=("wav", "pcm"), default="wav")
    parser.add_argument("--model", default=None)
    parser.add_argument("--phrase", action="append", help="Optional allowed command phrase; repeat to add phrases (includes unknown speech)")
    parser.add_argument("--max-seconds", type=float, default=30.0)
    parser.add_argument("--check-model", action="store_true", help="Load model only and print readiness")
    args = parser.parse_args(argv)
    try:
        asr = OfflineASR(args.model, args.max_seconds, args.phrase)
        if args.check_model:
            result = {"ready": True, "engine": "vosk", "offline": True, "model": str(asr.model_path)}
        else:
            # Bound reads before allocating recordings received on standard input.
            limit = int(args.max_seconds * SAMPLE_RATE * 2) + 65536
            if args.input == "-":
                payload = sys.stdin.buffer.read(limit + 1)
            else:
                with open(args.input, "rb") as source:
                    payload = source.read(limit + 1)
            if len(payload) > limit:
                raise ValueError("Audio input is too large")
            result = asr.transcribe_wav(payload) if args.format == "wav" else asr.transcribe_pcm(payload)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (RuntimeError, TypeError, ValueError, OSError, EOFError, wave.Error) as exc:
        print(json.dumps({"error": str(exc), "offline": True}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
