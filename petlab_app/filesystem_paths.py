"""Windows I/O spelling only; logical artifact identities remain unchanged."""
import os
from pathlib import Path


def native_directory(root, longest_relative):
    value=os.path.abspath(os.fspath(root))
    if (os.name=='nt' and not value.startswith('\\\\?\\')
            # atomic_write_json uses .<basename>.<8 random chars>.tmp beside
            # the final file. Its temporary spelling needs 14 more characters.
            and len(os.path.join(value,longest_relative).encode('utf-16-le'))//2+14>=260):
        value=('\\\\?\\UNC\\'+value[2:] if value.startswith('\\\\') else '\\\\?\\'+value)
    return Path(value)
