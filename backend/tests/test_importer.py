from io import BytesIO
from zipfile import ZipFile

import pytest

from app.services.importer import ImportErrorDetail


def make_package(files: dict[str, str]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as zip_file:
        for name, content in files.items():
            zip_file.writestr(name, content)
    return buffer.getvalue()


def test_package_missing_metadata_is_rejected():
    from app.services.importer import import_problem_package

    with pytest.raises(ImportErrorDetail):
        import_problem_package(None, make_package({"statement.md": "x"}))  # type: ignore[arg-type]

