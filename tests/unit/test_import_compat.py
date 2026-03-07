import unittest

import cli
import engine
import privacy_guard
import server

try:
    import vector_db
    VECTOR_DB_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - environment dependent
    vector_db = None
    VECTOR_DB_IMPORT_ERROR = exc

from agensic.engine import RequestContext as PackageRequestContext
from agensic.engine import SuggestionEngine as PackageSuggestionEngine
from agensic.privacy import PrivacyGuard as PackagePrivacyGuard
try:
    from agensic.vector_db import CommandVectorDB as PackageCommandVectorDB
except Exception:  # pragma: no cover - environment dependent
    PackageCommandVectorDB = None


class ImportCompatTests(unittest.TestCase):
    def test_engine_symbols_are_exported(self):
        self.assertIs(engine.RequestContext, PackageRequestContext)
        self.assertIs(engine.SuggestionEngine, PackageSuggestionEngine)

    def test_privacy_symbol_is_exported(self):
        self.assertIs(privacy_guard.PrivacyGuard, PackagePrivacyGuard)

    def test_vector_symbol_is_exported(self):
        if vector_db is None or PackageCommandVectorDB is None:
            self.skipTest(f"Vector DB import unavailable: {VECTOR_DB_IMPORT_ERROR}")
        self.assertIs(vector_db.CommandVectorDB, PackageCommandVectorDB)

    def test_cli_and_server_entrypoints_exist(self):
        self.assertTrue(hasattr(cli, "app"))
        self.assertTrue(callable(server.run))


if __name__ == "__main__":
    unittest.main()
