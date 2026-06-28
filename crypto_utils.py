#!/usr/bin/env python3
"""
AES-256-GCM encryption/decryption utilities for video segments.

Encrypted segment structure:
[IV (12 bytes)][Ciphertext (variable)][Authentication Tag (16 bytes)]

Total overhead: 28 bytes per segment (~0.006% for 500KB segment)
"""

import os
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend


class SegmentEncryptor:
    """AES-256-GCM encryption for video segments"""

    def __init__(self, key: bytes = None):
        """
        Args:
            key: 32-byte AES-256 key. If None, generates new random key.
        """
        self.key = key if key else os.urandom(32)

        if len(self.key) != 32:
            raise ValueError(f"Key must be 32 bytes for AES-256, got {len(self.key)}")

    def encrypt_segment(self, plaintext: bytes) -> bytes:
        """
        Encrypt a video segment with AES-256-GCM.

        Args:
            plaintext: Raw segment data (MP4 bytes)

        Returns:
            Encrypted data: IV || Ciphertext || Tag
        """
        # Generate random IV (nonce) for GCM - 12 bytes recommended
        iv = os.urandom(12)

        # Create cipher
        cipher = Cipher(
            algorithms.AES(self.key),
            modes.GCM(iv),
            backend=default_backend()
        )
        encryptor = cipher.encryptor()

        # Encrypt
        ciphertext = encryptor.update(plaintext) + encryptor.finalize()
        tag = encryptor.tag  # 16 bytes authentication tag

        # Combine: IV || Ciphertext || Tag
        encrypted = iv + ciphertext + tag

        return encrypted

    def decrypt_segment(self, encrypted: bytes) -> bytes:
        """
        Decrypt a video segment.

        Args:
            encrypted: IV || Ciphertext || Tag

        Returns:
            Decrypted plaintext

        Raises:
            cryptography.exceptions.InvalidTag: If authentication fails
        """
        # Extract components
        iv = encrypted[:12]
        tag = encrypted[-16:]
        ciphertext = encrypted[12:-16]

        # Create cipher with tag
        cipher = Cipher(
            algorithms.AES(self.key),
            modes.GCM(iv, tag),
            backend=default_backend()
        )
        decryptor = cipher.decryptor()

        # Decrypt and verify tag
        plaintext = decryptor.update(ciphertext) + decryptor.finalize()

        return plaintext

    def get_key_hex(self) -> str:
        """Get key as hex string for transmission"""
        return self.key.hex()

    @staticmethod
    def from_hex(key_hex: str) -> 'SegmentEncryptor':
        """Create encryptor from hex key string"""
        key = bytes.fromhex(key_hex)
        return SegmentEncryptor(key)


if __name__ == "__main__":
    # Test encryption/decryption
    import time

    print("=== AES-256-GCM Segment Encryption Test ===\n")

    # Create encryptor
    encryptor = SegmentEncryptor()
    print(f"Generated key: {encryptor.get_key_hex()[:32]}...\n")

    # Test with different segment sizes
    test_sizes = [
        (10_000, "10 KB"),
        (500_000, "500 KB (typical segment)"),
        (2_000_000, "2 MB (large segment)")
    ]

    for size, description in test_sizes:
        # Create test data
        plaintext = os.urandom(size)

        # Encrypt
        start = time.time()
        encrypted = encryptor.encrypt_segment(plaintext)
        encrypt_time = time.time() - start

        # Decrypt
        start = time.time()
        decrypted = encryptor.decrypt_segment(encrypted)
        decrypt_time = time.time() - start

        # Verify
        assert decrypted == plaintext, "Decryption failed!"

        overhead = len(encrypted) - len(plaintext)
        overhead_pct = (overhead / len(plaintext)) * 100

        print(f"{description}:")
        print(f"  Original size:  {len(plaintext):,} bytes")
        print(f"  Encrypted size: {len(encrypted):,} bytes")
        print(f"  Overhead:       {overhead} bytes ({overhead_pct:.4f}%)")
        print(f"  Encrypt time:   {encrypt_time*1000:.2f} ms")
        print(f"  Decrypt time:   {decrypt_time*1000:.2f} ms")
        print(f"  Encrypt speed:  {(size/1024/1024)/encrypt_time:.1f} MB/s")
        print(f"  Decrypt speed:  {(size/1024/1024)/decrypt_time:.1f} MB/s")
        print()

    print("✅ All tests passed!")
