#!/usr/bin/env python3
"""
Генератор RSA ключей для защиты видео

Создает пару ключей:
- public_key.pem - используется encoder'ом для шифрования AES ключа
- private_key.pem - используется плеером для расшифровки (вводит пользователь)
"""

import argparse
from pathlib import Path
from rsa_utils import generate_rsa_keypair, export_private_key_pem, export_public_key_pem


def main():
    parser = argparse.ArgumentParser(description='Generate RSA keypair for video encryption')
    parser.add_argument('-o', '--output-dir', default='.', help='Output directory for keys')
    parser.add_argument('-s', '--key-size', type=int, default=2048, choices=[2048, 4096],
                       help='RSA key size (default: 2048)')

    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("🔐 RSA Keypair Generator")
    print("=" * 60)
    print(f"Key size: {args.key_size}-bit")
    print(f"Output: {output_dir.absolute()}/")
    print()

    # Генерируем ключи
    print("Generating RSA keypair...")
    private_key, public_key = generate_rsa_keypair(key_size=args.key_size)
    print("✓ Keypair generated")
    print()

    # Экспортируем
    private_pem = export_private_key_pem(private_key)
    public_pem = export_public_key_pem(public_key)

    # Сохраняем
    public_path = output_dir / "public_key.pem"
    private_path = output_dir / "private_key.pem"

    with open(public_path, 'w') as f:
        f.write(public_pem)

    with open(private_path, 'w') as f:
        f.write(private_pem)

    print(f"✅ Keys saved:")
    print(f"   📄 Public key:  {public_path}")
    print(f"   🔒 Private key: {private_path}")
    print()
    print("⚠️  IMPORTANT:")
    print("   - Use public_key.pem with encoder (--public-key)")
    print("   - Keep private_key.pem SECRET - needed for playback!")
    print("   - Users will need to paste private key into player")
    print()


if __name__ == '__main__':
    main()
