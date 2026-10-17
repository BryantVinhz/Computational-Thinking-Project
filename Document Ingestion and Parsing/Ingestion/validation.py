import os
from pathlib import Path
from typing import Optional
from schema_ingestion import FileFormat


class FileValidator:
    """Nhận diện định dạng và kiểm tra tính hợp lệ của tệp đầu vào."""

    EXTENSION_MAP = {
        ".pdf": FileFormat.PDF,
        ".md": FileFormat.MARKDOWN,
        ".markdown": FileFormat.MARKDOWN,
        ".csv": FileFormat.CSV,
        ".json": FileFormat.JSON,
        ".txt": FileFormat.TXT,
    }

    @classmethod
    def detect_format(cls, file_path: Path) -> FileFormat:
        ext = file_path.suffix.lower()
        return cls.EXTENSION_MAP.get(ext, FileFormat.UNKNOWN)

    @classmethod
    def validate(cls, file_path: Path) -> tuple[bool, FileFormat, Optional[str]]:
        """
        Kiểm tra tệp đầu vào.
        Trả về: (is_valid, file_format, error_message)
        """
        # 1. Kiểm tra tồn tại
        if not file_path.exists():
            return False, FileFormat.UNKNOWN, "File does not exist."
        if not file_path.is_file():
            return False, FileFormat.UNKNOWN, "Path is not a regular file."

        # 2. Kiểm tra quyền đọc
        if not os.access(file_path, os.R_OK):
            return False, FileFormat.UNKNOWN, "Permission denied: File is not readable."

        # 3. Kiểm tra định dạng hỗ trợ
        file_format = cls.detect_format(file_path)
        if file_format == FileFormat.UNKNOWN:
            return (
                False,
                FileFormat.UNKNOWN,
                f"Unsupported file format '{file_path.suffix}'.",
            )

        # 4. Kiểm tra file rỗng
        if file_path.stat().st_size == 0:
            return False, file_format, "File is empty (0 bytes)."

        # 5. Kiểm tra khả năng đọc và chữ ký tệp cơ bản
        try:
            if file_format == FileFormat.PDF:
                with open(file_path, "rb") as f:
                    if f.read(5) != b"%PDF-":
                        return False, file_format, "Invalid or corrupted PDF header."
            else:
                with open(file_path, "r", encoding="utf-8") as f:
                    if not f.read(1024).strip():
                        return False, file_format, "File contains only whitespace."
        except UnicodeDecodeError:
            return False, file_format, "Encoding error: File is not valid UTF-8."
        except Exception as e:
            return False, file_format, f"Read error: {str(e)}"

        return True, file_format, None