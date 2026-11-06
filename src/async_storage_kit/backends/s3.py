"""Optional, async S3-compatible backend (AWS S3 and compatible endpoints).

No bucket creation, public ACL, public URL or implicit credentials by default.
All no-clobber writes use S3 conditional requests, including multipart completion.
"""

from __future__ import annotations

import inspect
import mimetypes
from collections.abc import AsyncIterator, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, NoReturn
from urllib.parse import quote, urlsplit

from .._async import wait_for_completion
from ..base import StorageBackend
from ..exceptions import (
    FileAlreadyExists,
    FileNotFound,
    StorageError,
    UnsupportedOperation,
)
from ..streams import iter_content
from ..types import ConflictPolicy, Content, FileMetadata
from ..utils import normalize_name

_MIN_PART_SIZE = 5 * 1024 * 1024
_MAX_PARTS = 10_000
_MISSING_CODES = frozenset({"404", "NoSuchKey", "NotFound"})
_CONFLICT_CODES = frozenset(
    {"412", "PreconditionFailed", "ConditionalRequestConflict", "409"}
)
_ALLOWED_OBJECT_PARAMETERS = frozenset(
    {
        "CacheControl",
        "ContentDisposition",
        "ContentEncoding",
        "ContentLanguage",
        "ContentType",
        "Metadata",
        "ServerSideEncryption",
        "SSEKMSKeyId",
        "BucketKeyEnabled",
        "StorageClass",
        "Tagging",
        "ChecksumAlgorithm",
    }
)


@dataclass(frozen=True, slots=True)
class S3FileMetadata(FileMetadata):
    """Additional S3 information; the ETag is *not* necessarily an MD5 checksum."""

    etag: str | None = None
    content_type: str | None = None
    user_metadata: Mapping[str, str] = field(default_factory=dict)


def _error_code(exc: BaseException) -> str | None:
    """Inspect botocore ClientError without importing optional dependencies eagerly."""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        error = response.get("Error")
        if isinstance(error, dict):
            return str(error.get("Code")) if error.get("Code") is not None else None
    return None


def _raise_s3_error(exc: Exception, operation: str, name: str) -> NoReturn:
    code = _error_code(exc)
    if code in _MISSING_CODES:
        raise FileNotFound(name) from exc
    if code in _CONFLICT_CODES:
        raise FileAlreadyExists(f"Conditional S3 write failed for {name!r}.") from exc
    # Never mask a 403 as a missing key: without permissions, its existence is unknown.
    raise StorageError(
        f"S3 {operation} failed for {name!r} ({code or type(exc).__name__})."
    ) from exc


class S3Storage(StorageBackend):
    """S3-compatible storage; requires ``pip install async-storage-kit[s3]``.

    Only uploads that fit in one memory-limited part are sent via PutObject.
    Larger streams use sequential multipart uploads and conditional completion.
    Conditional completion must be supported by the chosen S3-compatible server;
    no check-then-write fallback is used to fake atomic no-clobber semantics.

    ``session`` is an advanced injection point for an aioboto3-compatible Session.
    """

    def __init__(
        self,
        bucket_name: str,
        *,
        location: str = "",
        region_name: str | None = None,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        session_token: str | None = None,
        use_ssl: bool = True,
        verify_ssl: bool = True,
        addressing_style: str = "auto",
        custom_domain: str | None = None,
        public_urls: bool = False,
        object_parameters: Mapping[str, Any] | None = None,
        multipart_threshold: int = 8 * 1024 * 1024,
        multipart_chunksize: int = 8 * 1024 * 1024,
        session: Any | None = None,
    ) -> None:
        if not bucket_name or not isinstance(bucket_name, str):
            raise ValueError("bucket_name must be a non-empty string.")
        if bool(access_key) != bool(secret_key):
            raise ValueError("access_key and secret_key must be supplied together.")
        if session_token and not access_key:
            raise ValueError(
                "session_token requires explicit access_key and secret_key."
            )
        if addressing_style not in {"auto", "path", "virtual"}:
            raise ValueError("addressing_style must be 'auto', 'path' or 'virtual'.")
        if (
            type(multipart_chunksize) is not int
            or not _MIN_PART_SIZE <= multipart_chunksize <= 5 * 1024**3
        ):
            raise ValueError("multipart_chunksize must be between 5 MiB and 5 GiB.")
        if (
            type(multipart_threshold) is not int
            or not _MIN_PART_SIZE <= multipart_threshold <= 5 * 1024**3
        ):
            raise ValueError("multipart_threshold must be between 5 MiB and 5 GiB.")
        if endpoint_url is not None:
            endpoint = urlsplit(endpoint_url)
            if (
                endpoint.scheme not in {"http", "https"}
                or not endpoint.hostname
                or endpoint.username
                or endpoint.password
                or endpoint.query
                or endpoint.fragment
            ):
                raise ValueError(
                    "endpoint_url must be an HTTP(S) URL without credentials, query or fragment."
                )
        if custom_domain is not None:
            parsed = urlsplit(
                custom_domain if "://" in custom_domain else "https://" + custom_domain
            )
            if (
                parsed.scheme not in {"https", "http"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    "custom_domain must be a valid HTTP(S) hostname or base URL."
                )
            custom_domain = parsed.geturl().rstrip("/")
        invalid_params = set(object_parameters or {}) - _ALLOWED_OBJECT_PARAMETERS
        if invalid_params:
            raise ValueError(
                f"Unsupported S3 object parameters: {sorted(invalid_params)!r}."
            )
        self.bucket_name = bucket_name
        self.location = normalize_name(location) if location else ""
        self.region_name = region_name
        self.endpoint_url = endpoint_url
        self.access_key = access_key
        self.secret_key = secret_key
        self.session_token = session_token
        self.use_ssl = use_ssl
        self.verify_ssl = verify_ssl
        self.addressing_style = addressing_style
        self.custom_domain = custom_domain
        self.public_urls = public_urls
        self.object_parameters = dict(object_parameters or {})
        self.multipart_threshold = multipart_threshold
        self.multipart_chunksize = multipart_chunksize
        self._session = session  # Lazily construct SDK only when a request is made.

    def _client(self) -> Any:
        if self._session is None:
            try:
                import aioboto3
            except ImportError as exc:
                raise ImportError(
                    "S3Storage requires aioboto3. Install 'async-storage-kit[s3]'."
                ) from exc
            self._session = aioboto3.Session()
        try:
            from botocore.config import Config
        except ImportError as exc:
            raise ImportError(
                "S3Storage requires botocore via 'async-storage-kit[s3]'."
            ) from exc
        options: dict[str, Any] = {
            "use_ssl": self.use_ssl,
            "verify": self.verify_ssl,
            "config": Config(
                signature_version="s3v4", s3={"addressing_style": self.addressing_style}
            ),
        }
        if self.region_name is not None:
            options["region_name"] = self.region_name
        if self.endpoint_url is not None:
            options["endpoint_url"] = self.endpoint_url
        if self.access_key is not None:
            options.update(
                aws_access_key_id=self.access_key, aws_secret_access_key=self.secret_key
            )
        if self.session_token is not None:
            options["aws_session_token"] = self.session_token
        return self._session.client("s3", **options)

    def _key(self, name: str) -> str:
        key = normalize_name(name)
        return f"{self.location}/{key}" if self.location else key

    def _prefix(self, path: str) -> str:
        path = normalize_name(path, allow_empty=True)
        prefix = "/".join(part for part in (self.location, path) if part)
        return f"{prefix}/" if prefix else ""

    def _upload_parameters(self, name: str) -> dict[str, Any]:
        result = dict(self.object_parameters)
        content_type, _ = mimetypes.guess_type(name)
        if content_type:
            result.setdefault("ContentType", content_type)
        return result

    async def bucket_exists(self) -> bool:
        async with self._client() as client:
            try:
                await client.head_bucket(Bucket=self.bucket_name)
                return True
            except Exception as exc:
                if _error_code(exc) in {"404", "NoSuchBucket", "NotFound"}:
                    return False
                _raise_s3_error(exc, "head_bucket", self.bucket_name)

    async def create_bucket(self) -> None:
        """Explicit provisioning only; ordinary file operations never create buckets."""
        params: dict[str, Any] = {"Bucket": self.bucket_name}
        if self.region_name and self.region_name != "us-east-1":
            params["CreateBucketConfiguration"] = {
                "LocationConstraint": self.region_name
            }
        async with self._client() as client:
            try:
                await client.create_bucket(**params)
            except Exception as exc:
                _raise_s3_error(exc, "create_bucket", self.bucket_name)

    async def _save(
        self,
        name: str,
        content: Content,
        *,
        max_length: int | None,
        on_conflict: ConflictPolicy,
    ) -> str:
        candidate = name
        # Resolve usual collisions before consuming the one-shot content stream.
        if on_conflict == "rename":
            for _ in range(100):
                if not await self.exists(candidate):
                    break
                candidate = self.get_alternative_name(name, max_length)
            else:
                raise FileAlreadyExists("Could not allocate an available S3 key.")

        key = self._key(candidate)
        conditional = {"IfNoneMatch": "*"} if on_conflict != "overwrite" else {}
        params = self._upload_parameters(candidate)
        parts: list[dict[str, Any]] = []
        upload_id: str | None = None
        buffer = bytearray()
        async with self._client() as client:
            try:
                async for chunk in iter_content(content):
                    buffer.extend(chunk)
                    if upload_id is None and len(buffer) < self.multipart_threshold:
                        continue
                    if upload_id is None:
                        result = await client.create_multipart_upload(
                            Bucket=self.bucket_name, Key=key, **params
                        )
                        upload_id = str(result["UploadId"])
                    while len(buffer) >= self.multipart_chunksize:
                        if len(parts) >= _MAX_PARTS:
                            raise StorageError(
                                "S3 multipart upload exceeds 10,000 parts."
                            )
                        part = bytes(buffer[: self.multipart_chunksize])
                        del buffer[: self.multipart_chunksize]
                        result = await client.upload_part(
                            Bucket=self.bucket_name,
                            Key=key,
                            UploadId=upload_id,
                            PartNumber=len(parts) + 1,
                            Body=part,
                        )
                        parts.append(
                            {"PartNumber": len(parts) + 1, "ETag": result["ETag"]}
                        )
                if upload_id is None:
                    await client.put_object(
                        Bucket=self.bucket_name,
                        Key=key,
                        Body=bytes(buffer),
                        **params,
                        **conditional,
                    )
                else:
                    if buffer:  # Only the last part may be smaller than 5 MiB.
                        if len(parts) >= _MAX_PARTS:
                            raise StorageError(
                                "S3 multipart upload exceeds 10,000 parts."
                            )
                        result = await client.upload_part(
                            Bucket=self.bucket_name,
                            Key=key,
                            UploadId=upload_id,
                            PartNumber=len(parts) + 1,
                            Body=bytes(buffer),
                        )
                        parts.append(
                            {"PartNumber": len(parts) + 1, "ETag": result["ETag"]}
                        )
                    await client.complete_multipart_upload(
                        Bucket=self.bucket_name,
                        Key=key,
                        UploadId=upload_id,
                        MultipartUpload={"Parts": parts},
                        **conditional,
                    )
                    upload_id = None
            except BaseException as exc:
                if upload_id is not None:
                    # Never replace the original upload error with an abort error.
                    with suppress(Exception):
                        await wait_for_completion(
                            client.abort_multipart_upload(
                                Bucket=self.bucket_name, Key=key, UploadId=upload_id
                            )
                        )
                if isinstance(exc, Exception) and (
                    _error_code(exc) is not None
                    or type(exc).__module__.startswith(("botocore.", "aiobotocore."))
                ):
                    _raise_s3_error(exc, "save", candidate)
                raise
        return candidate

    async def iter_chunks(
        self, name: str, chunk_size: int = 256 * 1024
    ) -> AsyncIterator[bytes]:
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive.")
        key = self._key(name)
        async with self._client() as client:
            try:
                response = await client.get_object(Bucket=self.bucket_name, Key=key)
            except Exception as exc:
                _raise_s3_error(exc, "get_object", name)
            body = response["Body"]
            try:
                while True:
                    try:
                        data = await body.read(chunk_size)
                    except Exception as exc:
                        _raise_s3_error(exc, "stream read", name)
                    if not data:
                        break
                    yield bytes(data)
            finally:
                result = body.close()
                if inspect.isawaitable(result):
                    await result

    async def exists(self, name: str) -> bool:
        key = self._key(name)
        async with self._client() as client:
            try:
                await client.head_object(Bucket=self.bucket_name, Key=key)
                return True
            except Exception as exc:
                if _error_code(exc) in _MISSING_CODES:
                    return False
                _raise_s3_error(exc, "head_object", name)

    async def delete(self, name: str, raise_on_missing: bool = False) -> bool:
        name = normalize_name(name)
        # S3 delete_object is idempotent and normally doesn't report missing keys.
        # This is inherently check-then-delete; concurrent replacement can race.
        if not await self.exists(name):
            if raise_on_missing:
                raise FileNotFound(name)
            return False
        async with self._client() as client:
            try:
                await client.delete_object(Bucket=self.bucket_name, Key=self._key(name))
            except Exception as exc:
                _raise_s3_error(exc, "delete_object", name)
        return True

    async def listdir(self, path: str = "") -> tuple[list[str], list[str]]:
        prefix = self._prefix(path)
        directories: set[str] = set()
        files: set[str] = set()
        token: str | None = None
        async with self._client() as client:
            while True:
                kwargs: dict[str, Any] = {
                    "Bucket": self.bucket_name,
                    "Prefix": prefix,
                    "Delimiter": "/",
                }
                if token is not None:
                    kwargs["ContinuationToken"] = token
                try:
                    page = await client.list_objects_v2(**kwargs)
                except Exception as exc:
                    _raise_s3_error(exc, "list_objects_v2", path)
                for item in page.get("CommonPrefixes", []):
                    child = str(item["Prefix"])[len(prefix) :].rstrip("/")
                    if child:
                        directories.add(child)
                for item in page.get("Contents", []):
                    child = str(item["Key"])[len(prefix) :]
                    if child and "/" not in child:
                        files.add(child)
                if not page.get("IsTruncated"):
                    break
                token = page.get("NextContinuationToken")
                if not token or token == kwargs.get("ContinuationToken"):
                    raise StorageError(
                        "S3 returned a truncated listing without a new continuation token."
                    )
        return sorted(directories), sorted(files)

    async def metadata(self, name: str) -> S3FileMetadata:
        name = normalize_name(name)
        async with self._client() as client:
            try:
                info = await client.head_object(
                    Bucket=self.bucket_name, Key=self._key(name)
                )
            except Exception as exc:
                _raise_s3_error(exc, "head_object", name)
        modified = info["LastModified"]
        if not isinstance(modified, datetime):
            raise StorageError("S3 returned an invalid LastModified timestamp.")
        if modified.tzinfo is None:
            modified = modified.replace(tzinfo=timezone.utc)
        return S3FileMetadata(
            name=name,
            size=int(info["ContentLength"]),
            modified_at=modified.astimezone(timezone.utc),
            etag=info.get("ETag"),
            content_type=info.get("ContentType"),
            user_metadata=dict(info.get("Metadata", {})),
        )

    async def size(self, name: str) -> int:
        return (await self.metadata(name)).size

    async def get_modified_time(self, name: str) -> datetime:
        return (await self.metadata(name)).modified_at

    def url(self, name: str) -> str:
        if not self.public_urls or not self.custom_domain:
            raise UnsupportedOperation(
                "Public S3 URLs require both public_urls=True and custom_domain. "
                "Use await get_presigned_url() for private objects."
            )
        suffix = "/".join(
            quote(segment, safe="") for segment in self._key(name).split("/")
        )
        return f"{self.custom_domain}/{suffix}"

    async def get_presigned_url(self, name: str, expire: int = 3600) -> str:
        if not isinstance(expire, int) or not 1 <= expire <= 604_800:
            raise ValueError("expire must be between 1 and 604800 seconds.")
        key = self._key(name)
        async with self._client() as client:
            try:
                url = client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": self.bucket_name, "Key": key},
                    ExpiresIn=expire,
                )
                return await url if inspect.isawaitable(url) else url
            except Exception as exc:
                _raise_s3_error(exc, "generate_presigned_url", name)
