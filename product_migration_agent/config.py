"""Static configuration for the Product Master migration agent."""
import os
import tempfile
from pathlib import Path

KEY_FIELD = "MATNR"
# SAP key structure that the Oracle->S4 mapping file does not carry.
TABLE_KEYS = {"MARA": ["MATNR"], "MAKT": ["MATNR", "SPRAS"],
              "MARC": ["MATNR", "WERKS"], "MBEW": ["MATNR", "BWKEY"]}
STATIC_FIELDS = {"MAKT": {"SPRAS": os.environ.get("MIGRATION_DEFAULT_LANGUAGE", "E")}}
DERIVED_FIELDS = {"MBEW": {"BWKEY": "WERKS"}}          # key field copied from another field
TRUNCATE_FIELDS = {"MAKTX"}                            # cut to SAP length (logged) instead of raising an error

# AI decisions below this confidence are NOT applied; they are reported for a human instead.
DEFAULT_MIN_CONF = float(os.environ.get("MIGRATION_MIN_CONFIDENCE", "0.7"))
MAX_UPLOAD_BYTES = int(os.environ.get("MIGRATION_MAX_UPLOAD_MB", "20")) * 1024 * 1024
MAX_ROWS = int(os.environ.get("MIGRATION_MAX_ROWS", "200000"))
CACHE_FILE = Path(os.environ.get("MIGRATION_CACHE_DIR", Path(tempfile.gettempdir()) / "product_migration_cache")) / "cache.json"
