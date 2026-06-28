#!/usr/bin/env python3
"""
RSA key generation and AES key encryption utilities.

Используется для защиты AES ключей:
1. Генерируем RSA пару (public/private)
2. Encoder шифрует AES ключ публичным ключом
3. Player расшифровывает приватным ключом (вводит пользователь)
"""

from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.backends import default_backend


def generate_rsa_keypair(key_size: int = 2048):
    """
    Генерирует RSA пару ключей

    Returns:
        (private_key, public_key) tuple
    """
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=key_size,
        backend=default_backend()
    )
    public_key = private_key.public_key()

    return private_key, public_key


def export_private_key_pem(private_key) -> str:
    """Экспорт приватного ключа в PEM формат"""
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    return pem.decode('utf-8')


def export_public_key_pem(public_key) -> str:
    """Экспорт публичного ключа в PEM формат"""
    pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return pem.decode('utf-8')


def import_public_key_pem(pem_string: str):
    """Импорт публичного ключа из PEM"""
    return serialization.load_pem_public_key(
        pem_string.encode('utf-8'),
        backend=default_backend()
    )


def import_private_key_pem(pem_string: str):
    """Импорт приватного ключа из PEM"""
    return serialization.load_pem_private_key(
        pem_string.encode('utf-8'),
        password=None,
        backend=default_backend()
    )


def encrypt_aes_key(aes_key: bytes, public_key) -> bytes:
    """
    Шифрует AES ключ публичным RSA ключом

    Args:
        aes_key: 32-byte AES-256 key
        public_key: RSA public key

    Returns:
        Encrypted AES key (256 bytes for RSA-2048)
    """
    encrypted = public_key.encrypt(
        aes_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None
        )
    )
    return encrypted


def decrypt_aes_key(encrypted_key: bytes, private_key) -> bytes:
    """
    Расшифровывает AES ключ приватным RSA ключом

    Args:
        encrypted_key: Encrypted AES key
        private_key: RSA private key

    Returns:
        Decrypted AES key (32 bytes)
    """
    decrypted = private_key.decrypt(
        encrypted_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None
        )
    )
    return decrypted


if __name__ == "__main__":
    import os

    print("=== RSA Key Generation & AES Key Encryption Test ===\n")

    # 1. Генерируем RSA пару
    print("1. Generating RSA keypair (2048-bit)...")
    private_key, public_key = generate_rsa_keypair()
    print("   ✓ Keypair generated\n")

    # 2. Экспортируем ключи
    private_pem = export_private_key_pem(private_key)
    public_pem = export_public_key_pem(public_key)

    print("2. Public key (PEM):")
    print(public_pem)

    print("3. Private key (PEM) - first 10 lines:")
    print('\n'.join(private_pem.split('\n')[:10]))
    print("   ...\n")

    # 3. Тест шифрования AES ключа
    print("4. Testing AES key encryption...")
    aes_key = os.urandom(32)  # 256-bit AES key
    print(f"   Original AES key: {aes_key.hex()[:32]}...")

    encrypted_aes = encrypt_aes_key(aes_key, public_key)
    print(f"   Encrypted size: {len(encrypted_aes)} bytes")
    print(f"   Encrypted (hex): {encrypted_aes.hex()[:64]}...")

    # 4. Тест расшифровки
    print("\n5. Testing AES key decryption...")
    decrypted_aes = decrypt_aes_key(encrypted_aes, private_key)
    print(f"   Decrypted AES key: {decrypted_aes.hex()[:32]}...")

    if aes_key == decrypted_aes:
        print("   ✓ Decryption successful - keys match!\n")
    else:
        print("   ✗ ERROR: Keys don't match!\n")

    print("✅ All tests passed!")
