import base64
import hashlib
import importlib.util
import os
from pathlib import Path
import unittest


source = Path(os.getenv('PIPE_SOURCE', '/tmp/forestry_runtime_pipe.py'))
spec = importlib.util.spec_from_file_location('forestry_pipe_test', source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PipeUploadTests(unittest.TestCase):
    def test_image_over_30mb_is_decoded_without_corruption(self):
        data = b'0123456789abcdef' * (2 * 1024 * 1024)  # 32 MiB
        url = 'data:image/tiff;base64,' + base64.b64encode(data).decode()
        stream, digest, mime, suffix = module.decode_image_data_url(url, 512 * 1048576)
        with stream:
            self.assertEqual(hashlib.sha256(stream.read()).hexdigest(), hashlib.sha256(data).hexdigest())
        self.assertEqual(digest, hashlib.sha256(data).hexdigest())
        self.assertEqual((mime, suffix), ('image/tiff', '.tif'))

    def test_exact_size_limit_and_invalid_encoding(self):
        for size, valid in [(1048576, True), (1048577, False)]:
            url = 'data:image/png;base64,' + base64.b64encode(b'x' * size).decode()
            if valid:
                stream, *_ = module.decode_image_data_url(url, 1048576)
                stream.close()
            else:
                with self.assertRaises(ValueError):
                    module.decode_image_data_url(url, 1048576)
        with self.assertRaises(ValueError):
            module.decode_image_data_url('data:image/png;base64,!!!!', 1048576)


if __name__ == '__main__':
    unittest.main()
