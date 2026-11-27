from itertools import compress

STENO_KEY_CHART = (
    ('^', '#', 'S-', 'T-', 'K-', 'P-'),
    ('W-', 'H-', 'R-', 'A-', 'O-', '*'),
    ('-E', '-U', '-F', '-R', '-P', '-B'),
    ('-L', '-G', '-T', '-S', '-D', '-Z'),
)

class Stroke:
    def __init__(self, keys):
        self.keys = keys

    def __repr__(self):
        return "Stroke([{0}])".format(", ".join(self.keys))

    @staticmethod
    def decode_records(data):
        """Decode complete 8-byte records; a bad record must not restart a job."""
        if len(data) % 8:
            raise ValueError("Incomplete stroke record")
        strokes = []
        skipped = 0
        for offset in range(0, len(data), 8):
            record = data[offset:offset + 8]
            if any(byte < 0xc0 for byte in record[:4]):
                skipped += 1
                continue
            strokes.append(Stroke.unpack(record))
        return strokes, skipped

    @staticmethod
    def unpack(stroke_data):
        keys = []
        for steno_byte, key_chart_row in zip(stroke_data, STENO_KEY_CHART):
            assert steno_byte >= 0b11000000
            # Only interested in right 6 values
            key_mask = [int(i) for i in bin(steno_byte)[-6:]]
            keys.extend(compress(key_chart_row, key_mask))
        return Stroke(keys)
