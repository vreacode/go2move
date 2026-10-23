"""Vendored LAN signaling from legion1581/unitree_webrtc_connect.
Commit e0abc5780761539eff89a382a79319e5cf6ad1f4.
https://github.com/legion1581/unitree_webrtc_connect
Cloud signaling deliberately omitted; requests are local with a bounded timeout.

MIT License

Copyright (c) 2025 Konstantin Severov

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

from __future__ import annotations

import base64
import json
import logging
import socket
import requests
from Crypto.PublicKey import RSA
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

def print_status(*args):
    logging.debug('%s: %s', *args)

from Crypto.Cipher import AES
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5
import base64
import uuid
import binascii

###############
### AES handling
###############

# Function to generate a UUID and return it as a 32-character hexadecimal string
def _generate_uuid() -> str:
    uuid_32 = uuid.uuid4().bytes  
    uuid_32_hex_string = binascii.hexlify(uuid_32).decode('utf-8')
    return uuid_32_hex_string

def pad(data: str) -> bytes:
    """Pad data to be a multiple of 16 bytes (AES block size)."""
    block_size = AES.block_size
    padding = block_size - len(data) % block_size
    padded_data = data + chr(padding) * padding
    return padded_data.encode('utf-8')

def unpad(data: bytes) -> str:
    """Remove padding from data."""
    padding = data[-1]
    return data[:-padding].decode('utf-8')

def aes_encrypt(data: str, key: str) -> str:
    """Encrypt the given data using AES (ECB mode with PKCS5 padding)."""
    # Ensure key is 32 bytes for AES-256
    key_bytes = key.encode('utf-8')

    # Pad the data to ensure it is a multiple of block size
    padded_data = pad(data)

    # Create AES cipher in ECB mode
    cipher = AES.new(key_bytes, AES.MODE_ECB)

    # Encrypt data
    encrypted_data = cipher.encrypt(padded_data)

    # Encode encrypted data to Base64
    encoded_encrypted_data = base64.b64encode(encrypted_data).decode('utf-8')

    return encoded_encrypted_data

def aes_decrypt(encrypted_data: str, key: str) -> str:
    """Decrypt the given data using AES (ECB mode with PKCS5 padding)."""
    # Ensure key is 32 bytes for AES-256
    key_bytes = key.encode('utf-8')

    # Decode Base64 encrypted data
    encrypted_data_bytes = base64.b64decode(encrypted_data)

    # Create AES cipher in ECB mode
    cipher = AES.new(key_bytes, AES.MODE_ECB)

    # Decrypt data
    decrypted_padded_data = cipher.decrypt(encrypted_data_bytes)

    # Unpad the decrypted data
    decrypted_data = unpad(decrypted_padded_data)

    return decrypted_data

# Function to generate an AES key
def generate_aes_key() -> str:
    return _generate_uuid()

###############
### RSA handling
###############

def rsa_load_public_key(pem_data: str) -> RSA.RsaKey:
    """Load an RSA public key from a PEM-formatted string."""
    key_bytes = base64.b64decode(pem_data)
    return RSA.importKey(key_bytes)

def rsa_encrypt(data: str, public_key: RSA.RsaKey) -> str:
    """Encrypt data using RSA and a given public key."""
    cipher = PKCS1_v1_5.new(public_key)

    # Maximum chunk size for encryption with RSA/ECB/PKCS1Padding is key size - 11 bytes
    max_chunk_size = ((public_key.size() + 8) // 8 if not hasattr(public_key, 'size_in_bytes') else public_key.size_in_bytes()) - 11
    data_bytes = data.encode('utf-8')

    encrypted_bytes = bytearray()
    for i in range(0, len(data_bytes), max_chunk_size):
        chunk = data_bytes[i:i + max_chunk_size]
        encrypted_chunk = cipher.encrypt(chunk)
        encrypted_bytes.extend(encrypted_chunk)

    # Base64 encode the final encrypted data
    encoded_encrypted_data = base64.b64encode(encrypted_bytes).decode('utf-8')
    return encoded_encrypted_data


_LEGACY_GCM_KEY = bytes(
    [232, 86, 130, 189, 22, 84, 155, 0, 142, 4, 166, 104, 43, 179, 235, 227]
)


class AesKeyRequiredError(RuntimeError):
    """Raised when a `data2 === 3` robot is reached without an AES-128 key."""

    def __init__(self):
        super().__init__(
            "This robot speaks data2=3 (G1 ≥ 1.5.1 / Go2 ≥ 1.1.15 / any "
            "R1) — the per-device AES-128 key is required to decrypt the "
            "LAN handshake.\n"
            "Pass `aes_128_key=...` (32 hex chars) to UnitreeWebRTCConnection.\n"
            "Fetch it via `examples/fetch_aes_key.py` or "
            "`UnitreeCloud.list_devices()`."
        )


class AesKeyRejectedError(RuntimeError):
    """Raised when the supplied AES-128 key fails the GCM tag check."""

    def __init__(self, hex_key: str):
        snippet = (hex_key[:8] + "…") if hex_key else "<empty>"
        super().__init__(
            f"AES-128 key rejected by the robot (key={snippet}). "
            "Verify it via `examples/fetch_aes_key.py` against the SN of "
            "the robot you are connecting to."
        )


class LocalSignalingPortError(RuntimeError):
    """Raised when neither LAN signaling port (9991 / 8081) is reachable on
    the robot. Distinct from auth / decryption failures so callers can tell
    "robot unreachable" apart from "key wrong"."""

    def __init__(self, ip: str, ports: tuple = (9991, 8081)):
        super().__init__(
            f"Robot at {ip} is not exposing a signaling port "
            f"(tried {', '.join(str(p) for p in ports)}). "
            f"Check the IP / power / Wi-Fi network / firewall on the robot."
        )
        self.ip = ip
        self.ports = ports


class RobotBusyError(RuntimeError):
    """Raised when the robot rejects the SDP offer because another WebRTC
    client (typically the official mobile apk) is already connected.
    Only one peer at a time."""

    def __init__(self):
        super().__init__(
            "Robot is connected by another WebRTC client. Close the "
            "official mobile app (or any other connected client) and try "
            "again."
        )


class NoSdpAnswerError(RuntimeError):
    """Raised when the LAN signaling round-trip succeeded at the HTTP
    level but the robot returned an empty / unparseable SDP answer."""

    def __init__(self, detail: str = ""):
        msg = "Robot signaling returned no SDP answer."
        if detail:
            msg = f"{msg} {detail}"
        msg += (
            " Check that the robot is powered on, the Wi-Fi link is "
            "stable, and no other WebRTC client is mid-handshake."
        )
        super().__init__(msg)


def _decrypt_data1_legacy(data1_b64: str) -> str:
    """`data2 === 2`: decrypt with the static GCM key."""
    raw = base64.b64decode(data1_b64)
    if len(raw) < 28:
        raise ValueError("data1 too short for legacy GCM decrypt")
    tag = raw[-16:]
    nonce = raw[-28:-16]
    ciphertext = raw[:-28]
    return AESGCM(_LEGACY_GCM_KEY).decrypt(nonce, ciphertext + tag, None).decode("utf-8")


def _decrypt_data1_v3(data1_b64: str, aes_128_hex: str) -> str:
    """`data2 === 3`: decrypt with the per-device AES-128 key."""
    if not aes_128_hex:
        raise AesKeyRequiredError()
    try:
        key = bytes.fromhex(aes_128_hex.strip().lower())
    except ValueError as e:
        raise RuntimeError(
            f"aes_128_key is not valid hex: {e}. Expected 32 hex chars."
        ) from e
    if len(key) != 16:
        raise RuntimeError(
            f"aes_128_key must be 16 bytes (32 hex chars), got {len(key)} bytes."
        )
    raw = base64.b64decode(data1_b64)
    if len(raw) < 28:
        raise ValueError("data1 too short for V3 GCM decrypt")
    tag = raw[-16:]
    nonce = raw[-28:-16]
    ciphertext = raw[:-28]
    try:
        return AESGCM(key).decrypt(nonce, ciphertext + tag, None).decode("utf-8")
    except Exception as e:
        raise AesKeyRejectedError(aes_128_hex) from e


def _calc_local_path_ending(data1):
    """Reproduce the apk's `con_ing_<path>` derivation: take the last 10
    chars of the decrypted `data1`, group into pairs, map each pair's
    second char (A-J) to its index, concatenate."""
    strArr = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"]
    last_10 = data1[-10:]
    chunks = [last_10[i : i + 2] for i in range(0, len(last_10), 2)]
    out = []
    for chunk in chunks:
        if len(chunk) > 1:
            try:
                out.append(strArr.index(chunk[1]))
            except ValueError:
                logging.warning(f"unexpected char {chunk[1]!r} in con_notify path")
    return "".join(map(str, out))


# ─── HTTP helpers for the LAN flow ────────────────────────────────────

def make_local_request(path, body=None, headers=None):
    """LAN-side POST. Returns the response object on success, None otherwise."""
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.post(url=path, data=body, headers=headers, timeout=6)
        response.raise_for_status()
        return response if response.status_code == 200 else None
    except requests.exceptions.RequestException as e:
        logging.error(f"Local request failed: {e}")
        return None


def _probe_tcp_port(ip: str, port: int, timeout: float = 1.5) -> bool:
    """Quick TCP-connect probe. Returns True if the port accepts a
    connection within `timeout` seconds. Closed-port RSTs return False
    instantly; only filtered/dropped traffic actually waits the full
    timeout, so this is fast on healthy LANs."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except (ConnectionRefusedError, socket.timeout, OSError):
        return False


def send_sdp_to_local_peer(ip, sdp, aes_128_key: str = None):
    """Send the SDP offer over LAN. Probes which signaling port the robot
    is exposing and dispatches to the matching flow:

        :9991 (con_notify)  —  newer firmware, all G1 and R1, post-1.1.11
                                Go2. Uses `aes_128_key` when the robot
                                replies with `data2 === 3` (G1 ≥ 1.5.1,
                                Go2 ≥ 1.1.15, every R1).
        :8081 (offer)        —  legacy Go2 firmware (pre-1.1.11).

    Raises `LocalSignalingPortError` if neither port is reachable."""
    if _probe_tcp_port(ip, 9991):
        print_status("LAN Signaling Method", f"🆕 con_notify ({ip}:9991)")
        return send_sdp_to_local_peer_new_method(ip, sdp, aes_128_key)

    if _probe_tcp_port(ip, 8081):
        print_status("LAN Signaling Method", f"🛑 legacy /offer ({ip}:8081)")
        return send_sdp_to_local_peer_old_method(ip, sdp)

    raise LocalSignalingPortError(ip)


def send_sdp_to_local_peer_old_method(ip, sdp):
    """Legacy `POST http://<ip>:8081/offer` flow. The whole SDP exchange
    is plaintext on this path — no AES, no RSA wrapping."""
    url = f"http://{ip}:8081/offer"
    headers = {"Content-Type": "application/json"}
    response = make_local_request(url, body=sdp, headers=headers)
    if response and response.status_code == 200:
        logging.debug(f"Received SDP: {response.text}")
        return response.text
    raise RuntimeError(
        f"Legacy /offer flow on {ip}:8081 returned "
        f"{response.status_code if response else 'no response'}"
    )


def send_sdp_to_local_peer_new_method(ip, sdp, aes_128_key: str = None):
    """LAN signaling flow: `con_notify` returns the robot's per-session pub
    key (encrypted under `data2 === 2` legacy / `data2 === 3` per-device)
    and a path token; the SDP is then encrypted under a fresh AES key,
    that AES key is RSA-wrapped with the robot's pub key, and POSTed to
    `con_ing_<path>`. The response is the encrypted SDP answer."""
    try:
        url = f"http://{ip}:9991/con_notify"
        response = make_local_request(url, body=None, headers=None)
        if not response:
            raise ValueError("Failed to receive initial public key response.")

        decoded_response = base64.b64decode(response.text).decode("utf-8")
        logging.debug(f"con_notify response: {decoded_response}")
        decoded_json = json.loads(decoded_response)

        data1 = decoded_json.get("data1")
        data2 = decoded_json.get("data2")

        if data2 == 2:
            data1 = _decrypt_data1_legacy(data1)
        elif data2 == 3:
            data1 = _decrypt_data1_v3(data1, aes_128_key)
        # data2 == 1 (or absent) → data1 is already plaintext

        public_key_pem = data1[10 : len(data1) - 10]
        path_ending = _calc_local_path_ending(data1)

        aes_key = generate_aes_key()
        public_key = rsa_load_public_key(public_key_pem)

        body = {
            "data1": aes_encrypt(sdp, aes_key),
            "data2": rsa_encrypt(aes_key, public_key),
        }

        url = f"http://{ip}:9991/con_ing_{path_ending}"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        response = make_local_request(url, body=json.dumps(body), headers=headers)

        if response:
            decrypted_response = aes_decrypt(response.text, aes_key)
            logging.debug(f"con_ing_{path_ending} response: {decrypted_response}")
            return decrypted_response
        return None
    except (AesKeyRequiredError, AesKeyRejectedError):
        raise
    except requests.exceptions.RequestException as e:
        logging.error(f"New-method SDP send failed: {e}")
        return None
    except json.JSONDecodeError as e:
        logging.error(f"con_notify JSON decode failed: {e}")
        return None
    except base64.binascii.Error as e:
        logging.error(f"con_notify base64 decode failed: {e}")
        return None


# ─── Backwards-compat shims ───────────────────────────────────────────

def decrypt_con_notify_data(encrypted_b64: str) -> str:
    """Legacy public helper kept for downstream callers that import it
    directly. Equivalent to `_decrypt_data1_legacy`."""
    return _decrypt_data1_legacy(encrypted_b64)
