import tempfile
from pathlib import Path
import settings_store

# This creates a temporary test file, not your real settings.
with tempfile.TemporaryDirectory() as folder:
    test_file = Path(folder) / "settings.json"

    settings_store.set_pin("123456", test_file)
    print("PIN exists before damage:", settings_store.pin_is_set(test_file))

    test_file.write_text("damaged settings", encoding="utf-8")
    print("PIN exists after damage: ", settings_store.pin_is_set(test_file))