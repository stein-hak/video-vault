# 🔐 Encrypted Video Blob Encoder (Production)

Zero-knowledge encrypted video storage system for private conference recordings and CCTV footage.

## 📋 Overview

**Problem:** Need to store private video recordings (conferences, CCTV) in untrusted cloud storage (AWS S3, Google Cloud) where the cloud provider cannot access the content.

**Solution:** End-to-end encrypted video blob with RSA-protected AES keys and streaming fMP4 format.

## 🎯 Use Cases

✅ **Private conference recordings** - Store in cloud, only authorized viewers can decrypt
✅ **CCTV/Surveillance footage** - Zero-knowledge cloud backup
✅ **Compliance scenarios** - GDPR, HIPAA encrypted at rest
✅ **Selective sharing** - Share private key = grant access
✅ **Multi-tenant security** - Each recording has unique keypair

❌ **NOT for:** Commercial DRM, copy protection, streaming services

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        ENCODER                              │
│                                                             │
│  GStreamer (fMP4) → StreamingMP4Parser (pymp4)             │
│         ↓                    ↓                              │
│  Video chunks      Extract segments + durations            │
│         ↓                    ↓                              │
│  AES-256-GCM encryption (random key)                       │
│         ↓                    ↓                              │
│  Encrypted blob      RSA-OAEP encrypt AES key              │
│         ↓                    ↓                              │
│  segments.blob       manifest.json                         │
└─────────────────────────────────────────────────────────────┘
                         ↓ Upload
┌─────────────────────────────────────────────────────────────┐
│              UNTRUSTED CLOUD STORAGE (S3)                   │
│                                                             │
│  📦 segments.blob        ← AES-256-GCM encrypted           │
│  📄 manifest.json        ← Contains encrypted_key_hex      │
│                                                             │
│  ❌ Cloud provider CANNOT decrypt (no private key)         │
│  ❌ Breach = useless encrypted data                        │
└─────────────────────────────────────────────────────────────┘
                         ↓ Download
┌─────────────────────────────────────────────────────────────┐
│                     PLAYER (Browser)                        │
│                                                             │
│  User pastes private_key.pem (RSA private key)             │
│         ↓                                                   │
│  RSA-OAEP decrypt → AES key                                │
│         ↓                                                   │
│  Fetch segments → AES-256-GCM decrypt                      │
│         ↓                                                   │
│  MSE (Media Source Extensions) → Video playback            │
└─────────────────────────────────────────────────────────────┘
```

## 🔐 Security Model

### Encryption Layers

1. **AES-256-GCM** - Segment encryption
   - Random 32-byte key per recording
   - 12-byte IV per segment (random)
   - 16-byte authentication tag
   - Overhead: 28 bytes/segment (~0.016%)

2. **RSA-OAEP-SHA256** - Key protection
   - 2048-bit RSA keypair
   - AES key encrypted with public key
   - Only private key holder can decrypt

### Key Separation

```
Cloud storage:
  ✅ segments.blob (encrypted with AES)
  ✅ manifest.json (contains encrypted_key_hex)
  ❌ AES key (encrypted, useless without private key)
  ❌ Private RSA key (NEVER uploaded)

Client (authorized user):
  ✅ private_key.pem (stored securely offline)
  ✅ Can decrypt AES key
  ✅ Can decrypt and view video
```

### Zero-Knowledge Properties

- **Cloud provider cannot decrypt** - No access to private RSA keys
- **Breach resistance** - Stolen encrypted blobs are cryptographically useless
- **Selective access** - Share private key = grant access to specific recording
- **Key independence** - Each recording can have unique keypair

## 🚀 Quick Start

### 1. Generate RSA Keypair

```bash
# Generate keypair (once per recording or shared across recordings)
python3 keygen.py -o keys/recording-001/

# Output:
#   keys/recording-001/public_key.pem   (for encoder)
#   keys/recording-001/private_key.pem  (for viewer - KEEP SECRET!)
```

### 2. Encode Video

```bash
# Test pattern (development)
python3 encoder.py \
    -o output \
    -d 60 \
    --public-key keys/recording-001/public_key.pem

# Results:
#   output/segments.blob    - Encrypted video data
#   output/manifest.json    - Metadata + encrypted AES key
```

**Parameters:**
- `-o, --output DIR` - Output directory
- `-d, --duration SECONDS` - Video duration (for test pattern)
- `-f, --fragment-duration MS` - Fragment size (default: 2000ms)
- `--public-key FILE` - RSA public key (PEM format)
- `--aac` - Use AAC instead of Opus audio

### 3. Upload to Cloud

```bash
# Upload to S3
aws s3 sync output/ s3://my-bucket/recordings/rec-001/

# Or any cloud storage (Google Cloud, Azure, etc)
```

### 4. Play Video

```bash
# Download from cloud
aws s3 sync s3://my-bucket/recordings/rec-001/ ./local/

# Start HTTP server
cd ./local
python3 server.py -p 8000

# Open browser: http://localhost:8000/player.html
# Click "Load Encrypted Video"
# Paste private_key.pem content
# Click "Unlock & Load Video"
```

## 📁 Project Structure

```
encoder-production/
├── README.md                    # This file
│
├── encoder.py                   # Main encoder (streaming fMP4 + encryption)
├── streaming_mp4_parser.py      # Incremental MP4 parser (pymp4-based)
├── crypto_utils.py              # AES-256-GCM encryption
├── rsa_utils.py                 # RSA key generation and AES key encryption
├── keygen.py                    # CLI tool to generate RSA keypairs
│
├── player.html                  # Browser-based player (RSA + AES decryption)
├── server.py                    # HTTP server with Range request support
│
└── [Generated files]
    ├── public_key.pem           # RSA public key (safe to share)
    ├── private_key.pem          # RSA private key (SECRET!)
    └── output/
        ├── segments.blob        # Encrypted video data
        ├── manifest.json        # Metadata + encrypted key
        └── player.html          # Copy of player
```

## 🔧 Technical Details

### Streaming Architecture

**Constant Memory Usage:**
- Video processed in chunks (~2s fragments)
- Each segment encrypted immediately and written to blob
- No accumulation in RAM
- Memory: O(segment_size) ≈ 260KB regardless of video length

**Performance:**
- Encoding speed: ~4x realtime (with sync=false)
- 60s video → ~15s encoding
- 5 min video → ~77s encoding

**fMP4 Structure:**
```
[ftyp][moov] ← Init segment (loaded once)
[moof][mdat] ← Media segment 0 (video frame)
[moof][mdat] ← Media segment 1 (audio-only)
[moof][mdat] ← Media segment 2 (video frame)
...
```

### Accurate Duration Parsing

Uses **pymp4** library to parse MP4 boxes:
- Extracts timescale from `moov/trak/mdia/mdhd`
- Parses sample durations from `moof/traf/trun`
- Calculates: `duration_seconds = sum(sample_durations) / timescale`
- Result: Accurate total_duration in manifest (not estimated)

### Encryption Format

**Encrypted segment structure:**
```
[IV (12 bytes)][Ciphertext (variable)][Auth Tag (16 bytes)]
```

**Manifest encryption section:**
```json
{
  "encryption": {
    "method": "AES-256-GCM",
    "iv_length": 12,
    "tag_length": 16,
    "key_encryption": "RSA-OAEP-SHA256",
    "encrypted_key_hex": "a73c5a7c..."  // AES key encrypted with RSA
  }
}
```

**Without RSA (backward compatibility):**
```json
{
  "encryption": {
    "method": "AES-256-GCM",
    "key_hex": "b1f4459ae..."  // Plaintext AES key
  }
}
```

## 🎯 Production Recommendations

### Key Management

**Per-recording keypairs (recommended):**
```bash
# Each recording/stream gets unique keypair
keygen.py -o keys/recording-001/
keygen.py -o keys/recording-002/
keygen.py -o keys/camera-001/
```

**Benefits:**
- Breach of one key ≠ access to all recordings
- Selective revocation (delete specific private key)
- Per-tenant isolation

**Shared keypair (simpler):**
```bash
# One keypair for all recordings
keygen.py -o keys/master/
```

**Benefits:**
- Simpler management
- One private key for all authorized users

### Private Key Storage

**Options (in order of security):**

1. **Hardware Security Module (HSM)** - Best for enterprise
2. **Password manager** (1Password, BitWarden) - Good for teams
3. **Encrypted vault** - Good for personal use
4. **Paper backup** (safe deposit box) - Cold storage

⚠️ **CRITICAL:** Loss of private key = permanent data loss!

**Backup strategy:**
```
Primary:  Password manager (cloud, encrypted)
Backup:   USB drive (offline, encrypted)
Recovery: Paper printout (safe deposit box)
```

### Multi-recipient Access

**Encrypt AES key with multiple RSA keys:**

Modify `encoder.py` to support:
```json
{
  "encryption": {
    "method": "AES-256-GCM",
    "recipients": [
      {
        "id": "admin",
        "encrypted_key_hex": "a73c5a7c..."
      },
      {
        "id": "participant1",
        "encrypted_key_hex": "f8d9b2e4..."
      },
      {
        "id": "participant2",
        "encrypted_key_hex": "c1a8f5d3..."
      }
    ]
  }
}
```

Each recipient can decrypt with their own private key.

### Metadata Protection

**Hide sensitive info in filenames/manifest:**

```bash
# ❌ Bad (leaks info):
s3://bucket/sensitive-recording-title/manifest.json

# ✅ Good (obscured):
s3://bucket/a7f3c9e1-4b2d-8f5a-9c1e-3d7b2f8a4e6c/manifest.json
```

**Manifest should use UUIDs:**
```json
{
  "id": "a7f3c9e1-4b2d-8f5a-9c1e-3d7b2f8a4e6c",
  // Don't include: title, participants, room name, etc.
}
```

Store metadata separately (encrypted or access-controlled).

### Cloud Upload

```bash
# S3 with server-side encryption (defense in depth)
aws s3 sync output/ \
    s3://bucket/recording-id/ \
    --sse AES256

# Set lifecycle policy (auto-delete after N days)
aws s3api put-bucket-lifecycle-configuration \
    --bucket my-recordings \
    --lifecycle-configuration file://lifecycle.json
```

### Audit Logging

**Log access (but NOT keys!):**
```python
# ✅ Good:
log.info(f"Video {video_id} accessed from IP {ip} at {timestamp}")

# ❌ Bad:
log.info(f"Decrypted with key: {aes_key_hex}")  # NEVER log keys!
```

## 📊 Security Evaluation

### Threat Model

| Attack Vector | Protection | Effectiveness |
|---------------|------------|---------------|
| **Cloud provider breach** | Encrypted blob, no keys in cloud | ✅ 10/10 |
| **S3 bucket misconfiguration** | Data encrypted at rest | ✅ 10/10 |
| **Network interception** | HTTPS for download | ✅ 10/10 |
| **Key theft (private key)** | Secure key storage required | ⚠️ Depends on user |
| **Brute force** | AES-256 + RSA-2048 | ✅ 10/10 |
| **Client-side attack** | After decrypt = plaintext | ⚠️ 4/10 (expected) |

### What This Protects

✅ **Storage security (at rest)** - Cloud cannot read data
✅ **Breach resistance** - Stolen files are useless
✅ **Unauthorized access** - Need private key to view
✅ **Compliance** - GDPR/HIPAA encrypted storage

### What This Does NOT Protect

❌ **Screen recording** - After decrypt, user can record
❌ **Authorized sharing** - User can share decrypted content
❌ **Hardware DRM** - No Widevine/FairPlay protection
❌ **Forensic watermarking** - No per-user tracking

**This is zero-knowledge storage, not commercial DRM.**

## 🔮 Future Enhancements

### 1. Transmuxing Mode (CRITICAL for production)

**Current:** Video encoding (slow, CPU-intensive)
```
videotestsrc → x264enc → mp4mux → encrypt
```

**Future:** Transmuxing only (fast, minimal CPU)
```
RTSP camera (H264) → h264parse → mp4mux → encrypt
IP camera          → rtph264depay → mp4mux → encrypt
Existing file.mp4  → qtdemux → mp4mux → encrypt
```

**Benefits:**
- 100x+ faster (transmux vs transcode)
- Original quality preserved
- Real-time encryption for live streams
- Minimal CPU usage

**Implementation:**
```bash
# RTSP camera
python3 encoder.py \
    --input-type rtsp \
    --input-source rtsp://camera.local/stream \
    --public-key public_key.pem

# Existing file
python3 encoder.py \
    --input-type file \
    --input-source meeting.mp4 \
    --public-key public_key.pem
```

### 2. Progressive Download Player

**Current:** Load entire video before playback

**Future:** Stream-on-demand
- Fetch segments as needed (Range requests)
- Seek support (jump to timestamp)
- Lower initial latency

### 3. Multi-recipient Support

**Encrypt AES key with multiple RSA public keys:**
- Admin key
- Participant keys
- Department keys

Each can decrypt independently.

### 4. Key Rotation

**Periodic re-encryption:**
```bash
# Generate new keypair
keygen.py -o keys/new-rotation/

# Re-encrypt existing recordings
rotate-keys.py \
    --old-key keys/old-rotation/private_key.pem \
    --new-key keys/new-rotation/public_key.pem \
    --input s3://bucket/
```

### 5. Hardware Acceleration

**GPU-accelerated encoding (when transcoding needed):**
- NVENC (NVIDIA)
- Quick Sync (Intel)
- AMF (AMD)

## 🐛 Troubleshooting

### Player shows "Private Key Required" but video isn't RSA encrypted

**Check manifest.json:**
```bash
cat output/manifest.json | grep "key_encryption"
```

- If `"key_encryption": "RSA-OAEP-SHA256"` → Need private key
- If no `key_encryption` field → Uses plaintext `key_hex`

**Solution:** Re-encode with `--public-key` flag

### "Decryption failed" error in player

**Causes:**
1. Wrong private key (for different recording)
2. Corrupted blob file
3. Network error during download

**Debug:**
```bash
# Verify blob integrity
sha256sum output/segments.blob

# Check manifest
cat output/manifest.json | python3 -m json.tool
```

### Video duration is wrong

**Older versions:** Used fragment_duration estimate (inaccurate)

**Current version:** Uses pymp4 parser for accurate durations

**Verify:**
```json
// manifest.json
{
  "total_duration": 60.0,  // Should match actual video length
  "segments": [
    {"id": 0, "duration": 1.0},   // Video segment
    {"id": 1, "duration": 0.0},   // Audio-only segment
    ...
  ]
}
```

Audio-only segments have `duration: 0.0` (correct).

### Out of memory during encoding

**Cause:** Not using streaming mode

**Check:** Old encoder versions accumulated data

**Solution:** Use current encoder (streaming mode)
```bash
# Should see during encoding:
📦 Parser buffer (max): 0.0 KB  # ← Constant memory!
```

## 📚 Dependencies

**Python packages:**
```bash
pip install cryptography  # AES-GCM, RSA
pip install pymp4        # MP4 parsing
pip install pygobject    # GStreamer bindings
```

**System packages:**
```bash
# Ubuntu/Debian
sudo apt install gstreamer1.0-tools \
                 gstreamer1.0-plugins-base \
                 gstreamer1.0-plugins-good \
                 gstreamer1.0-plugins-bad \
                 gstreamer1.0-plugins-ugly \
                 gstreamer1.0-libav

# For Opus support
sudo apt install gstreamer1.0-plugins-opus
```

## 📄 License

MIT License

Copyright (c) 2026

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

---

**Note:** This project was developed independently in personal time on personal equipment.

## 🤝 Contributing

*Add contribution guidelines*

## 📞 Support

*Add contact info*

---

**Created:** 2026-06-28
**Last Updated:** 2026-06-28
**Version:** 1.0.0 (Production-ready)
