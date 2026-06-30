#!/usr/bin/env python3
"""
Encrypted Video Transmuxer - Convert existing video to encrypted fMP4

TRANSMUXING MODE (minimal re-encoding):
1. Takes existing video file (MP4, MKV, etc.)
2. Uses GstDiscoverer to detect codecs
3. Transmuxes H.264/H.265 video without re-encoding
4. Transmuxes Opus audio or transcodes AAC/MP3 → Opus
5. Encrypts segments on-the-fly

Output: H.264/H.265 + Opus (encrypted)

Use cases:
- RTSP camera streams (H.264/H.265)
- Conference recordings (convert to Opus for better quality)
- Existing video files (fast conversion)

SECURITY: No unencrypted temp files
PERFORMANCE: ~10-50x realtime (video transmux, audio may transcode)
"""

import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstPbutils', '1.0')
from gi.repository import Gst, GstPbutils, GLib

import os
import sys
import json
import time
import argparse
from pathlib import Path
from typing import Dict, List, Optional

# Import streaming parser
from streaming_mp4_parser import StreamingMP4Parser

# Import crypto utilities
from crypto_utils import SegmentEncryptor
from rsa_utils import import_public_key_pem, encrypt_aes_key


class CodecInfo:
    """Detected codec information"""
    def __init__(self):
        self.video_codec = None  # 'h264' or 'h265'
        self.audio_codec = None  # 'opus', 'aac', 'mp3', etc
        self.video_caps = None
        self.audio_caps = None


class TransmuxEncoder:
    """Transmux existing video to encrypted fMP4 blob"""

    def __init__(
        self,
        input_file: str,
        output_dir: str,
        fragment_duration_ms: int = 2000,
        public_key_path: str = None,
    ):
        self.input_file = Path(input_file)
        if not self.input_file.exists():
            raise FileNotFoundError(f"Input file not found: {input_file}")

        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.fragment_duration_ms = fragment_duration_ms

        # Output files
        self.blob_path = self.output_dir / "segments.blob"
        self.manifest_path = self.output_dir / "manifest.json"

        # Encryption
        self.encryptor = SegmentEncryptor()

        # RSA public key (optional - for AES key encryption)
        self.public_key = None
        if public_key_path:
            with open(public_key_path, 'r') as f:
                pem_data = f.read()
            self.public_key = import_public_key_pem(pem_data)
            print(f"🔑 Loaded RSA public key: {public_key_path}")

        # Streaming parser
        self.parser = StreamingMP4Parser()

        # Blob file handle (write segments as they come)
        self.blob_file = None
        self.current_offset = 0

        # Manifest data
        self.manifest = {
            "init_segment": None,
            "segments": [],
            "video": {},
            "audio": {},
            "total_duration": 0,
            "blob_file": "segments.blob"
        }

        # Codec info (detected by discoverer)
        self.codec_info = None

        # Statistics
        self.segment_count = 0
        self.total_plaintext_size = 0
        self.total_encrypted_size = 0
        self.start_time = None

        # GStreamer
        Gst.init(None)
        self.pipeline = None
        self.loop = None

    def discover_codecs(self) -> CodecInfo:
        """Use GstDiscoverer to detect video/audio codecs"""
        print(f"🔍 Analyzing input file: {self.input_file}")

        discoverer = GstPbutils.Discoverer.new(5 * Gst.SECOND)
        uri = self.input_file.resolve().as_uri()

        try:
            info = discoverer.discover_uri(uri)
        except Exception as e:
            raise RuntimeError(f"Failed to discover file: {e}")

        codec_info = CodecInfo()

        # Get video stream
        video_streams = info.get_video_streams()
        if not video_streams:
            raise RuntimeError("No video stream found in file")

        video_stream = video_streams[0]
        video_caps = video_stream.get_caps()
        codec_info.video_caps = video_caps

        # Detect video codec
        structure = video_caps.get_structure(0)
        video_format = structure.get_name()

        if 'h264' in video_format or 'avc' in video_format:
            codec_info.video_codec = 'h264'
            print(f"✓ Video: H.264 (will transmux)")
        elif 'h265' in video_format or 'hevc' in video_format:
            codec_info.video_codec = 'h265'
            print(f"✓ Video: H.265 (will transmux)")
        else:
            raise RuntimeError(f"Unsupported video codec: {video_format}. Only H.264 and H.265 are supported.")

        # Get audio stream
        audio_streams = info.get_audio_streams()
        if not audio_streams:
            raise RuntimeError("No audio stream found in file")

        audio_stream = audio_streams[0]
        audio_caps = audio_stream.get_caps()
        codec_info.audio_caps = audio_caps

        # Detect audio codec
        structure = audio_caps.get_structure(0)
        audio_format = structure.get_name()

        if 'opus' in audio_format:
            codec_info.audio_codec = 'opus'
            print(f"✓ Audio: Opus (will transmux)")
        elif 'mpeg' in audio_format or 'aac' in audio_format:
            codec_info.audio_codec = 'aac'
            print(f"✓ Audio: AAC (will transcode to Opus)")
        elif 'mp3' in audio_format:
            codec_info.audio_codec = 'mp3'
            print(f"✓ Audio: MP3 (will transcode to Opus)")
        else:
            print(f"⚠️  Audio: {audio_format} (will attempt to transcode to Opus)")
            codec_info.audio_codec = 'unknown'

        return codec_info

    def build_pipeline(self):
        """Build GStreamer pipeline based on detected codecs"""

        # Video pipeline (transmux only)
        if self.codec_info.video_codec == 'h264':
            video_pipeline = "h264parse"
        elif self.codec_info.video_codec == 'h265':
            video_pipeline = "h265parse"
        else:
            raise RuntimeError(f"Unsupported video codec: {self.codec_info.video_codec}")

        # Audio pipeline (transmux or transcode)
        if self.codec_info.audio_codec == 'opus':
            # Transmux Opus
            audio_pipeline = "opusparse"
        else:
            # Transcode to Opus
            # Decode → convert → encode
            audio_pipeline = "decodebin ! audioconvert ! audioresample ! opusenc ! opusparse"

        # Full pipeline
        pipeline_str = f"""
        filesrc location="{self.input_file}" ! qtdemux name=demux

        demux.video_0 ! queue ! {video_pipeline} ! mp4mux name=mux
        demux.audio_0 ! queue ! {audio_pipeline} ! mux.

        mux. ! appsink name=sink emit-signals=true sync=false
        """

        print(f"\n📝 Pipeline:")
        print(f"   Video: {video_pipeline}")
        print(f"   Audio: {audio_pipeline}\n")

        self.pipeline = Gst.parse_launch(pipeline_str)

        # Configure mp4mux for fragmentation
        mux = self.pipeline.get_by_name('mux')
        mux.set_property('fragment-duration', self.fragment_duration_ms)
        mux.set_property('streamable', True)

        # Configure appsink
        sink = self.pipeline.get_by_name('sink')
        sink.set_property('emit-signals', True)
        sink.set_property('sync', False)
        sink.connect('new-sample', self.on_new_sample)

        # Bus messages
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect('message', self.on_bus_message)

    def on_new_sample(self, sink):
        """Callback when GStreamer emits new data"""
        sample = sink.emit('pull-sample')
        buffer = sample.get_buffer()

        # Extract data
        success, map_info = buffer.map(Gst.MapFlags.READ)
        if not success:
            return Gst.FlowReturn.ERROR

        data = bytes(map_info.data)
        buffer.unmap(map_info)

        # Feed to streaming parser with callbacks
        self.parser.feed(
            data,
            on_init=self.on_init_segment,
            on_segment=self.on_media_segment
        )

        return Gst.FlowReturn.OK

    def on_bus_message(self, bus, message):
        """Handle GStreamer bus messages"""
        t = message.type

        if t == Gst.MessageType.EOS:
            elapsed = time.time() - self.start_time
            print(f"\n⏹️  End of stream ({elapsed:.1f}s)")
            self.loop.quit()
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print(f"\n❌ Error: {err}")
            print(f"Debug: {debug}")
            self.loop.quit()

    def on_init_segment(self, data: bytes):
        """Callback when init segment is ready"""
        print(f"[Parser] Init segment ready: {len(data)} bytes")

        # Encrypt init segment
        print(f"🔐 Encrypting init segment ({len(data)} bytes)...")
        encrypted = self.encryptor.encrypt_segment(data)

        # Write to blob
        self.blob_file.write(encrypted)

        # Save to manifest
        self.manifest["init_segment"] = {
            "offset": self.current_offset,
            "size": len(encrypted)
        }

        self.current_offset += len(encrypted)
        print(f"   ✓ Init: {len(data)} → {len(encrypted)} bytes (+{len(encrypted) - len(data)})")

    def on_media_segment(self, data: bytes, duration: float):
        """Callback when media segment is ready"""
        size_kb = len(data) / 1024

        # Encrypt segment
        start_time = time.time()
        encrypted = self.encryptor.encrypt_segment(data)
        encrypt_time_ms = (time.time() - start_time) * 1000

        # Write to blob
        self.blob_file.write(encrypted)

        # Save to manifest
        self.manifest["segments"].append({
            "offset": self.current_offset,
            "size": len(encrypted),
            "duration": duration
        })

        self.current_offset += len(encrypted)
        self.segment_count += 1
        self.total_plaintext_size += len(data)
        self.total_encrypted_size += len(encrypted)

        encrypted_kb = len(encrypted) / 1024
        print(f"🔐 Segment {self.segment_count - 1} ({size_kb:.1f} KB, {duration:.4f}s) → "
              f"{encrypted_kb:.1f} KB (+{len(encrypted) - len(data)}) in {encrypt_time_ms:.1f}ms")

    def save_manifest(self):
        """Save manifest JSON"""
        # Calculate total duration
        total_duration = sum(seg["duration"] for seg in self.manifest["segments"])
        self.manifest["total_duration"] = total_duration

        # Video/audio info (from detected codecs)
        if self.codec_info.video_codec == 'h264':
            video_codec_str = "avc1.64001f"  # H.264 baseline
        elif self.codec_info.video_codec == 'h265':
            video_codec_str = "hev1.1.6.L93.B0"  # H.265 main profile

        self.manifest["video"] = {
            "codec": video_codec_str,
            "timescale": self.parser.video_timescale
        }

        self.manifest["audio"] = {
            "codec": "opus",  # Always Opus output
            "timescale": 48000  # Opus standard
        }

        # Encryption info
        encryption_info = {}
        if self.public_key:
            # Encrypt AES key with RSA
            aes_key_bytes = bytes.fromhex(self.encryptor.get_key_hex())
            encrypted_aes = encrypt_aes_key(aes_key_bytes, self.public_key)
            encryption_info["encrypted_key_hex"] = encrypted_aes.hex()
            encryption_info["key_encryption"] = "RSA-OAEP-SHA256"
            encryption_info["note"] = "Decrypt encrypted_key_hex with RSA private key to get AES key"
        else:
            # Plaintext key (for testing)
            encryption_info["key_hex"] = self.encryptor.get_key_hex()
            encryption_info["note"] = "WARNING: Unencrypted AES key! Use --public-key for production"

        encryption_info["algorithm"] = "AES-256-GCM"
        encryption_info["iv_size"] = 12
        encryption_info["tag_size"] = 16

        self.manifest["encryption"] = encryption_info

        # Write manifest
        with open(self.manifest_path, 'w') as f:
            json.dump(self.manifest, f, indent=2)

        print(f"\n✅ Manifest saved: {self.manifest_path}")

    def run(self):
        """Run the transmuxing process"""
        print("=" * 80)
        print("🎬 Encrypted fMP4 Transmuxer")
        print("=" * 80)
        print(f"Input: {self.input_file}")
        print(f"Output: {self.output_dir}")
        print(f"Target: H.264/H.265 + Opus (encrypted)\n")

        # Discover codecs
        self.codec_info = self.discover_codecs()

        # Open blob file for writing
        self.blob_file = open(self.blob_path, 'wb')

        # Build and start pipeline
        self.build_pipeline()

        print("▶️  Running GStreamer pipeline...")
        mode = "transmux" if self.codec_info.audio_codec == 'opus' else "transmux video + transcode audio"
        print(f"    Mode: {mode}\n")

        self.start_time = time.time()
        self.pipeline.set_state(Gst.State.PLAYING)

        # Run event loop
        self.loop = GLib.MainLoop()

        try:
            self.loop.run()
        except KeyboardInterrupt:
            print("\n⏸️  Interrupted by user")

        # Cleanup
        self.pipeline.set_state(Gst.State.NULL)
        self.blob_file.close()

        # Save manifest
        self.save_manifest()

        # Statistics
        elapsed = time.time() - self.start_time
        realtime_factor = self.manifest['total_duration'] / elapsed if elapsed > 0 else 0

        print("\n" + "=" * 80)
        print("📊 Summary")
        print("=" * 80)
        print(f"Input file: {self.input_file}")
        print(f"Video codec: {self.codec_info.video_codec.upper()} (transmuxed)")
        print(f"Audio codec: {self.codec_info.audio_codec.upper()} → Opus {'(transmuxed)' if self.codec_info.audio_codec == 'opus' else '(transcoded)'}")
        print(f"Segments: {self.segment_count}")
        print(f"Total duration: {self.manifest['total_duration']:.2f}s")
        print(f"Processing time: {elapsed:.2f}s ({realtime_factor:.1f}x realtime)")
        print(f"Plaintext size: {self.total_plaintext_size / 1024 / 1024:.2f} MB")
        print(f"Encrypted size: {self.total_encrypted_size / 1024 / 1024:.2f} MB")
        overhead = ((self.total_encrypted_size / self.total_plaintext_size) - 1) * 100
        print(f"Encryption overhead: {overhead:.3f}%")
        print(f"\n✅ Done! Encrypted blob: {self.blob_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Transmux video to encrypted fMP4 (H.264/H.265 + Opus)"
    )
    parser.add_argument(
        "-i", "--input",
        required=True,
        help="Input video file (must contain H.264/H.265 video)"
    )
    parser.add_argument(
        "-o", "--output",
        default="output_transmux",
        help="Output directory (default: output_transmux)"
    )
    parser.add_argument(
        "-f", "--fragment-duration",
        type=int,
        default=2000,
        help="Fragment duration in milliseconds (default: 2000)"
    )
    parser.add_argument(
        "-k", "--public-key",
        help="RSA public key PEM file (for AES key encryption)"
    )

    args = parser.parse_args()

    encoder = TransmuxEncoder(
        input_file=args.input,
        output_dir=args.output,
        fragment_duration_ms=args.fragment_duration,
        public_key_path=args.public_key
    )

    encoder.run()


if __name__ == "__main__":
    main()
