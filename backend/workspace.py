"""Validation and storage definitions for shared documents and agenda items."""

import mimetypes
from datetime import date, timedelta
from pathlib import PurePosixPath
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator


WORKSPACE_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS document_categories (
        category_id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(100) NOT NULL UNIQUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS ideas (
        idea_id INT AUTO_INCREMENT PRIMARY KEY,
        category VARCHAR(100) NOT NULL DEFAULT 'General',
        title VARCHAR(200) NOT NULL,
        description TEXT NULL,
        idea_date DATE NOT NULL,
        status VARCHAR(30) NOT NULL DEFAULT 'Proposed',
        priority VARCHAR(20) NOT NULL DEFAULT 'Normal',
        expected_benefit TEXT NULL,
        next_steps TEXT NULL,
        created_by_user_id INT NULL,
        created_by_username VARCHAR(50) NOT NULL,
        updated_by_username VARCHAR(50) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        FOREIGN KEY (created_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL,
        INDEX idx_ideas_date_status (idea_date, status)
    )""",
    """CREATE TABLE IF NOT EXISTS shared_documents (
        document_id INT AUTO_INCREMENT PRIMARY KEY,
        title VARCHAR(200) NOT NULL,
        category VARCHAR(100) NOT NULL DEFAULT 'General',
        document_date DATE NOT NULL,
        description TEXT NULL,
        original_filename VARCHAR(255) NOT NULL,
        file_type VARCHAR(30) NOT NULL,
        content_type VARCHAR(100) NOT NULL,
        file_size INT NOT NULL,
        file_data LONGBLOB NOT NULL,
        version INT NOT NULL DEFAULT 1,
        created_by_user_id INT NULL,
        created_by_username VARCHAR(50) NOT NULL,
        updated_by_username VARCHAR(50) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        FOREIGN KEY (created_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL,
        INDEX idx_documents_date (document_date, document_id)
    )""",
    """CREATE TABLE IF NOT EXISTS agenda_items (
        agenda_id INT AUTO_INCREMENT PRIMARY KEY,
        title VARCHAR(200) NOT NULL,
        description TEXT NULL,
        due_date DATE NOT NULL,
        status VARCHAR(30) NOT NULL DEFAULT 'Open',
        priority VARCHAR(20) NOT NULL DEFAULT 'Normal',
        progress INT NOT NULL DEFAULT 0,
        tracking_notes TEXT NULL,
        created_by_user_id INT NULL,
        created_by_username VARCHAR(50) NOT NULL,
        updated_by_username VARCHAR(50) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        FOREIGN KEY (created_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL,
        INDEX idx_agenda_due_status (due_date, status)
    )""",
)

FILE_GROUPS = {
    "PDF": {".pdf"},
    "Spreadsheet": {".xls", ".xlsx", ".xlsm", ".csv", ".ods"},
    "Document": {".doc", ".docx", ".odt", ".rtf"},
    "Archive": {".zip", ".7z", ".rar"},
    "Image": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff", ".bmp"},
    "Presentation": {".ppt", ".pptx", ".odp"},
    "Text": {".txt"},
}
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024


class DocumentMetadata(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    category: str = Field(default="General", min_length=1, max_length=100)
    document_date: date
    description: str = Field(default="", max_length=5000)

    @field_validator("category")
    @classmethod
    def clean_category(cls, value):
        if not value.strip() or "|" in value:
            raise ValueError("Enter a category without the | character")
        return value.strip()

    @field_validator("title")
    @classmethod
    def clean_title(cls, value):
        if not value.strip():
            raise ValueError("Enter a title")
        return value.strip()


class DocumentCategoryInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        if not value.strip() or "|" in value:
            raise ValueError("Enter a category without the | character")
        return value.strip()


class IdeaItem(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    category: str = Field(default="General", min_length=1, max_length=100)
    description: str = Field(default="", max_length=5000)
    idea_date: date
    status: Literal["Proposed", "Under review", "Accepted", "Implemented", "Not pursued"] = "Proposed"
    priority: Literal["Low", "Normal", "High", "Urgent"] = "Normal"
    expected_benefit: str = Field(default="", max_length=5000)
    next_steps: str = Field(default="", max_length=5000)

    @field_validator("category")
    @classmethod
    def clean_category(cls, value):
        if not value.strip() or "|" in value:
            raise ValueError("Enter a category without the | character")
        return value.strip()

    @field_validator("title")
    @classmethod
    def clean_title(cls, value):
        if not value.strip():
            raise ValueError("Enter an idea title")
        return value.strip()


class AgendaItem(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    due_date: date
    status: Literal["Open", "In progress", "Completed", "Cancelled"] = "Open"
    priority: Literal["Low", "Normal", "High", "Urgent"] = "Normal"
    progress: int = Field(default=0, ge=0, le=100)
    tracking_notes: str = Field(default="", max_length=5000)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value):
        if not value.strip():
            raise ValueError("Enter an agenda item")
        return value.strip()

    @model_validator(mode="after")
    def check_progress(self):
        if self.status == "Completed":
            self.progress = 100
        elif self.status == "Open" and self.progress:
            raise ValueError("Select In progress when work has started")
        elif self.status == "In progress" and self.progress == 100:
            raise ValueError("Select Completed for 100% progress")
        return self


def workspace_owner(user, item):
    return user["department"] == "Admin" or item["created_by_user_id"] == user["user_id"]


def require_workspace_owner(user, item):
    if not workspace_owner(user, item):
        raise HTTPException(status_code=403, detail="Only the creator or Admin can change this entry")


def document_file_details(filename):
    clean = PurePosixPath(filename.replace("\\", "/")).name
    if not clean or len(clean) > 255 or any(ord(char) < 32 for char in clean):
        raise HTTPException(status_code=400, detail="Invalid document filename")
    suffix = PurePosixPath(clean).suffix.lower()
    group = next((name for name, extensions in FILE_GROUPS.items() if suffix in extensions), None)
    if not group:
        raise HTTPException(status_code=415, detail="Unsupported document type")
    return clean, group, mimetypes.guess_type(clean)[0] or "application/octet-stream"


async def read_document_upload(request):
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > MAX_DOCUMENT_BYTES:
            raise HTTPException(status_code=413, detail="The maximum document size is 20 MB")
        content.extend(chunk)
    if not content:
        raise HTTPException(status_code=400, detail="Choose a non-empty document file")
    return bytes(content)


def agenda_timing(item, today=None):
    today = today or date.today()
    if item["status"] in {"Completed", "Cancelled"}:
        return item["status"]
    if item["due_date"] < today:
        return "Overdue"
    if item["due_date"] <= today + timedelta(days=7):
        return "Due soon"
    return "On track"
