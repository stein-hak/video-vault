#!/usr/bin/env python3
"""
Encrypted Video Encoder - Variant 3: Streaming fMP4 (PRODUCTION)

Creates encrypted fMP4 blob with CONSTANT memory usage:
1. GStreamer → appsink → streaming parser
2. Parser emits segments as they're ready → encrypt → write to blob
3. NO accumulation - constant memory regardless of video length

SECURITY: No unencrypted temp files
PERFORMANCE: Constant memory usage (suitable for hours of video)
"""

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

import os
import sys
import json
import time
from pathlib import Path
from typing import Dict, List

# Import streaming parser
from streaming_mp4_parser import StreamingMP4Parser

# Import crypto utilities
from crypto_utils import SegmentEncryptor
from rsa_utils import import_public_key_pem, encrypt_aes_key


class StreamingFMP4Encoder:
    """Encode video to encrypted fMP4 blob with streaming processing"""

    def __init__(
        self,
        output_dir: str,
        duration_seconds: int = 10,
        fragment_duration_ms: int = 2000,
        test_pattern: str = "smpte",
        use_opus: bool = True,
        public_key_path: str = None,
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.duration_seconds = duration_seconds
        self.fragment_duration_ms = fragment_duration_ms
        self.test_pattern = test_pattern
        self.use_opus = use_opus

        # Calculate number of buffers
        self.video_fps = 30
        self.video_buffers = duration_seconds * self.video_fps
        self.audio_buffers = int(duration_seconds * 46.875)

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
        self.init_info = None
        self.segments_info = []

        # GStreamer
        Gst.init(None)
        self.pipeline = None
        self.loop = None
        self.appsink = None

    def on_init_segment(self, init_data: bytes):
        """Callback когда init segment готов"""
        print(f"\n🔐 Encrypting init segment ({len(init_data)} bytes)...")

        # Encrypt
        encrypted = self.encryptor.encrypt_segment(init_data)

        # Write to blob
        self.blob_file.write(encrypted)
        self.blob_file.flush()

        # Save info
        self.init_info = {
            "type": "init",
            "offset": 0,
            "size": len(encrypted),
            "plaintext_size": len(init_data),
            "overhead_bytes": len(encrypted) - len(init_data)
        }

        self.current_offset = len(encrypted)

        print(f"   ✓ Init: {len(init_data)} → {len(encrypted)} bytes (+{self.init_info['overhead_bytes']})")

    def on_media_segment(self, segment_data: bytes, duration: float = 0.0):
        """Callback когда media segment готов"""
        seg_id = len(self.segments_info)

        print(f"🔐 Encrypting segment {seg_id} ({len(segment_data)/1024:.1f} KB, {duration:.4f}s)...", end=" ")
        start = time.time()

        # Encrypt
        encrypted = self.encryptor.encrypt_segment(segment_data)

        # Write to blob
        self.blob_file.write(encrypted)
        self.blob_file.flush()

        encrypt_time = time.time() - start

        # duration уже распарсен из video track
        # audio-only сегменты будут иметь duration=0.0 (это нормально)

        # Save info
        seg_info = {
            "id": seg_id,
            "offset": self.current_offset,
            "size": len(encrypted),
            "duration": duration,
            "plaintext_size": len(segment_data),
            "overhead_bytes": len(encrypted) - len(segment_data)
        }

        self.segments_info.append(seg_info)
        self.current_offset += len(encrypted)

        print(f"✓ {len(encrypted)/1024:.1f} KB (+{seg_info['overhead_bytes']}) in {encrypt_time*1000:.1f}ms")

    def on_new_sample(self, appsink):
        """Callback for appsink - feed to streaming parser"""
        sample = appsink.emit('pull-sample')
        if sample:
            buffer = sample.get_buffer()
            success, map_info = buffer.map(Gst.MapFlags.READ)
            if success:
                self.parser.feed(
                    map_info.data,
                    on_init=self.on_init_segment,
                    on_segment=self.on_media_segment
                )
                buffer.unmap(map_info)
        return Gst.FlowReturn.OK

    def build_pipeline(self):
        if self.use_opus:
            audio_enc = "opusenc bitrate=128000"
        else:
            audio_enc = "avenc_aac bitrate=128000"

        pipeline_desc = f"""
            mp4mux name=mux
                fragment-duration={self.fragment_duration_ms}
                fragment-mode=dash-or-mss !
            appsink name=sink emit-signals=true sync=false

            videotestsrc num-buffers={self.video_buffers} pattern={self.test_pattern} is-live=false !
            video/x-raw,width=1920,height=1080,framerate={self.video_fps}/1,format=I420 !
            x264enc key-int-max={self.video_fps} tune=zerolatency speed-preset=ultrafast !
            video/x-h264,stream-format=avc,profile=main !
            h264parse !
            queue !
            mux.

            audiotestsrc num-buffers={self.audio_buffers} wave=sine freq=440 is-live=false !
            audio/x-raw,rate=48000,channels=2 !
            audioconvert !
            {audio_enc} !
            queue !
            mux.
        """

        pipeline = Gst.parse_launch(pipeline_desc)
        self.appsink = pipeline.get_by_name('sink')
        self.appsink.connect('new-sample', self.on_new_sample)
        return pipeline

    def run_pipeline(self):
        print("\n▶️  Running GStreamer pipeline (streaming mode)...")
        print("    Memory usage: O(segment_size) - constant!")

        self.blob_file = open(self.blob_path, 'wb')
        self.pipeline = self.build_pipeline()

        bus = self.pipeline.get_bus()
        bus.add_signal_watch()

        def on_message(bus, message):
            t = message.type
            if t == Gst.MessageType.EOS:
                print("\n✅ Pipeline finished")
                self.loop.quit()
            elif t == Gst.MessageType.ERROR:
                err, debug = message.parse_error()
                print(f"\n❌ Error: {err}")
                self.loop.quit()

        bus.connect("message", on_message)

        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("Failed to start pipeline")

        self.loop = GLib.MainLoop()
        start = time.time()

        try:
            self.loop.run()
        except KeyboardInterrupt:
            print("\n⚠️  Interrupted")

        elapsed = time.time() - start
        self.pipeline.set_state(Gst.State.NULL)
        self.blob_file.close()

        print(f"\n⏱️  Encoding time: {elapsed:.1f}s ({self.duration_seconds/elapsed:.2f}x realtime)")
        print(f"📦 Parser buffer (max): {self.parser.get_buffer_size() / 1024:.1f} KB")

    def save_manifest(self):
        total_overhead = (self.init_info['overhead_bytes'] +
                         sum(s['overhead_bytes'] for s in self.segments_info))
        total_size = self.init_info['size'] + sum(s['size'] for s in self.segments_info)

        # Подготовка encryption секции
        encryption_info = {
            "method": "AES-256-GCM",
            "iv_length": 12,
            "tag_length": 16,
        }

        # Если есть RSA ключ - шифруем AES ключ
        if self.public_key:
            aes_key_bytes = bytes.fromhex(self.encryptor.get_key_hex())
            encrypted_aes = encrypt_aes_key(aes_key_bytes, self.public_key)
            encryption_info["encrypted_key_hex"] = encrypted_aes.hex()
            encryption_info["key_encryption"] = "RSA-OAEP-SHA256"
            print(f"\n🔐 AES key encrypted with RSA ({len(encrypted_aes)} bytes)")
        else:
            # Plaintext key (backward compatibility)
            encryption_info["key_hex"] = self.encryptor.get_key_hex()

        manifest = {
            "version": "1.0",
            "format": "fmp4",
            "variant": "variant3-fmp4-streaming",
            "encryption": encryption_info,
            "video": {
                "codec": "avc1.4d001f",
                "width": 1920,
                "height": 1080,
                "framerate": self.video_fps
            },
            "audio": {
                "codec": "opus" if self.use_opus else "mp4a.40.2",
                "sample_rate": 48000,
                "channels": 2,
                "bitrate": 128000
            },
            "blob_file": "segments.blob",
            "blob_size": total_size,
            "init_segment": self.init_info,
            "segments": self.segments_info,
            "segment_count": len(self.segments_info),
            "total_duration": sum(s['duration'] for s in self.segments_info),  # Sum of parsed durations
            "encryption_overhead": {
                "total_bytes": total_overhead,
                "percentage": (total_overhead / total_size) * 100
            },
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }

        with open(self.manifest_path, 'w') as f:
            json.dump(manifest, f, indent=2)

        print(f"\n✅ Manifest saved: {self.manifest_path}")
        print(f"   Media segments: {len(self.segments_info)}")
        print(f"   Blob size: {total_size / 1024 / 1024:.2f} MB")

    def run(self):
        print("=" * 80)
        print("🎬 Encrypted fMP4 Blob Encoder - Variant 3 (STREAMING)")
        print("=" * 80)
        print(f"Output: {self.output_dir}")
        print(f"Duration: {self.duration_seconds}s")
        print(f"⚡ STREAMING MODE: Constant memory!")
        print()

        if self.blob_path.exists():
            self.blob_path.unlink()

        try:
            self.run_pipeline()
            self.save_manifest()
            return True
        except Exception as e:
            print(f"\n❌ Error: {e}")
            import traceback
            traceback.print_exc()
            return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Streaming encrypted video encoder")
    parser.add_argument('-o', '--output', default='output')
    parser.add_argument('-d', '--duration', type=int, default=10)
    parser.add_argument('-f', '--fragment-duration', type=int, default=2000)
    parser.add_argument('-p', '--pattern', default='smpte')
    parser.add_argument('--aac', action='store_true', help='Use AAC instead of Opus')
    parser.add_argument('--public-key', type=str, help='RSA public key for AES key encryption (PEM format)')

    args = parser.parse_args()

    encoder = StreamingFMP4Encoder(
        output_dir=args.output,
        duration_seconds=args.duration,
        fragment_duration_ms=args.fragment_duration,
        test_pattern=args.pattern,
        use_opus=not args.aac,
        public_key_path=args.public_key
    )

    success = encoder.run()
    exit(0 if success else 1)
