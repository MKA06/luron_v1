import os
import json
import mimetypes
import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

# Use only stdlib for HTTP to avoid extra deps
import urllib.request
import urllib.parse
import uuid

from dotenv import load_dotenv
from supabase import create_client, Client
from openai import OpenAI


load_dotenv()

APYHUB_API_KEY = os.getenv("APYHUB_API_KEY", "")
APYHUB_BASE_URL = os.getenv("APYHUB_BASE_URL", "https://api.apyhub.com")

# Endpoint paths are configurable to let you adapt to ApyHub changes
# Per ApyHub docs, endpoints are:
#   - POST https://api.apyhub.com/ai/summarize-url
#   - POST https://api.apyhub.com/ai/summarize-documents/file
# You can override via env variables if needed.
APYHUB_SUMMARIZE_URL_ENDPOINT = os.getenv("APYHUB_SUMMARIZE_URL_ENDPOINT", "/ai/summarize-url")
APYHUB_SUMMARIZE_FILE_ENDPOINT = os.getenv("APYHUB_SUMMARIZE_FILE_ENDPOINT", "/ai/summarize-documents/file")
# Legacy: some deployments used `summary_type`; keep for backwards compat
APYHUB_SUMMARY_TYPE = os.getenv("APYHUB_SUMMARY_TYPE", "long")
# Preferred: ApyHub now expects `summary_length` (e.g., short|medium|long). Default to long.
APYHUB_SUMMARY_LENGTH = os.getenv("APYHUB_SUMMARY_LENGTH", "long")

# Supabase configuration for updating agent prompt
SUPABASE_URL = os.getenv('SUPABASE_URL', '')
SUPABASE_KEY = os.getenv('SUPABASE_KEY', '')
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None
openai_client: Optional[OpenAI] = None
try:
    openai_client = OpenAI()
except Exception:
    # If OPENAI_API_KEY isn't configured at import time, we'll fail lazily when used
    openai_client = None


class ApyHubClient:
    """Minimal ApyHub client for generating summaries by URL or file.

    Note: Endpoints/params are configurable via env vars to align with your ApyHub account.
    Required env:
      - APYHUB_API_KEY: your API key
    Optional env overrides:
      - APYHUB_BASE_URL (default: https://api.apyhub.com)
      - APYHUB_SUMMARIZE_URL_ENDPOINT (default: /ai/summarize-url)
      - APYHUB_SUMMARIZE_FILE_ENDPOINT (default: /generate/summary/file)
      - APYHUB_SUMMARY_TYPE (default: long)
    """

    def __init__(self,
                 api_key: Optional[str] = None,
                 base_url: Optional[str] = None):
        self.api_key = (api_key or APYHUB_API_KEY).strip()
        self.base_url = (base_url or APYHUB_BASE_URL).rstrip("/")
        if not self.api_key:
            raise ValueError("APYHUB_API_KEY is not set.")

    def _build_url(self, path: str) -> str:
        path = path if path.startswith("/") else f"/{path}"
        return f"{self.base_url}{path}"

    def _headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers = {
            "apy-token": self.api_key,
        }
        if extra:
            headers.update(extra)
        return headers

    def summarize_url(self, url: str, summary_type: str = APYHUB_SUMMARY_TYPE) -> Dict[str, Any]:
        """Summarize a webpage by URL using ApyHub.

        Returns parsed JSON on success; raises on HTTP errors.
        Expected ApyHub behavior: JSON with a summary field (schema may vary).
        """
        endpoint = self._build_url(APYHUB_SUMMARIZE_URL_ENDPOINT)
        # ApyHub's summarize-url endpoint expects just {"url": ...}
        payload = {"url": url}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=data,
            headers=self._headers({"Content-Type": "application/json"}),
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            resp_data = resp.read().decode("utf-8")
            try:
                return json.loads(resp_data)
            except json.JSONDecodeError:
                return {"raw": resp_data}

    def summarize_file(self, file_bytes: bytes, filename: str, content_type: Optional[str] = None,
                       summary_length: str = APYHUB_SUMMARY_LENGTH) -> Dict[str, Any]:
        """Summarize an uploaded file using ApyHub via multipart/form-data.

        Returns parsed JSON on success; raises on HTTP errors.
        """
        endpoint = self._build_url(APYHUB_SUMMARIZE_FILE_ENDPOINT)
        if not content_type:
            content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

        # Per ApyHub file summarize API, we can send summary_length with the file
        fields: Dict[str, Any] = {"summary_length": summary_length}
        files = {
            "file": (filename, file_bytes, content_type),
        }
        body, content_type_hdr = _encode_multipart_formdata(fields, files)
        req = urllib.request.Request(
            endpoint,
            data=body,
            headers=self._headers({"Content-Type": content_type_hdr}),
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            resp_data = resp.read().decode("utf-8")
            try:
                return json.loads(resp_data)
            except json.JSONDecodeError:
                return {"raw": resp_data}


def _encode_multipart_formdata(fields: Dict[str, Any],
                               files: Dict[str, Tuple[str, bytes, str]]) -> Tuple[bytes, str]:
    """Encode fields and files for multipart/form-data.

    fields: {name: value}
    files: {name: (filename, filebytes, content_type)}
    Returns: (body_bytes, content_type_header)
    """
    boundary = uuid.uuid4().hex
    boundary_bytes = boundary.encode()
    CRLF = b"\r\n"
    body = bytearray()

    for name, value in fields.items():
        body.extend(b"--" + boundary_bytes + CRLF)
        header = f'Content-Disposition: form-data; name="{name}"'.encode()
        body.extend(header + CRLF)
        body.extend(CRLF)
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        body.extend(str(value).encode())
        body.extend(CRLF)

    for name, (filename, filebytes, content_type) in files.items():
        body.extend(b"--" + boundary_bytes + CRLF)
        disp = f'Content-Disposition: form-data; name="{name}"; filename="{filename}"'.encode()
        body.extend(disp + CRLF)
        ctype = f"Content-Type: {content_type}".encode()
        body.extend(ctype + CRLF)
        body.extend(CRLF)
        body.extend(filebytes)
        body.extend(CRLF)

    body.extend(b"--" + boundary_bytes + b"--" + CRLF)
    content_type_header = f"multipart/form-data; boundary={boundary}"
    return bytes(body), content_type_header


ALLOWED_EXTS = {".pdf", ".docx"}


def is_allowed_file(filename: str) -> bool:
    lower = filename.lower()
    return any(lower.endswith(ext) for ext in ALLOWED_EXTS)


# --------------------
# Local URL fallback
# --------------------

class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._texts: List[str] = []
        self._ignore_stack: List[str] = []

    def handle_starttag(self, tag: str, attrs):
        if tag in ("script", "style", "noscript"):
            self._ignore_stack.append(tag)
        if tag in ("br", "p", "div", "section", "article", "li", "ul", "ol", "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "tr"):
            self._texts.append("\n")

    def handle_endtag(self, tag: str):
        if self._ignore_stack and self._ignore_stack[-1] == tag:
            self._ignore_stack.pop()
        if tag in ("p", "div", "section", "article", "li", "ul", "ol", "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table"):
            self._texts.append("\n")

    def handle_data(self, data: str):
        if not self._ignore_stack:
            # Skip very short noise
            if data and data.strip():
                self._texts.append(data)

    def text(self) -> str:
        raw = " ".join(self._texts)
        # Collapse whitespace and excessive newlines
        raw = re.sub(r"\s+", " ", raw)
        raw = re.sub(r"\n\s*\n+", "\n\n", raw)
        return raw.strip()


def _fetch_url_text(url: str) -> Tuple[str, Optional[str]]:
    """Fetch URL and extract visible text. Returns (text, error)."""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; KBFetcher/1.0)",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            content_type = resp.headers.get("Content-Type", "")
            charset = None
            # Attempt to parse charset
            if "charset=" in content_type:
                try:
                    charset = content_type.split("charset=")[-1].split(";")[0].strip()
                except Exception:
                    charset = None
            data = resp.read()
            html = data.decode(charset or "utf-8", errors="ignore")
            parser = _HTMLTextExtractor()
            parser.feed(html)
            text = parser.text()
            # Basic cleanup: limit to 50k chars to avoid sending huge prompts
            if len(text) > 50000:
                text = text[:50000]
            return text, None
    except Exception as e:
        return "", str(e)


def _fetch_url_text_with_variants(url: str) -> Tuple[str, Optional[str], str]:
    """Try fetching the URL; if it fails, try common variants (www/no-www, http/https).
    Returns (text, error, final_url).
    """
    text, err = _fetch_url_text(url)
    if text:
        return text, None, url

    # prepare variants
    try:
        parsed = urllib.parse.urlparse(url)
        candidates: List[str] = []
        # toggle www
        hostname = parsed.hostname or ""
        if hostname.startswith("www."):
            host2 = hostname[4:]
        else:
            host2 = f"www.{hostname}" if hostname else hostname
        if host2 and host2 != hostname:
            candidates.append(urllib.parse.urlunparse(parsed._replace(netloc=host2)))
        # toggle scheme
        if parsed.scheme == "https":
            candidates.append(urllib.parse.urlunparse(parsed._replace(scheme="http")))
        elif parsed.scheme == "http":
            candidates.append(urllib.parse.urlunparse(parsed._replace(scheme="https")))
        # If path empty, try adding trailing slash
        if not parsed.path:
            candidates.append(urllib.parse.urlunparse(parsed._replace(path="/")))

        for cand in candidates:
            t2, e2 = _fetch_url_text(cand)
            if t2:
                return t2, None, cand
        # If still failing, return last error
        return "", err or "fetch_failed", url
    except Exception:
        return "", err or "fetch_failed", url


def _summarize_text_openai(text: str, url: str, desired_length: Optional[str] = None) -> str:
    if not openai_client:
        raise ValueError("OpenAI client not configured; set OPENAI_API_KEY.")
    # Adjust length and token budget based on desired_length
    desired_length = (desired_length or APYHUB_SUMMARY_LENGTH or "long").lower()
    if desired_length not in ("short", "medium", "long"):
        desired_length = "long"
    if desired_length == "short":
        max_tokens_each, max_chunk = 250, 4000
    elif desired_length == "medium":
        max_tokens_each, max_chunk = 400, 5000
    else:
        max_tokens_each, max_chunk = 700, 6000
    # If too long, chunk and summarize progressively
    chunks = [text[i:i+max_chunk] for i in range(0, len(text), max_chunk)] if len(text) > max_chunk else [text]

    partial_summaries: List[str] = []
    for idx, chunk in enumerate(chunks[:5]):  # cap to 5 chunks to protect cost
        prompt = (
            f"You are a precise web page summarizer. Summarize the content from {url}. "
            "Return clear, scannable bullets covering: purpose, key services, pricing cues, contact/location, hours, audience, special offers. "
            f"Aim for a {desired_length} summary with specific details; omit boilerplate."
        )
        try:
            resp = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": chunk},
                ],
                temperature=0.2,
                max_tokens=max_tokens_each,
            )
            partial = (resp.choices[0].message.content or "").strip()
            if partial:
                partial_summaries.append(partial)
        except Exception:
            # If one chunk fails, continue with others
            continue

    if not partial_summaries:
        return ""

    if len(partial_summaries) == 1:
        return partial_summaries[0]

    try:
        combined = "\n".join(partial_summaries)
        final_prompt = (
            f"Consolidate the bullet points to a cohesive summary for {url}. "
            f"Remove duplicates and keep essentials. Keep it {desired_length}."
        )
        resp2 = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": final_prompt},
                {"role": "user", "content": combined},
            ],
            temperature=0.2,
            max_tokens=max_tokens_each,
        )
        return (resp2.choices[0].message.content or "").strip()
    except Exception:
        return "\n".join(partial_summaries)


async def summarize_inputs_multiple(urls: Optional[List[str]], files: Optional[List[Any]], *, summary_length: Optional[str] = None) -> Dict[str, Any]:
    """Summarize a list of URLs via OpenAI and/or a list of uploaded files via ApyHub.

    - urls: optional list of strings
    - files: optional list of Starlette/FastAPI UploadFile or file-like with .filename/.read()

    URL flow: fetch page → extract text → summarize with OpenAI.
    File flow: send the file to ApyHub and extract summary from response.

    Returns a dict with individual results and a combined long summary string in `summary`.
    """
    results: Dict[str, Any] = {"links": [], "files": [], "summary": ""}

    def _extract_summary(obj: Any) -> str:
        if obj is None:
            return ""
        if isinstance(obj, str):
            return obj
        if isinstance(obj, dict):
            # Common ApyHub variants
            for key in ("summary", "data", "result"):
                val = obj.get(key)
                if isinstance(val, str) and val.strip():
                    return val
                if isinstance(val, dict):
                    for k2 in ("summary", "result", "text"):
                        v2 = val.get(k2)
                        if isinstance(v2, str) and v2.strip():
                            return v2
            try:
                return json.dumps(obj)
            except Exception:
                return str(obj)
        try:
            return json.dumps(obj)
        except Exception:
            return str(obj)

    link_summaries: List[str] = []
    if urls:
        for url in urls:
            entry: Dict[str, Any] = {"url": url}
            try:
                fetched_text, fetch_err, final_url = _fetch_url_text_with_variants(url)
                if fetch_err:
                    entry.setdefault("fetch", {})["error"] = fetch_err
                if fetched_text:
                    summary_text = _summarize_text_openai(fetched_text, final_url, desired_length=summary_length)
                    entry["summary"] = summary_text
                    if final_url != url:
                        entry["resolved_url"] = final_url
                    if summary_text:
                        link_summaries.append(f"- {final_url}: {summary_text}")
                else:
                    entry.setdefault("fetch", {})["error"] = entry["fetch"].get("error") or "no_text_extracted"
            except Exception as e:
                entry["error"] = str(e)
            results["links"].append(entry)

    file_summaries: List[str] = []
    if files:
        client: Optional[ApyHubClient] = None
        for f in files:
            name = getattr(f, "filename", "uploaded")
            entry: Dict[str, Any] = {"name": name}
            if not name or not is_allowed_file(name):
                entry["error"] = "Unsupported file type. Only .pdf and .docx allowed."
                results["files"].append(entry)
                continue
            try:
                # If f is a FastAPI UploadFile, .read may be async
                if hasattr(f, "read"):
                    try:
                        file_bytes = await f.read()  # type: ignore
                    except TypeError:
                        file_bytes = f.read()  # type: ignore
                else:
                    file_bytes = f.file.read()  # type: ignore

                content_type = getattr(f, "content_type", None)
                if client is None:
                    client = ApyHubClient()
                resp = client.summarize_file(
                    file_bytes,
                    name,
                    content_type=content_type,
                    summary_length=(summary_length or APYHUB_SUMMARY_LENGTH or APYHUB_SUMMARY_TYPE),
                )
                text = _extract_summary(resp)
                entry["summary"] = text
                if text:
                    file_summaries.append(f"- {name}: {text}")
            except Exception as e:
                entry["error"] = str(e)
            results["files"].append(entry)

    sections: List[str] = []
    if link_summaries:
        sections.append("Web Summaries:\n" + "\n".join(link_summaries))
    if file_summaries:
        sections.append("File Summaries:\n" + "\n".join(file_summaries))
    results["summary"] = "\n\n".join(sections) if sections else ""
    return results


async def summarize_and_update_agent(agent_id: str, urls: Optional[List[str]], files: Optional[List[Any]], *, summary_length: Optional[str] = None) -> Dict[str, Any]:
    """Summarize provided inputs and append combined summary to the agent's prompt.

    Returns JSON containing the combined `summary` and the updated `prompt`.
    """
    if not supabase:
        raise ValueError("Supabase is not configured. Missing SUPABASE_URL or SUPABASE_KEY.")

    res = await summarize_inputs_multiple(urls, files, summary_length=summary_length)
    combined_summary = res.get("summary") or ""

    # Fetch existing prompt
    agent = supabase.table('agents').select('prompt').eq('id', agent_id).single().execute()
    existing_prompt = (agent.data or {}).get('prompt') if agent and getattr(agent, 'data', None) else None

    divider = "\n\n---\nContext Summary (auto-generated):\n"
    if existing_prompt:
        new_prompt = f"{existing_prompt}{divider}{combined_summary}" if combined_summary else existing_prompt
    else:
        new_prompt = combined_summary

    if new_prompt is not None:
        supabase.table('agents').update({'prompt': new_prompt}).eq('id', agent_id).execute()

    return {
        "agent_id": agent_id,
        "summary": combined_summary,
        "prompt": new_prompt,
        "details": res,
    }
