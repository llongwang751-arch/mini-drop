"""用导入 gzip 夹具验证：S3 校验和必须基于实际存储字节。"""

import gzip
import hashlib
import io
import unittest
from unittest.mock import Mock

from maintenance import S3Storage
from urllib3.response import HTTPResponse


class StoredBytesTests(unittest.TestCase):
    def test_content_encoding_is_not_transparently_decoded(self):
        stored = gzip.compress(b"imported-compressed-fixture;leaf 23\n", mtime=0)
        response = HTTPResponse(
            body=io.BytesIO(stored),
            preload_content=False,
            headers={"Content-Encoding": "gzip"},
        )
        response.close = Mock(wraps=response.close)
        response.release_conn = Mock(wraps=response.release_conn)
        client = Mock()
        client.get_object.return_value = response
        actual = b"".join(S3Storage(client, "imported-fixture").chunks("raw/fixture"))
        self.assertEqual(actual, stored)
        self.assertEqual(
            hashlib.sha256(actual).hexdigest(), hashlib.sha256(stored).hexdigest()
        )
        response.close.assert_called_once()
        response.release_conn.assert_called_once()

    def test_early_consumer_exit_closes_the_response(self):
        response = HTTPResponse(
            body=io.BytesIO(b"x" * (1024 * 1024 + 1)), preload_content=False
        )
        response.close = Mock(wraps=response.close)
        response.release_conn = Mock(wraps=response.release_conn)
        client = Mock()
        client.get_object.return_value = response
        chunks = S3Storage(client, "imported-fixture").chunks("raw/fixture")
        next(chunks)
        chunks.close()
        response.close.assert_called_once()
        response.release_conn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
