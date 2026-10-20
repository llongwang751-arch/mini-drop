import importlib.util
from pathlib import Path
import tempfile
import unittest
spec=importlib.util.spec_from_file_location("public_address",Path(__file__).resolve().parents[1]/"tls/public-address.py")
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
class PublicAddressTests(unittest.TestCase):
 def test_valid_target_is_rendered_as_canonical_redirect(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);(root/"runtime").mkdir();(root/"tls").mkdir()
   (root/"tls/edge-tls.conf").write_text("return 308 https://120.24.187.205$request_uri;")
   (root/"runtime/public-ip").write_text("82.157.81.79\n")
   self.assertEqual(module.prepare(root),"82.157.81.79")
   self.assertEqual((root/"runtime/edge-tls.template.conf").read_text(),"return 308 https://82.157.81.79$request_uri;")
 def test_invalid_targets_do_not_overwrite_existing_config(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);(root/"runtime").mkdir();(root/"tls").mkdir()
   (root/"tls/edge-tls.conf").write_text("legacy")
   destination=root/"runtime/edge-tls.template.conf";destination.write_text("keep")
   for value in ("127.0.0.1","192.168.1.1","82.157.81.79;id","example.com","2001:4860:4860::8888"):
    with self.assertRaises(ValueError):module.prepare(root,value)
    self.assertEqual(destination.read_text(),"keep")
if __name__=="__main__":unittest.main()
