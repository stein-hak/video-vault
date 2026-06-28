#!/usr/bin/env python3
"""
Streaming MP4 Box Parser

Парсит MP4 boxes инкрементально, не накапливая весь файл в памяти.
Возвращает готовые сегменты (moof+mdat) как только они полностью получены.
"""

import struct
from typing import Optional, Tuple, Callable
from io import BytesIO
from pymp4.parser import Box


class StreamingMP4Parser:
    """Streaming parser для fMP4 файлов"""

    def __init__(self):
        # Буфер для накопления данных текущего box
        self.buffer = BytesIO()

        # Состояние парсера
        self.current_box_type = None
        self.current_box_size = None
        self.bytes_needed = 8  # Начинаем с чтения header

        # Накопленные boxes для init segment
        self.ftyp_box = None
        self.moov_box = None
        self.init_sent = False

        # Текущий media segment (moof + mdat)
        self.current_moof = None

        # Timescale для конвертации длительности
        self.video_timescale = 30000  # Default, will be extracted from moov

        # Статистика
        self.segment_count = 0

    def feed(self, data: bytes, on_init: Callable[[bytes], None],
             on_segment: Callable[[bytes], None]):
        """
        Подать данные в парсер

        Args:
            data: Очередной chunk данных от GStreamer
            on_init: Callback для init segment (ftyp + moov)
            on_segment: Callback для media segment (moof + mdat)
        """
        self.buffer.write(data)

        # Обрабатываем накопленные данные
        while True:
            # Проверяем достаточно ли данных
            available = self.buffer.tell()

            if available < self.bytes_needed:
                # Недостаточно данных - ждем еще
                break

            # Читаем с начала буфера
            self.buffer.seek(0)

            if self.current_box_type is None:
                # Читаем header нового box
                header = self.buffer.read(8)

                if len(header) < 8:
                    # Восстанавливаем позицию
                    self.buffer.seek(0, 2)
                    break

                size = struct.unpack('>I', header[:4])[0]
                box_type = header[4:8].decode('ascii', errors='ignore')

                # TODO: Handle extended size (size == 1)
                if size == 1:
                    # Extended size - нужно еще 8 байт
                    extended = self.buffer.read(8)
                    if len(extended) < 8:
                        # Не хватает данных
                        self.buffer.seek(0, 2)
                        break
                    size = struct.unpack('>Q', extended)[0]
                    header = header + extended

                self.current_box_type = box_type
                self.current_box_size = size
                self.bytes_needed = size

                # Сохраняем header
                remaining = self.buffer.read()
                self.buffer = BytesIO()
                self.buffer.write(header)
                self.buffer.write(remaining)
                self.buffer.seek(0, 2)

            else:
                # У нас уже есть header, читаем весь box
                self.buffer.seek(0)
                box_data = self.buffer.read(self.current_box_size)

                if len(box_data) < self.current_box_size:
                    # Еще не весь box получен
                    self.buffer.seek(0, 2)
                    break

                # Обрабатываем полученный box
                self._process_box(self.current_box_type, box_data,
                                 on_init, on_segment)

                # Очищаем буфер от обработанного box
                remaining = self.buffer.read()
                self.buffer = BytesIO()
                self.buffer.write(remaining)
                self.buffer.seek(0, 2)

                # Сбрасываем состояние
                self.current_box_type = None
                self.current_box_size = None
                self.bytes_needed = 8

    def _process_box(self, box_type: str, box_data: bytes,
                     on_init: Callable, on_segment: Callable):
        """Обработать полностью полученный box"""

        if box_type == 'ftyp':
            self.ftyp_box = box_data
            print(f"[Parser] ftyp received: {len(box_data)} bytes")

        elif box_type == 'moov':
            self.moov_box = box_data

            # Извлекаем timescale из moov
            self.video_timescale = self._extract_timescale_from_moov(box_data)
            print(f"[Parser] moov received: {len(box_data)} bytes, video_timescale={self.video_timescale}")

            # Отправляем init segment (ftyp + moov)
            if self.ftyp_box and not self.init_sent:
                init_segment = self.ftyp_box + self.moov_box
                print(f"[Parser] ✓ Init segment ready: {len(init_segment)} bytes")
                on_init(init_segment)
                self.init_sent = True

        elif box_type == 'moof':
            self.current_moof = box_data
            # Парсим длительность из moof
            self.current_moof_duration = self._parse_duration_from_moof(box_data)
            print(f"[Parser] moof received: {len(box_data)} bytes, duration: {self.current_moof_duration:.4f}s")

        elif box_type == 'mdat':
            print(f"[Parser] mdat received: {len(box_data)} bytes")

            # Отправляем media segment (moof + mdat)
            if self.current_moof:
                segment = self.current_moof + box_data
                duration = self.current_moof_duration if hasattr(self, 'current_moof_duration') else 0.0
                print(f"[Parser] ✓ Media segment {self.segment_count} ready: {len(segment)} bytes, duration={duration:.4f}s")
                on_segment(segment, duration)
                self.segment_count += 1
                self.current_moof = None
            else:
                print(f"[Parser] ⚠️  mdat without moof!")

        else:
            print(f"[Parser] Unknown box: {box_type} ({len(box_data)} bytes)")

    def get_buffer_size(self) -> int:
        """Текущий размер буфера (для отладки)"""
        return self.buffer.tell()

    def _extract_timescale_from_moov(self, moov_data: bytes) -> int:
        """
        Извлечь video timescale из moov/trak/mdia/mdhd используя pymp4
        """
        try:
            moov = Box.parse(moov_data)

            # Ищем video track
            for trak in moov.children:
                if trak.type != b'trak':
                    continue

                # Проверяем что это video track
                for child in trak.children:
                    if child.type == b'mdia':
                        # Проверяем handler type
                        for mdia_child in child.children:
                            if mdia_child.type == b'hdlr':
                                handler_type = mdia_child.handler_type
                                if handler_type == b'vide':
                                    # Это video track - извлекаем timescale из mdhd
                                    for mdia_box in child.children:
                                        if mdia_box.type == b'mdhd':
                                            return mdia_box.timescale

            return 30000  # Default fallback

        except Exception as e:
            print(f"[Parser] Warning: Failed to extract timescale: {e}")
            return 30000


    def _parse_duration_from_moof(self, moof_data: bytes) -> float:
        """
        Извлечь длительность сегмента из moof box используя pymp4

        Берем длительность ТОЛЬКО из video traf (track_ID=1 обычно)
        """
        try:
            moof = Box.parse(moof_data)

            # Ищем video traf (track_ID=1)
            for traf in moof.children:
                if traf.type != b'traf':
                    continue

                # Проверяем track_ID из tfhd
                track_id = None
                for box in traf.children:
                    if box.type == b'tfhd':
                        track_id = box.track_ID
                        break

                # Track ID 1 обычно video
                if track_id == 1:
                    # Ищем trun box
                    for box in traf.children:
                        if box.type == b'trun':
                            # Суммируем длительности всех samples
                            total_duration = 0
                            if hasattr(box, 'sample_info') and box.sample_info:
                                for sample in box.sample_info:
                                    total_duration += sample.sample_duration

                            # Конвертируем в секунды
                            return total_duration / float(self.video_timescale)

            # Нет video traf - это audio-only сегмент, возвращаем 0
            return 0.0

        except Exception as e:
            print(f"[Parser] Warning: Failed to parse duration from moof: {e}")
            import traceback
            traceback.print_exc()
            return 0.0



if __name__ == "__main__":
    # Test
    parser = StreamingMP4Parser()

    def on_init(data):
        print(f"GOT INIT: {len(data)} bytes")

    def on_segment(data):
        print(f"GOT SEGMENT: {len(data)} bytes")

    # Simulate streaming data
    test_data = b'\x00\x00\x00\x20ftyp' + b'x' * 24  # ftyp box
    parser.feed(test_data[:10], on_init, on_segment)  # Partial
    parser.feed(test_data[10:], on_init, on_segment)  # Rest
