"""Pure fixed OAuth boundary checks. Never opens a socket."""
import sys,unittest
from pathlib import Path
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P))
from me3_openai import http as h

class Checks(unittest.TestCase):
    def test_fixed_endpoints_and_auth_domain_JWKS_only(self):
        self.assertEqual(h.trusted_url(h.DISCOVERY),h.DISCOVERY)
        self.assertEqual(h.trusted_url(h.auth.TOKEN_ENDPOINT),h.auth.TOKEN_ENDPOINT)
        self.assertEqual(h.trusted_url('https://auth.openai.com/jwks','jwks'),'https://auth.openai.com/jwks')
        for url in ('http://auth.openai.com/jwks','https://auth.openai.com:443/jwks','https://example.invalid/jwks','https://auth.openai.com/jwks?x=1','https://user@auth.openai.com/jwks'):
            with self.assertRaises(h.HTTPError):h.trusted_url(url,'jwks')
        with self.assertRaises(h.HTTPError):h.trusted_url('https://api.openai.com/v1/responses')
    def test_metadata_JSON_is_bounded_unique_finite_UTF8_object(self):
        self.assertEqual(h.decode_json(b'{"keys":[]}'),{'keys':[]})
        for value in (b'{"a":1,"a":2}',b'[]',b'{"a":NaN}',b'{"a":1e400}',b'{"a":"\\ud800"}',b'\xff',b' '* (h.LIMIT+1)):
            with self.assertRaises(h.HTTPError):h.decode_json(value)
    def test_runtime_module_has_no_Tk_or_setup_dependency(self):
        self.assertNotIn('tkinter',sys.modules)
        self.assertNotIn('setup211',sys.modules)

if __name__=='__main__':unittest.main(verbosity=2)
