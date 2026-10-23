#!/usr/bin/env python3
"""Download the pinned full-precision Chinese Kokoro model into local runtime."""
import hashlib
import json
from pathlib import Path
import tarfile
import time
from urllib.request import Request, urlopen

BASE = Path(__file__).resolve().parent
DESTINATION = BASE / 'runtime' / 'tts'
URL = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/kokoro-multi-lang-v1_1.tar.bz2'
SHA256 = 'a3f4c73d043860e3fd2e5b06f36795eb81de0fc8e8de6df703245edddd87dbad'


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def main():
    DESTINATION.mkdir(parents=True, exist_ok=True)
    archive = DESTINATION / 'kokoro-multi-lang-v1_1.tar.bz2'
    if not archive.exists() or digest(archive) != SHA256:
        temporary = archive.with_suffix('.part')
        request = Request(URL + '?download=1&go2_timestamp=' + str(int(time.time())),
                          headers={'User-Agent': 'Go2VoiceSetup/1.0'})
        received, last_report = 0, time.monotonic()
        with urlopen(request, timeout=45) as response, temporary.open('wb') as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                received += len(chunk)
                if time.monotonic() - last_report >= 10:
                    print(json.dumps({'downloaded_mb': round(received / 1024**2, 1)}), flush=True)
                    last_report = time.monotonic()
        if digest(temporary) != SHA256:
            raise RuntimeError('Downloaded model checksum does not match the official release.')
        temporary.replace(archive)
    with tarfile.open(archive, 'r|bz2') as source:
        for member in source:
            path = (DESTINATION / member.name).resolve()
            if DESTINATION.resolve() not in path.parents:
                raise RuntimeError('Archive contains a path outside its model directory.')
            if not (member.isfile() or member.isdir()):
                raise RuntimeError('Archive contains an unsupported non-file entry.')
            source.extract(member, DESTINATION)
    manifest = {'model': 'Kokoro v1.1 Chinese FP32', 'source_url': URL,
                'sha256': SHA256, 'license': 'Apache-2.0',
                'model_card': 'https://huggingface.co/hexgrad/Kokoro-82M-v1.1-zh',
                'speakers': 103, 'chinese_speakers': 100, 'sample_rate': 24000}
    (DESTINATION / 'model_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'installed': True, 'directory': str(DESTINATION), 'sha256_verified': True}), flush=True)


if __name__ == '__main__':
    main()
