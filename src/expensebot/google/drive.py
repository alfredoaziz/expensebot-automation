"""Uploads original receipt files (photo/PDF) to a Google Drive folder,
organized as <folder>/<year>/<month>/."""

from __future__ import annotations

import io

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload


class DriveRepository:
    def __init__(self, credentials: Credentials, root_folder_id: str) -> None:
        self._service = build("drive", "v3", credentials=credentials)
        self._root_folder_id = root_folder_id
        self._folder_cache: dict[str, str] = {}

    def upload_receipt(
        self, file_bytes: bytes, filename: str, mime_type: str, year: int, month: int
    ) -> str:
        folder_id = self._ensure_month_folder(year, month)
        media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=mime_type, resumable=False)
        metadata = {"name": filename, "parents": [folder_id]}
        created = (
            self._service.files()
            .create(body=metadata, media_body=media, fields="id, webViewLink")
            .execute()
        )
        return created["webViewLink"]

    def _ensure_month_folder(self, year: int, month: int) -> str:
        year_folder = self._ensure_child_folder(self._root_folder_id, str(year))
        return self._ensure_child_folder(year_folder, f"{month:02d}")

    def _ensure_child_folder(self, parent_id: str, name: str) -> str:
        cache_key = f"{parent_id}/{name}"
        if cache_key in self._folder_cache:
            return self._folder_cache[cache_key]

        query = (
            f"'{parent_id}' in parents and name = '{name}' "
            "and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        )
        results = self._service.files().list(q=query, fields="files(id, name)").execute()
        files = results.get("files", [])
        if files:
            folder_id = files[0]["id"]
        else:
            metadata = {
                "name": name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [parent_id],
            }
            folder_id = self._service.files().create(body=metadata, fields="id").execute()["id"]

        self._folder_cache[cache_key] = folder_id
        return folder_id
